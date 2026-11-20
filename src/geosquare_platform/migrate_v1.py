"""Convert synthetic or external V1 cell CSVs into V2 datasets.

This command deliberately converts only the file supplied by the caller. It does not
scan, select, or migrate any team dataset.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import shutil
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from geosquare_v2.errors import GeosquareError
from geosquare_v2.geometry import profile_grid_crs, projected_cell_geometry
from geosquare_v2.grid import GeosquareGrid
from geosquare_v2.codec import side_cell_count
from geosquare_v2.migration import LegacyV1Decoder, V1MigrationAdapter

from .app import apply_crosswalk_data, data_root
from .cellstore import DATASET_ID_PATTERN, REGISTRY_VERSION
from .crosswalk import CrosswalkRejected, crosswalk_csv, validate_crosswalk
from .importer import ImportRejected, read_csv
from .registry import load_registry
from .cellstore import parse_value_fields


TARGET_SEMANTICS = frozenset(("COUNT", "TOTAL"))
METHOD = "v1_cell_area_crosswalk"
UNALLOCATED_EPSILON = 1e-6


class MigrationRejected(ValueError):
    """The input cannot be converted without risking a wrong result."""


def _positive_threshold(text: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a finite number >= 0") from exc
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("must be a finite number >= 0")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m geosquare_platform.migrate_v1",
        description="Convert a supplied V1 GID CSV to a V2 cell dataset; team data is never scanned or migrated.",
    )
    parser.add_argument("--input", required=True, type=Path, help="input CSV")
    parser.add_argument("--domain", required=True, help="target registry domain, for example ID")
    parser.add_argument("--level", required=True, type=int, choices=range(0, 15), help="target V2 level (0-14)")
    parser.add_argument("--name", required=True, help="new V2 dataset name, for example ID-L9-0123456789ab")
    parser.add_argument("--value-fields", required=True, help="JSON array in the POST /datasets format")
    parser.add_argument("--gid-column", default="gid_v1", help="V1 GID column (default: gid_v1)")
    parser.add_argument(
        "--max-unallocated",
        type=_positive_threshold,
        default=1e-6,
        help="maximum allowed uncovered source-cell ratio (default: 1e-6)",
    )
    return parser


def _parse_fields(raw_json: str):
    try:
        raw = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise MigrationRejected("--value-fields is not valid JSON") from exc
    if not isinstance(raw, list):
        raise MigrationRejected("--value-fields must be a JSON array")
    try:
        fields = parse_value_fields(raw)
    except ImportRejected as exc:
        raise MigrationRejected(str(exc)) from exc
    for field in fields:
        if field.semantics not in TARGET_SEMANTICS:
            raise MigrationRejected(
                f"value field {field.name!r} has semantics {field.semantics!r}; "
                "v1 migration accepts only COUNT or TOTAL"
            )
        if field.logical_type != "numeric":
            raise MigrationRejected(f"value field {field.name!r} must use logical_type numeric")
    return fields


def _read_input(path: Path, gid_column: str, fields) -> tuple[bytes, dict[str, list[str | None]], list[str]]:
    try:
        data = path.read_bytes()
        columns = read_csv(data)
    except (OSError, ImportRejected) as exc:
        raise MigrationRejected(f"cannot read input CSV: {exc}") from exc
    if gid_column not in columns:
        raise MigrationRejected(f"input CSV has no GID column {gid_column!r}")
    for field in fields:
        if field.name not in columns:
            raise MigrationRejected(f"value column {field.name!r} is not in the input CSV")
    gids: list[str] = []
    for row, raw_gid in enumerate(columns[gid_column], start=2):
        if not raw_gid:
            raise MigrationRejected(f"row {row}, column {gid_column!r}: v1 GID is empty")
        gids.append(raw_gid)
    distinct = list(dict.fromkeys(gids))
    return data, columns, distinct


def _source_csv(columns: dict[str, list[str | None]], gid_column: str, fields) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    names = ["sumber_id", *(field.name for field in fields)]
    writer.writerow(names)
    for row in range(len(columns[gid_column])):
        values = [columns[gid_column][row] or ""]
        values.extend(columns[field.name][row] or "" for field in fields)
        writer.writerow(values)
    return output.getvalue().encode("utf-8")


def _crosswalk_name(domain: str, level: int, input_sha256: str) -> str:
    # The platform's current storage and crosswalk contracts use the ID name pattern.
    return f"{domain}-L{level}-{input_sha256[:12]}"


def _migration_report(
    *,
    input_path: Path,
    input_data: bytes,
    row_count: int,
    gids: list[str],
    domain: str,
    level: int,
    max_unallocated: float,
    profile_version: str,
    per_source_u: dict[str, float],
) -> dict[str, Any]:
    return {
        "source_file_name": input_path.name,
        "input_sha256": hashlib.sha256(input_data).hexdigest(),
        "input_row_count": row_count,
        "distinct_v1_gid_count": len(gids),
        "v1_reference": "geosquare-grid==1.1.1 (verified by tests/test_v1_parity.py)",
        "geosquare_v2_version": version("geosquare-v2"),
        "profile_version": profile_version,
        "registry_version": REGISTRY_VERSION,
        "domain": domain,
        "level": level,
        "method": METHOD,
        "max_unallocated": max_unallocated,
        "per_source_u": per_source_u,
        "sources_with_unallocated": [
            gid for gid, unallocated in per_source_u.items() if unallocated > UNALLOCATED_EPSILON
        ],
    }


def _projected_source_area(source_bounds: tuple[float, float, float, float], profile) -> float:
    """Measure a V1 box after the same CRS transform used by V2 polyfill."""
    try:
        from pyproj import CRS, Transformer
        from shapely.geometry import box
        from shapely.ops import transform

        transformer = Transformer.from_crs(
            CRS.from_user_input("OGC:CRS84"),
            CRS.from_user_input(profile_grid_crs(profile)),
            always_xy=True,
        )
        projected = transform(transformer.transform, box(*source_bounds))
        area = float(projected.area)
    except Exception as exc:
        raise MigrationRejected(f"could not measure the V1 source box in the profile grid CRS: {exc}") from exc
    if not math.isfinite(area) or area <= 0:
        raise MigrationRejected(f"V1 source box has no positive finite area in the profile grid CRS: {area!r}")
    return area


def _target_cell_area(grid: GeosquareGrid, records, level: int) -> float:
    """Measure one returned target cell in the profile's authoritative grid CRS."""
    target_gid = records[0].target_uri.rsplit(":", 1)[-1]
    target_side = grid.profile.root_side_m / side_cell_count(level)
    target_area = target_side * target_side
    try:
        returned_area = float(projected_cell_geometry(grid, target_gid).area)
    except Exception as exc:
        raise MigrationRejected(f"could not measure target cell {target_gid!r}: {exc}") from exc
    if not math.isclose(target_area, returned_area, rel_tol=1e-12, abs_tol=1e-6):
        raise MigrationRejected(
            f"target cell area mismatch for {target_gid!r}: A_t={target_area!r}, "
            f"returned geometry area={returned_area!r}"
        )
    return target_area


