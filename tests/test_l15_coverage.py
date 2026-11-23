"""L15 finish: exact coverage of each dataset, the catalog GID search, and the map catalog panel."""

from __future__ import annotations

import json

import geosquare_engine as gs
import pytest
from fastapi.testclient import TestClient

from geosquare_platform.app import app
from geosquare_platform.cellstore import Governance, parse_value_fields, store_csv
from geosquare_platform.registry import load_registry

FIELDS = json.dumps([{"name": "share", "logical_type": "numeric", "semantics": "MEASUREMENT", "unit": "ratio"}])


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    root = tmp_path / "cells"
    monkeypatch.setenv("GSQ_PLATFORM_DATA", str(root))
    return root


def cell(x: int, y: int = 3) -> str:
    return gs.encode(9, x, y)


def store(root, registry, cell_gid: str, value: str):
    body = f"gid,share\n{cell_gid},{value}\n".encode()
    return store_csv(
        registry,
        root,
        body,
        value_fields=parse_value_fields(json.loads(FIELDS)),
        governance=Governance(license="internal-test", share_policy="internal"),
    )


def test_catalog_gid_search_finds_the_dataset_that_covers_the_cell(data_root):
    registry = load_registry()
    near = store(data_root, registry, cell(10), "0.2")
    far = store(data_root, registry, cell(40, 40), "0.7")
    client = TestClient(app)

    inside = gs.children(cell(10))[0]  # a finer cell inside the first dataset's cell
    found = client.get("/catalog", params={"gid": inside}).json()
    assert [entry["dataset_id"] for entry in found] == [near.dataset_id]

    found_far = client.get("/catalog", params={"gid": gs.children(cell(40, 40))[0]}).json()
    assert [entry["dataset_id"] for entry in found_far] == [far.dataset_id]

    nothing = client.get("/catalog", params={"gid": cell(200, 200)}).json()
    assert nothing == []


def test_catalog_gid_search_rejects_a_malformed_gid(data_root):
    response = TestClient(app).get("/catalog", params={"gid": "!!"})

    assert response.status_code == 422


def test_dataset_coverage_is_the_compact_cell_set_and_is_cached(data_root):
    registry = load_registry()
    stored = store(data_root, registry, cell(10), "0.2")
    client = TestClient(app)

    response = client.get(f"/datasets/{stored.dataset_id}/coverage")

    assert response.status_code == 200, response.text
    assert response.json()["cells"] == [cell(10)]
    assert (data_root / "coverage" / f"{stored.dataset_id}.json").is_file()


def test_coverage_folder_is_not_listed_as_a_dataset(data_root):
    registry = load_registry()
    stored = store(data_root, registry, cell(10), "0.2")
    client = TestClient(app)
    client.get(f"/datasets/{stored.dataset_id}/coverage")

    entries = client.get("/catalog").json()

    assert [entry["dataset_id"] for entry in entries] == [stored.dataset_id]
    assert all("error" not in entry for entry in entries)


def test_coverage_of_an_unknown_dataset_is_404(data_root):
    response = TestClient(app).get("/datasets/ID-L9-000000000000/coverage")

    assert response.status_code == 404


def test_map_page_has_the_catalog_panel(data_root):
    page = TestClient(app).get("/map")

    assert page.status_code == 200
    assert 'id="catalog-list"' in page.text
    assert 'id="gid"' in page.text
    assert "Find datasets covering this cell" in page.text
