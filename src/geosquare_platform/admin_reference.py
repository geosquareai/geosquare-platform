"""Cell to admin reference table (roadmap L17).

One row per (cell, admin unit) pair:

- gid: the GeoSquare cell at the chosen level (default 11, 100 m).
- village_code, district_code, regency_code, province_code: admin codes of the unit.
- area_weight: overlap area of the unit with the cell, divided by the cell area.

Spec: for each gid, the sum of ``area_weight`` is the share of the cell covered by
admin units. It is at most 1, and exactly 1 when the cell is fully covered. It is
below 1 for cells on the edge of the covered area. Units must not overlap: the
builder checks that the sum of unit areas equals the area of their union.

A user joins a cell table on ``gid`` and groups by any admin code. A village's
weights, times the cell area, add up to its polygon area.

Input: WGS 84 lon/lat polygons only. Other coordinates are rejected.

Overlap areas come from ``geosquare_v2.crosswalk.polygon_crosswalk_rows``, the same
builder as the crosswalk tool. Each unit is cut into tiles first, because the
builder refuses polygons with more than 100 000 candidate cells.

Usage:
    python -m geosquare_platform.admin_reference SOURCE.parquet OUT.parquet \\
        --source-name "BIG village boundaries" --license "CC-BY 4.0" \\
        [--village-column kode_desa] [--district-column kode_kec] \\
        [--regency-column kode_kota] [--province-column kode_prov] [--level 11]
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import geosquare_engine as gs
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from geosquare_v2 import GeosquareGrid
from geosquare_v2.crs import wgs84_to_grid_transformer
from geosquare_v2.crosswalk import polygon_crosswalk_rows
from shapely import box, wkb
from shapely.ops import transform, unary_union

from .registry import load_registry

FORMAT_VERSION = "1.0"
DOMAIN_CODE = "ID"
DEFAULT_LEVEL = 11  # K14 in plan/04-risiko-dan-keputusan.md: 11 (100 m), same as the Jakarta Barat crosswalk
# shortcut: 200 by 200 cells per tile keeps each polyfill under geosquare_v2's 100 000 candidate limit. Upgrade if that limit changes.
TILE_CELLS = 200
# Relative area error allowed per admin unit (same tolerance as polygon_crosswalk_rows).
AREA_TOLERANCE = 1e-6
# Allowed sum of weights per cell above 1. Only float rounding is allowed.
OVERLAP_TOLERANCE = 1e-9
# Allowed overlap between units, as a share of their total area. Catches overlaps inside one cell.
OVERLAP_AREA_TOLERANCE = 1e-9
CODE_COLUMNS = ("village_code", "district_code", "regency_code", "province_code")


def _code(value, name: str) -> str | None:
    """Return an admin code as text. Whole-number floats become integers, so 3171.0 reads as 3171."""
    if value is None:
        return None
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{name} {value!r} is not a whole number")
        value = int(value)
    return str(value).strip() or None


def _polygonal(geom):
    """Keep only the polygon parts of an intersection. Return None when nothing has area."""
    parts = [part for part in getattr(geom, "geoms", [geom]) if part.geom_type in ("Polygon", "MultiPolygon")]
    if not parts:
        return None
    piece = unary_union(parts) if len(parts) > 1 else parts[0]
    return piece if piece.area > 0 else None


def _unit_overlaps(unit_id: str, geom, grid, level: int, side_m: float) -> dict[str, float]:
    """Return overlap area in m² per cell for one admin unit, tile by tile."""
    step = side_m * TILE_CELLS
    minx, miny, maxx, maxy = geom.bounds
    overlaps: dict[str, float] = {}
    x = minx
    while x < maxx:
        y = miny
        while y < maxy:
            piece = _polygonal(geom.intersection(box(x, y, x + step, y + step)))
            if piece is not None:
                for row in polygon_crosswalk_rows([(unit_id, piece)], grid, level):
                    overlaps[row["gid"]] = overlaps.get(row["gid"], 0.0) + row["bobot"] * piece.area
            y += step
        x += step
    return overlaps


def build_reference(
    source: Path,
    out: Path,
    *,
    level: int = DEFAULT_LEVEL,
    geometry_column: str = "geom",
    admin_columns: dict[str, str],
    source_name: str,
    license_name: str,
) -> dict:
    """Write the reference table to ``out`` and return the checks it passed.

    ``admin_columns`` maps ``village_code``, ``district_code``, ``regency_code`` and
    ``province_code`` to column names in ``source``. Missing keys stay empty.
    The finest code given is the unit id, and unit ids must be unique.
    If a check fails, ``out`` is removed.
    """
    if not any(admin_columns.get(key) for key in CODE_COLUMNS[:3]):
        raise ValueError("give at least one of village, district or regency column")
    if not source_name.strip() or not license_name.strip():
        raise ValueError("source_name and license_name are required; never leave them empty")
    table = pq.read_table(source)
    needed = [geometry_column, *[col for col in admin_columns.values() if col]]
    missing = [name for name in needed if name not in table.column_names]
    if missing:
        raise ValueError(f"source has no columns {missing}")

    profile = load_registry().get(DOMAIN_CODE)
    grid = GeosquareGrid(profile)
    to_grid = wgs84_to_grid_transformer(profile)
    side_m = gs.cell_side_m(level)
    cell_area = side_m * side_m
    schema = pa.schema(
        [("gid", pa.string()), *[(key, pa.string()) for key in CODE_COLUMNS], ("area_weight", pa.float64())]
    ).with_metadata(
        {
            "format": "geosquare admin reference",
            "format_version": FORMAT_VERSION,
            "level": str(level),
            "cell_side_m": str(side_m),
            "domain_code": DOMAIN_CODE,
            "weight": "overlap area / cell area; sum per gid is at most 1",
            "source_name": source_name,
            "source_file": Path(source).name,
            "license": license_name,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )

    seen_ids: set[str] = set()
    geoms: list = []
    rows_written = 0
    max_area_error = 0.0
    out.parent.mkdir(parents=True, exist_ok=True)
    writer = pq.ParquetWriter(out, schema)
    try:
        for number, record in enumerate(table.to_pylist(), start=1):
            codes = {key: _code(record[col], key) if col else None for key, col in admin_columns.items()}
            if codes.get("province_code") is None and codes.get("regency_code"):
                codes["province_code"] = codes["regency_code"][:2]  # Indonesian rule: province is the first two digits of the regency code
            unit_id = next((codes[key] for key in CODE_COLUMNS[:3] if codes.get(key)), None)
            if unit_id is None or unit_id in seen_ids:
                raise ValueError(f"row {number}: admin code is empty or repeated: {unit_id!r}")
            seen_ids.add(unit_id)

            raw = wkb.loads(record[geometry_column])
            if raw.is_empty:
                raise ValueError(f"unit {unit_id}: geometry is empty; fix the source")
            minx, miny, maxx, maxy = raw.bounds
            if not (-180 <= minx and maxx <= 180 and -90 <= miny and maxy <= 90):
                raise ValueError(
                    f"unit {unit_id}: coordinates are outside WGS 84 lon/lat (x in -180..180, y in -90..90); "
                    "reproject the source to WGS 84 (EPSG:4326) first"
                )
            geom = transform(to_grid.transform, raw)
            if not geom.is_valid:
                raise ValueError(f"unit {unit_id}: geometry is invalid; fix the source")
            geoms.append(geom)
            overlaps = _unit_overlaps(unit_id, geom, grid, level, side_m)
            area_error = abs(sum(overlaps.values()) - geom.area) / geom.area
            max_area_error = max(max_area_error, area_error)
            if area_error > AREA_TOLERANCE:
                raise ValueError(f"unit {unit_id}: cell areas differ from the polygon by {area_error:.2e}")
            if not overlaps:
                continue

            gids = list(overlaps)
            batch = pa.table(
                {
                    "gid": gids,
                    **{key: [codes.get(key)] * len(gids) for key in CODE_COLUMNS},
                    "area_weight": [overlaps[gid] / cell_area for gid in gids],
                },
                schema=schema,
            )
            writer.write_table(batch)
            rows_written += len(gids)
    except Exception:
        writer.close()
        out.unlink(missing_ok=True)
        raise
    writer.close()

    try:
        if rows_written == 0:
            raise ValueError("no admin unit produced cells")
        # Overlap check: units must not share area. Shared edges leave only float error.
        total_area = sum(geom.area for geom in geoms)
        overlap_area = total_area - unary_union(geoms).area
        if overlap_area > OVERLAP_AREA_TOLERANCE * total_area:
            raise ValueError(f"admin units overlap by {overlap_area:.3f} m2; units must not share area")
        per_cell = pq.read_table(out, columns=["gid", "area_weight"]).group_by("gid").aggregate(
            [("area_weight", "sum")]
        ).column("area_weight_sum")
        max_cell_sum = pc.max(per_cell).as_py()
        if max_cell_sum > 1 + OVERLAP_TOLERANCE:
            raise ValueError(f"admin units overlap: a cell has weight sum {max_cell_sum:.12f}")
    except Exception:
        out.unlink(missing_ok=True)
        raise

    cells_partial = pc.sum(pc.less(per_cell, 1 - 1e-6)).as_py()
    return {
        "units": len(seen_ids),
        "rows": rows_written,
        "cells": len(per_cell),
        "cells_inside": len(per_cell) - cells_partial,
        "cells_partial": cells_partial,
        "max_area_error": max_area_error,
        "max_cell_weight_sum": max_cell_sum,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="parquet file with WKB polygons in WGS 84")
    parser.add_argument("out", type=Path, help="output parquet file")
    parser.add_argument("--geometry-column", default="geom")
    parser.add_argument("--village-column")
    parser.add_argument("--district-column")
    parser.add_argument("--regency-column")
    parser.add_argument("--province-column")
    parser.add_argument("--level", type=int, default=DEFAULT_LEVEL, help="GID level (11 = 100 m, 12 = 50 m)")
    parser.add_argument("--source-name", required=True, help="boundary source, written to the metadata")
    parser.add_argument("--license", dest="license_name", required=True, help="licence of the boundary data")
    args = parser.parse_args(argv)
    admin = {
        "village_code": args.village_column,
        "district_code": args.district_column,
        "regency_code": args.regency_column,
        "province_code": args.province_column,
    }
    report = build_reference(
        args.source,
        args.out,
        level=args.level,
        geometry_column=args.geometry_column,
        admin_columns=admin,
        source_name=args.source_name,
        license_name=args.license_name,
    )
    for key, value in report.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
