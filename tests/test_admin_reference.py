"""L17: cell to admin reference table, checked on a small synthetic boundary."""

from __future__ import annotations

import geosquare_engine as gs
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from geosquare_v2 import GeosquareGrid
from geosquare_v2.crs import wgs84_to_grid_transformer
from geosquare_v2.geometry import projected_cell_geometry
from shapely import to_wkb
from shapely.geometry import box
from shapely.ops import transform

from geosquare_platform.admin_reference import build_reference
from geosquare_platform.registry import load_registry

LEVEL = 12
CELL_AREA = 50.0 * 50.0
# Relative area error allowed between output and polygon area.
TOLERANCE = 1e-6

# Two villages share the edge at lon 106.805. The third village sits 5 km away.
VILLAGES = {
    "3171010001": box(106.8000, -6.2000, 106.8050, -6.1950),
    "3171010002": box(106.8050, -6.2000, 106.8100, -6.1950),
    "3171020001": box(106.8500, -6.2000, 106.8550, -6.1950),
}
REGENCY = "3171"


def _write(path, polygons: dict, regency: str = REGENCY):
    table = pa.table(
        {
            "village_code": list(polygons),
            "regency_code": [regency] * len(polygons),
            "geom": [to_wkb(geom) for geom in polygons.values()],
        }
    )
    pq.write_table(table, path)
    return path


def _grid_area(geom) -> float:
    to_grid = wgs84_to_grid_transformer(load_registry().get("ID"))
    return transform(to_grid.transform, geom).area


@pytest.fixture
def built(tmp_path):
    source = _write(tmp_path / "boundary.parquet", VILLAGES)
    out = tmp_path / "admin_reference.parquet"
    report = build_reference(
        source,
        out,
        level=LEVEL,
        admin_columns={"village_code": "village_code", "regency_code": "regency_code"},
        source_name="synthetic test boundary",
        license_name="test only",
    )
    return out, report, pq.read_table(out).to_pylist()


def test_area_per_village_matches_polygon(built):
    _, report, rows = built
    for village, geom in VILLAGES.items():
        area = sum(row["area_weight"] for row in rows if row["village_code"] == village) * CELL_AREA
        assert area == pytest.approx(_grid_area(geom), rel=TOLERANCE), village
    assert report["units"] == 3
    assert report["max_area_error"] <= TOLERANCE


def test_weight_sum_per_cell_is_at_most_one(built):
    _, report, rows = built
    sums: dict[str, float] = {}
    for row in rows:
        sums[row["gid"]] = sums.get(row["gid"], 0.0) + row["area_weight"]
    assert max(sums.values()) <= 1 + 1e-9
    assert report["max_cell_weight_sum"] <= 1 + 1e-9
    # Some cells lie fully inside a village, so their weight is 1.
    assert report["cells_inside"] > 0


def test_cell_on_border_splits_between_two_villages(built):
    _, _, rows = built
    by_gid: dict[str, dict[str, float]] = {}
    for row in rows:
        by_gid.setdefault(row["gid"], {})[row["village_code"]] = row["area_weight"]
    shared = [parts for parts in by_gid.values() if {"3171010001", "3171010002"} <= parts.keys()]
    assert shared, "no cell lies across the shared edge"
    for parts in shared:
        assert 0 < parts["3171010001"] < 1
        assert 0 < parts["3171010002"] < 1
        assert sum(parts.values()) <= 1 + 1e-9
    # Cells at the top and bottom corners stick out of the union, so their sum is below 1.
    assert any(sum(parts.values()) == pytest.approx(1, abs=1e-9) for parts in shared)


def test_cell_outside_all_villages_has_no_row(built):
    _, _, rows = built
    grid = GeosquareGrid(load_registry().get("ID"))
    gids = {row["gid"] for row in rows}
    polygons = [transform(wgs84_to_grid_transformer(load_registry().get("ID")).transform, g) for g in VILLAGES.values()]
    checked = 0
    for gid in gids:
        for neighbour in gs.k_ring(gid, 1):
            if neighbour in gids:
                continue
            cell = projected_cell_geometry(grid, neighbour)
            assert all(cell.intersection(poly).area == 0 for poly in polygons), neighbour
            checked += 1
    assert checked > 0


def test_province_is_first_two_digits_of_regency(built):
    _, _, rows = built
    assert {row["province_code"] for row in rows} == {"31"}


def test_metadata_records_source_and_licence(built):
    out, _, _ = built
    metadata = pq.read_schema(out).metadata
    assert metadata[b"source_name"] == b"synthetic test boundary"
    assert metadata[b"license"] == b"test only"
    assert metadata[b"level"] == b"12"


def test_overlapping_units_are_rejected(tmp_path):
    polygons = {"3171010001": VILLAGES["3171010001"], "3171010009": VILLAGES["3171010001"]}
    source = _write(tmp_path / "overlap.parquet", polygons)
    with pytest.raises(ValueError, match="overlap"):
        build_reference(
            source,
            tmp_path / "out.parquet",
            level=LEVEL,
            admin_columns={"village_code": "village_code", "regency_code": "regency_code"},
            source_name="x",
            license_name="x",
        )


def test_empty_source_or_licence_is_rejected(tmp_path):
    source = _write(tmp_path / "boundary.parquet", VILLAGES)
    admin = {"village_code": "village_code", "regency_code": "regency_code"}
    for source_name, license_name in [("", "CC-BY"), ("  ", "CC-BY"), ("BIG", ""), ("BIG", " ")]:
        with pytest.raises(ValueError, match="required"):
            build_reference(
                source,
                tmp_path / "out.parquet",
                admin_columns=admin,
                source_name=source_name,
                license_name=license_name,
            )
    assert not (tmp_path / "out.parquet").exists()


def test_default_level_is_11(tmp_path):
    source = _write(tmp_path / "boundary.parquet", VILLAGES)
    out = tmp_path / "out.parquet"
    build_reference(
        source,
        out,
        admin_columns={"village_code": "village_code", "regency_code": "regency_code"},
        source_name="synthetic",
        license_name="test only",
    )
    assert pq.read_schema(out).metadata[b"level"] == b"11"


def test_overlap_inside_one_cell_is_rejected(tmp_path):
    # Two identical ~40 m squares. Their overlap sits inside one 100 m cell, so the cell's weight sum stays below 1.
    square = box(106.8000, -6.2000, 106.80036, -6.19964)
    source = _write(tmp_path / "overlap.parquet", {"3171010001": square, "3171010009": square})
    with pytest.raises(ValueError, match="overlap"):
        build_reference(
            source,
            tmp_path / "out.parquet",
            admin_columns={"village_code": "village_code", "regency_code": "regency_code"},
            source_name="x",
            license_name="x",
        )
    assert not (tmp_path / "out.parquet").exists()


def test_metre_coordinates_are_rejected_with_wgs84_message(tmp_path):
    source = _write(tmp_path / "utm.parquet", {"3171010001": box(700000, 9300000, 700500, 9300500)})
    with pytest.raises(ValueError, match="WGS 84 lon/lat"):
        build_reference(
            source,
            tmp_path / "out.parquet",
            admin_columns={"village_code": "village_code", "regency_code": "regency_code"},
            source_name="x",
            license_name="x",
        )


def test_missing_column_is_rejected(tmp_path):
    source = _write(tmp_path / "boundary.parquet", VILLAGES)
    with pytest.raises(ValueError, match="no columns"):
        build_reference(
            source,
            tmp_path / "out.parquet",
            level=LEVEL,
            admin_columns={"district_code": "kode_kec"},
            source_name="x",
            license_name="x",
        )
