from __future__ import annotations

import json

import geosquare_engine as gs
import pytest
from fastapi.testclient import TestClient
from geosquare_v2 import CellDatasetError, read_cell_dataset

from geosquare_platform.app import app
from geosquare_platform.cellstore import DatasetExists, Governance, ValueField, parse_value_fields, read_dataset, store_csv
from geosquare_platform.importer import ImportRejected
from geosquare_platform.registry import load_registry

NUMERIC = ValueField("share", "numeric", "MEASUREMENT", "ratio")
CATEGORY = ValueField("zone", "categorical", "CATEGORICAL", None)
VALUE_FIELDS_JSON = json.dumps(
    [
        {"name": "share", "logical_type": "numeric", "semantics": "MEASUREMENT", "unit": "ratio"},
        {"name": "zone", "logical_type": "categorical", "semantics": "CATEGORICAL", "unit": None},
    ]
)


GOVERNANCE = Governance(license="internal-test", share_policy="internal", owner="data team")
GOV_FORM = {"license": "internal-test", "share_policy": "internal", "owner": "data team"}


@pytest.fixture(scope="module")
def registry():
    return load_registry()


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    root = tmp_path / "cells"
    monkeypatch.setenv("GSQ_PLATFORM_DATA", str(root))
    return root


def cell(x: int, y: int = 3, level: int = 9) -> str:
    return gs.encode(level, x, y)


def csv(rows, header=("gid", "share", "zone")) -> bytes:
    lines = [",".join(header)] + [",".join(row) for row in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


GOOD = csv(
    [
        (cell(10), "0.25", "A"),
        (cell(11), "1", "B"),
        (cell(12), "0.5", "A"),
    ]
)


def test_stores_cells_with_a_valid_manifest(registry, tmp_path):
    stored = store_csv(registry, tmp_path, GOOD, value_fields=[NUMERIC, CATEGORY], governance=GOVERNANCE)

    assert stored.dataset_id.startswith("ID-L9-")
    assert (stored.level, stored.rows, stored.unique_cells) == (9, 3, 3)
    assert stored.dropped_columns == ()

    result = read_dataset(registry, stored.path)
    assert result["manifest"]["domain_code"] == "ID"
    assert result["manifest"]["domain_id"] == 1
    assert result["manifest"]["identity"]["gid_column"] == "gid"
    from importlib.metadata import version as installed_version

    assert f"geosquare-engine {installed_version('geosquare-engine')}" in result["manifest"]["provenance"]["created_by"]

    table = read_cell_dataset(stored.path, registry=registry).table
    assert table.to_pydict() == {
        "gid": [cell(10), cell(11), cell(12)],
        "share": [0.25, 1.0, 0.5],
        "zone": ["A", "B", "A"],
    }


def test_key_column_is_stored_as_gid_and_extra_columns_are_reported(registry, tmp_path):
    data = csv([(cell(10), "0.25", "A", "x")], header=("cell", "share", "zone", "note"))
    stored = store_csv(registry, tmp_path, data, value_fields=[NUMERIC, CATEGORY], governance=GOVERNANCE)

    assert stored.dropped_columns == ("note",)
    table = read_cell_dataset(stored.path, registry=registry).table
    assert table.column_names == ["gid", "share", "zone"]


def test_duplicate_gid_is_rejected_and_nothing_is_written(registry, tmp_path):
    data = csv([(cell(10), "0.25", "A"), (cell(10), "0.5", "B")])
    with pytest.raises(CellDatasetError, match="duplicate"):
        store_csv(registry, tmp_path, data, value_fields=[NUMERIC, CATEGORY], governance=GOVERNANCE)
    assert list(tmp_path.iterdir()) == []


def test_bad_number_names_the_row_and_column(registry, tmp_path):
    data = csv([(cell(10), "0.25", "A"), (cell(11), "n/a", "B")])
    with pytest.raises(ImportRejected, match="row 2, column 'share'"):
        store_csv(registry, tmp_path, data, value_fields=[NUMERIC, CATEGORY], governance=GOVERNANCE)


def test_empty_value_is_rejected(registry, tmp_path):
    data = csv([(cell(10), "0.25", "")])
    with pytest.raises(ImportRejected, match="row 1, column 'zone'"):
        store_csv(registry, tmp_path, data, value_fields=[NUMERIC, CATEGORY], governance=GOVERNANCE)


def test_same_upload_twice_is_refused(registry, tmp_path):
    store_csv(registry, tmp_path, GOOD, value_fields=[NUMERIC, CATEGORY], governance=GOVERNANCE)
    with pytest.raises(DatasetExists):
        store_csv(registry, tmp_path, GOOD, value_fields=[NUMERIC, CATEGORY], governance=GOVERNANCE)


def test_value_field_list_is_checked():
    with pytest.raises(ImportRejected, match="logical_type"):
        parse_value_fields([{"name": "share", "logical_type": "float", "semantics": "MEASUREMENT"}])
    with pytest.raises(ImportRejected, match="unknown keys"):
        parse_value_fields([{"name": "share", "logical_type": "numeric", "semantics": "COUNT", "colour": "red"}])
    with pytest.raises(ImportRejected, match="at least one"):
        parse_value_fields([])


def test_tampered_row_count_fails_the_check(registry, tmp_path):
    stored = store_csv(registry, tmp_path, GOOD, value_fields=[NUMERIC, CATEGORY], governance=GOVERNANCE)
    manifest_path = stored.path / "manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["storage"]["row_count"] = 99
    manifest_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(CellDatasetError):
        read_dataset(registry, stored.path)


def test_api_store_then_read_back(data_root):
    client = TestClient(app)
    created = client.post(
        "/datasets",
        files={"file": ("cells.csv", GOOD, "text/csv")},
        data={"value_fields": VALUE_FIELDS_JSON, **GOV_FORM},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["level"] == 9 and body["rows"] == 3 and body["valid"] is True

    fetched = client.get(f"/datasets/{body['dataset_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["manifest"]["level"] == 9


def test_api_second_upload_returns_409(data_root):
    client = TestClient(app)
    payload = {"value_fields": VALUE_FIELDS_JSON, **GOV_FORM}
    first = client.post("/datasets", files={"file": ("cells.csv", GOOD)}, data=payload)
    second = client.post("/datasets", files={"file": ("cells.csv", GOOD)}, data=payload)
    assert first.status_code == 201
    assert second.status_code == 409


def test_api_rejects_bad_value_fields(data_root):
    client = TestClient(app)
    not_json = client.post("/datasets", files={"file": ("cells.csv", GOOD)}, data={"value_fields": "{"})
    not_list = client.post("/datasets", files={"file": ("cells.csv", GOOD)}, data={"value_fields": "{}"})
    assert not_json.status_code == 422
    assert not_list.status_code == 422


def test_api_get_rejects_unknown_and_malformed_ids(data_root):
    client = TestClient(app)
    assert client.get("/datasets/not-an-id").status_code == 404
    assert client.get("/datasets/ID-L9-000000000000").status_code == 404


def test_api_get_reports_a_tampered_dataset(data_root):
    client = TestClient(app)
    created = client.post("/datasets", files={"file": ("cells.csv", GOOD)}, data={"value_fields": VALUE_FIELDS_JSON, **GOV_FORM})
    dataset_id = created.json()["dataset_id"]
    manifest_path = data_root / dataset_id / "manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["storage"]["unique_cell_count"] = 1
    manifest_path.write_text(json.dumps(document), encoding="utf-8")

    response = client.get(f"/datasets/{dataset_id}")
    assert response.status_code == 422
