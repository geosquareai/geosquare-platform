"""Raster to cells (L14). The test writes a small tiled GeoTIFF with rasterio, reads it with the
engine's read_raster, and checks each cell mean against a Python reference.

Needs rasterio and a built engine extension in GSQ_DUCKDB_EXTENSION. Without them the test skips.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from geosquare_platform.cellstore import Governance, ValueField
from geosquare_platform.importer import ImportRejected
from geosquare_platform.raster import raster_to_cells
from geosquare_platform.registry import load_registry
from geosquare_v2 import GeosquareGrid, read_cell_dataset
from geosquare_v2.crs import wgs84_to_grid_transformer

EXTENSION = os.environ.get("GSQ_DUCKDB_EXTENSION")
GOVERNANCE = Governance(license="synthetic test raster", share_policy="internal")
FLOOD = ValueField("depth_m", "numeric", "MEASUREMENT", "m", "median", None)
LEVEL = 12
ORIGIN_LON, ORIGIN_LAT, RES = 106.70, -6.10, 0.00027  # about 30 m pixels


@pytest.fixture
def raster(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    from rasterio.transform import from_origin

    rng = np.random.default_rng(7)
    height = width = 600
    values = (rng.random((height, width)) * 2).astype("float32")
    values[:, :40] = -9999.0  # nodata strip on the left
    path = tmp_path / "flood.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(ORIGIN_LON, ORIGIN_LAT, RES, RES),
        tiled=True, blockxsize=256, blockysize=256, compress="deflate", predictor=3, nodata=-9999.0,
    ) as dst:
        dst.write(values, 1)
    return path, values


BBOX = [106.7135, -6.2, 106.7800, -6.1500]


@pytest.mark.skipif(not EXTENSION, reason="set GSQ_DUCKDB_EXTENSION to the built engine extension")
def test_cell_means_match_a_python_reference(raster, tmp_path):
    path, values = raster
    stored = raster_to_cells(
        tmp_path / "cells", path, level=LEVEL, bbox=BBOX, value_field=FLOOD,
        governance=GOVERNANCE, extension=EXTENSION,
    )
    table = read_cell_dataset(stored.path, registry=load_registry()).table
    got = dict(zip(table.column("gid").to_pylist(), table.column("depth_m").to_pylist()))

    # Reference: the same pixel centres, mapped with the Python grid and averaged per cell.
    profile = load_registry().get("ID")
    grid = GeosquareGrid(profile)
    transformer = wgs84_to_grid_transformer(profile)
    sums: dict[str, list[float]] = {}
    height, width = values.shape
    for row in range(height):
        lat = ORIGIN_LAT - (row + 0.5) * RES
        if not BBOX[1] <= lat <= BBOX[3]:
            continue
        for col in range(width):
            lon = ORIGIN_LON + (col + 0.5) * RES
            value = float(values[row, col])
            if value == -9999.0 or not BBOX[0] <= lon <= BBOX[2]:
                continue
            gid = grid.gid_from_lonlat(lon, lat, LEVEL, transformer)
            sums.setdefault(gid, []).append(value)
    expected = {gid: sum(v) / len(v) for gid, v in sums.items()}

    assert stored.level == LEVEL
    assert set(got) == set(expected)
    for gid, mean in expected.items():
        assert got[gid] == pytest.approx(mean, rel=1e-6, abs=1e-6), gid


def test_missing_extension_is_refused(raster, tmp_path, monkeypatch):
    monkeypatch.delenv("GSQ_DUCKDB_EXTENSION", raising=False)
    path, _ = raster
    with pytest.raises(ImportRejected, match="GSQ_DUCKDB_EXTENSION"):
        raster_to_cells(tmp_path / "cells", path, level=LEVEL, bbox=BBOX, value_field=FLOOD, governance=GOVERNANCE)


@pytest.mark.skipif(not EXTENSION, reason="set GSQ_DUCKDB_EXTENSION to the built engine extension")
def test_projected_raster_error_is_one_line_without_sql(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    from rasterio.transform import from_origin

    path = tmp_path / "utm.tif"
    data = np.ones((256, 256), dtype="float32")
    with rasterio.open(
        path, "w", driver="GTiff", height=256, width=256, count=1, dtype="float32",
        crs="EPSG:32748", transform=from_origin(500000, 9000000, 30, 30), tiled=True, blockxsize=256, blockysize=256,
    ) as dst:
        dst.write(data, 1)
    with pytest.raises(ImportRejected) as caught:
        raster_to_cells(tmp_path / "cells", path, level=LEVEL, bbox=BBOX, value_field=FLOOD,
                        governance=GOVERNANCE, extension=EXTENSION)
    message = str(caught.value)
    assert message.startswith("raster could not be read: ")
    assert "\n" not in message and "LINE 1" not in message