def convert_v1(
    *,
    input_path: Path,
    domain: str,
    level: int,
    name: str,
    value_fields_json: str,
    gid_column: str = "gid_v1",
    max_unallocated: float = 1e-6,
) -> dict[str, Any]:
    """Convert one CSV and return the stored dataset summary plus its report."""
    if not domain:
        raise MigrationRejected("--domain is required and cannot be empty")
    if not gid_column:
        raise MigrationRejected("--gid-column cannot be empty")
    if level < 0 or level > 14:
        raise MigrationRejected("target level must be an integer from 0 to 14")
    if not math.isfinite(max_unallocated) or max_unallocated < 0:
        raise MigrationRejected("--max-unallocated must be a finite number >= 0")
    if not DATASET_ID_PATTERN.fullmatch(name):
        raise MigrationRejected("--name must match the dataset ID pattern, for example ID-L9-0123456789ab")

    fields = _parse_fields(value_fields_json)
    input_data, columns, gids = _read_input(input_path, gid_column, fields)
    decoder = LegacyV1Decoder()
    for gid in gids:
        try:
            decoded = decoder.decode(gid)
        except Exception as exc:
            raise MigrationRejected(f"invalid v1 GID {gid!r}: {exc}") from exc
        if decoded.level == 15:
            raise MigrationRejected(f"v1 GID {gid!r} is at level 15; level 15 migration is refused")

    destination = data_root() / name
    if destination.exists():
        raise MigrationRejected(f"dataset {name!r} already exists; refusing to overwrite")

    try:
        registry = load_registry()
        profile = registry.get(domain)
    except Exception as exc:
        raise MigrationRejected(f"registry could not load domain {domain!r}: {exc}") from exc
    if domain != "ID":
        raise MigrationRejected("the platform storage contract currently supports domain ID only")

    grid = GeosquareGrid(profile)
    adapter = V1MigrationAdapter(grid, decoder=decoder)
    crosswalk_rows: list[tuple[str, str, float]] = []
    per_source_u: dict[str, float] = {}
    for gid in gids:
        try:
            records = adapter.migrate_area(gid, target_level=level, min_coverage=0.0)
        except Exception as exc:
            raise MigrationRejected(f"area overlap failed for v1 GID {gid!r}: {exc}") from exc
        if not records:
            raise MigrationRejected(f"area overlap returned no cells for v1 GID {gid!r}")
        source_area = _projected_source_area(decoder.decode(gid).bounds, profile)
        target_area = _target_cell_area(grid, records, level)
        raw_total = sum(float(record.coverage_ratio or 0.0) for record in records)
        if not math.isfinite(raw_total) or raw_total <= 0:
            raise MigrationRejected(f"area overlap returned no positive coverage for v1 GID {gid!r}")
        unallocated = 1.0 - (raw_total * target_area) / source_area
        per_source_u[gid] = unallocated
        if unallocated < -UNALLOCATED_EPSILON:
            raise MigrationRejected(
                f"unallocated coverage is negative for source {gid!r}: u={unallocated:.12g}; "
                "the source and target area measurements are inconsistent"
            )
        if unallocated > max_unallocated:
            offenders = [
                f"{source} u={value:.12g}"
                for source, value in per_source_u.items()
                if value > max_unallocated
            ]
            raise MigrationRejected(
                f"unallocated coverage exceeds --max-unallocated for sources: {', '.join(offenders)}"
            )
        for record in records:
            ratio = float(record.coverage_ratio or 0.0)
            if ratio > 0:
                target_gid = record.target_uri.rsplit(":", 1)[-1]
                crosswalk_rows.append((gid, target_gid, ratio / raw_total))

    crosswalk_data = crosswalk_csv(
        validate_crosswalk(
            _crosswalk_bytes(crosswalk_rows),
            domain_code=domain,
            level=level,
            weight_kind="area",
        )
    )
    crosswalk_name = _crosswalk_name(domain, level, hashlib.sha256(input_data).hexdigest())
    crosswalk_path = data_root() / "crosswalks" / crosswalk_name
    if crosswalk_path.exists():
        raise MigrationRejected(f"crosswalk {crosswalk_name!r} already exists; refusing to overwrite")
    crosswalk_path.mkdir(parents=True, exist_ok=False)
    dataset_path = data_root() / name
    try:
        (crosswalk_path / "crosswalk.csv").write_bytes(crosswalk_data)
        crosswalk_manifest = validate_crosswalk(
            crosswalk_data, domain_code=domain, level=level, weight_kind="area"
        ).manifest
        (crosswalk_path / "manifest.json").write_text(
            json.dumps(crosswalk_manifest, indent=2) + "\n", encoding="utf-8"
        )

        result = apply_crosswalk_data(
            validate_crosswalk(crosswalk_data, domain_code=domain, level=level, weight_kind="area"),
            _source_csv(columns, gid_column, fields),
            dataset_id=name,
            value_columns=[field.name for field in fields],
            field_semantics={field.name: field.semantics for field in fields},
        )
        report = _migration_report(
            input_path=input_path,
            input_data=input_data,
            row_count=len(columns[gid_column]),
            gids=gids,
            domain=domain,
            level=level,
            max_unallocated=max_unallocated,
            profile_version=str(getattr(profile, "profile_version", "")),
            per_source_u=per_source_u,
        )
        (dataset_path / "migration_report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
    except Exception:
        shutil.rmtree(crosswalk_path, ignore_errors=True)
        shutil.rmtree(dataset_path, ignore_errors=True)
        raise
    return {**result, "migration_report": report, "crosswalk": crosswalk_name}


def _crosswalk_bytes(rows: list[tuple[str, str, float]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("sumber_id", "gid", "bobot"))
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = convert_v1(
            input_path=args.input,
            domain=args.domain,
            level=args.level,
            name=args.name,
            value_fields_json=args.value_fields,
            gid_column=args.gid_column,
            max_unallocated=args.max_unallocated,
        )
    except (CrosswalkRejected, HTTPException, MigrationRejected, GeosquareError, OSError, ValueError) as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        print(f"refused: {detail}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
