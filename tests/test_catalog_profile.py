from __future__ import annotations

import json

import geosquare_engine as gs
import pytest
from fastapi.testclient import TestClient

from geosquare_platform.app import app

VALUE_FIELDS = json.dumps(
    [{"name": "share", "logical_type": "numeric", "semantics": "MEASUREMENT", "unit": "ratio"}]
)


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    root = tmp_path / "cells"
    monkeypatch.setenv("GSQ_PLATFORM_DATA", str(root))
    return root


def gid(level: int, x: int = 10, y: int = 3) -> str:
    return gs.encode(level, x, y)


def csv(rows: list[tuple[str, str]]) -> bytes:
    lines = ["gid,share"] + [f"{cell},{value}" for cell, value in rows]
    return ("\n".join(lines) + "\n").encode()


def upload(client: TestClient, rows: list[tuple[str, str]]):
    return client.post(
        "/datasets",
        files={"file": ("cells.csv", csv(rows), "text/csv")},
        data={"value_fields": VALUE_FIELDS},
    )


def test_catalog_is_empty_without_datasets(data_root):
    assert TestClient(app).get("/catalog").json() == []


def test_catalog_lists_two_datasets_from_manifests(data_root):
    client = TestClient(app)
    first = upload(client, [(gid(9), "1.5")])
    second = upload(client, [(gid(9, x=11), "2.5")])
    assert first.status_code == 201
    assert second.status_code == 201

    response = client.get("/catalog")

    assert response.status_code == 200
    entries = response.json()
    assert [entry["dataset_id"] for entry in entries] == sorted(entry["dataset_id"] for entry in entries)
    assert entries[0]["domain_code"] == "ID"
    assert entries[0]["dataset_type"] == "cell_values"
    assert entries[0]["row_count"] == 1
    assert entries[0]["unique_cell_count"] == 1
    assert entries[0]["value_fields"] == [
        {"name": "share", "semantics": "MEASUREMENT", "unit": "ratio", "nullable": False}
    ]


def test_catalog_reports_unreadable_manifest(data_root):
    broken = data_root / "ID-L9-broken"
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text("{", encoding="utf-8")

    response = TestClient(app).get("/catalog")

    assert response.status_code == 200
    assert response.json() == [
        {"dataset_id": "ID-L9-broken", "error": "cannot read manifest: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"}
    ]


def test_cell_profile_same_level_hit_miss_and_ancestor(data_root):
    client = TestClient(app)
    ancestor = gid(9)
    child = gs.children(ancestor)[0]
    other_child = gs.children(ancestor)[1]
    coarse = upload(client, [(ancestor, "10")])
    same_level = upload(client, [(child, "20")])
    assert coarse.status_code == 201
    assert same_level.status_code == 201

    hit = client.get(f"/cells/{child}/profile")
    miss = client.get(f"/cells/{other_child}/profile")

    assert hit.status_code == 200
    hit_by_id = {dataset["dataset_id"]: dataset for dataset in hit.json()["datasets"]}
    assert hit.json()["parent_gid"] == ancestor
    assert hit_by_id[coarse.json()["dataset_id"]] == {
        "dataset_id": coarse.json()["dataset_id"],
        "relation": "ancestor",
        "present": True,
        "values": {"share": 10.0},
    }
    assert hit_by_id[same_level.json()["dataset_id"]]["relation"] == "same"
    assert hit_by_id[same_level.json()["dataset_id"]]["present"] is True
    assert hit_by_id[same_level.json()["dataset_id"]]["values"] == {"share": 20.0}

    assert miss.status_code == 200
    miss_by_id = {dataset["dataset_id"]: dataset for dataset in miss.json()["datasets"]}
    assert miss_by_id[same_level.json()["dataset_id"]]["present"] is False
    assert miss_by_id[same_level.json()["dataset_id"]]["values"] == {}


def test_cell_profile_marks_finer_dataset_not_applicable(data_root):
    client = TestClient(app)
    parent = gid(9)
    child = gs.children(parent)[0]
    finer = upload(client, [(child, "4")])
    assert finer.status_code == 201

    response = client.get(f"/cells/{parent}/profile")

    assert response.status_code == 200
    dataset = response.json()["datasets"][0]
    assert dataset == {
        "dataset_id": finer.json()["dataset_id"],
        "relation": "finer",
        "present": False,
        "values": {},
        "status": "not_applicable",
        "reason": "finer level; query the children",
    }


def test_cell_profile_rejects_invalid_gid(data_root):
    response = TestClient(app).get("/cells/not-a-gid/profile")

    assert response.status_code == 422
    assert "invalid GID" in response.json()["detail"]
