from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pyproj import Transformer

from geosquare_engine import encode
from geosquare_platform.app import app
from geosquare_platform.cellstore import Governance, ValueField, store_csv
from geosquare_platform.map_routes import _cell_centre_wgs84
from geosquare_platform.registry import load_registry
from geosquare_v2 import GeosquareGrid


VALUE_FIELDS = [ValueField("share", "numeric", "MEASUREMENT", "ratio")]
GIDS = [encode(9, 10, 3), encode(9, 11, 3), encode(9, 12, 3)]


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    root = tmp_path / "cells"
    monkeypatch.setenv("GSQ_PLATFORM_DATA", str(root))
    csv = "gid,share\n" + "\n".join(f"{gid},{index}" for index, gid in enumerate(GIDS, start=1)) + "\n"
    return store_csv(
        load_registry(),
        root,
        csv.encode(),
        value_fields=VALUE_FIELDS,
        governance=Governance(license="internal-test", share_policy="internal"),
    )


@pytest.fixture
def client(dataset):
    return TestClient(app)


def _centre(gid: str) -> tuple[float, float]:
    profile = load_registry().get("ID")
    grid = GeosquareGrid(profile)
    transformer = Transformer.from_crs(profile.crs_wkt2, "EPSG:4326", always_xy=True)
    return _cell_centre_wgs84(grid, gid, transformer)


def test_cells_filters_by_cell_centre_bbox(client, dataset):
    lon, lat = _centre(GIDS[0])
    other_lons = [_centre(gid)[0] for gid in GIDS[1:]]
    margin = min(abs(lon - other_lon) for other_lon in other_lons) / 3
    bbox = f"{lon - margin},{lat - 0.1},{lon + margin},{lat + 0.1}"

    response = client.get(f"/datasets/{dataset.dataset_id}/cells", params={"bbox": bbox, "limit": 20})

    assert response.status_code == 200, response.text
    assert [row["gid"] for row in response.json()["cells"]] == [GIDS[0]]


def test_cells_honours_limit(client, dataset):
    response = client.get(
        f"/datasets/{dataset.dataset_id}/cells",
        params={"bbox": "-180,-90,180,90", "limit": 2},
    )

    assert response.status_code == 200, response.text
    assert len(response.json()["cells"]) == 2


def test_cells_returns_404_for_unknown_dataset(client):
    response = client.get("/datasets/ID-L9-000000000000/cells", params={"bbox": "-180,-90,180,90"})

    assert response.status_code == 404


def test_cells_contains_only_gid_and_value_columns(client, dataset):
    response = client.get(
        f"/datasets/{dataset.dataset_id}/cells",
        params={"bbox": "-180,-90,180,90", "limit": 20},
    )

    assert response.status_code == 200, response.text
    rows = response.json()["cells"]
    assert rows
    assert all(set(row) == {"gid", "share"} for row in rows)
    assert all("geometry" not in row and "polygon" not in row for row in rows)
