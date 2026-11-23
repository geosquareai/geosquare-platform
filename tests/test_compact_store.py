from __future__ import annotations

import json

import geosquare_engine as gs
import pytest
from fastapi.testclient import TestClient

from geosquare_platform.app import INTERNAL_ENV, app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GSQ_PLATFORM_DATA", str(tmp_path / "data"))
    return TestClient(app)


def compact_bytes(rows) -> bytes:
    return ("gid,level\n" + "".join(f"{gid},{level}\n" for gid, level in rows)).encode()


def upload_compact(client, rows):
    return client.post("/datasets/compact", files={"file": ("compact.csv", compact_bytes(rows), "text/csv")})


def create_values(client, rows) -> str:
    body = "gid,population\n" + "".join(f"{gid},{value}\n" for gid, value in rows)
    response = client.post(
        "/datasets",
        files={"file": ("data.csv", body.encode(), "text/csv")},
        params={"confirm_digit_gid": "true"},
        data={
            "license": "internal-test",
            "share_policy": "internal",
            "value_fields": json.dumps(
                [{"name": "population", "logical_type": "numeric", "semantics": "COUNT", "unit": None}]
            ),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["dataset_id"]


def level8_cell() -> str:
    return gs.encode(8, 0, 0)


def test_store_then_join_sums_only_covered_cells(client, monkeypatch):
    monkeypatch.setenv(INTERNAL_ENV, "1")
    cell = level8_cell()
    stored = upload_compact(client, [(cell, 8)])
    assert stored.status_code == 201, stored.text
    assert stored.json()["dataset_type"] == "cell_compact_set"
    children = gs.children(cell)
    outside = gs.encode(9, 100, 100)
    dataset = create_values(client, [(children[0], "2"), (children[7], "3"), (outside, "7")])

    joined = client.post(
        "/query/compact-join",
        json={"compact_id": stored.json()["dataset_id"], "dataset_id": dataset, "sum": ["population"]},
    )

    assert joined.status_code == 200, joined.text
    assert joined.json()["matched_cells"] == 2
    assert joined.json()["sums"] == {"population": pytest.approx(5)}


def test_non_canonical_set_is_rejected(client):
    complete_group = gs.children(level8_cell())  # all 25 children replace their parent
    response = upload_compact(client, [(gid, 9) for gid in complete_group])
    assert response.status_code == 422
    assert "not canonical" in response.json()["detail"]


def test_duplicate_row_and_wrong_level_are_rejected(client):
    cell = level8_cell()
    assert upload_compact(client, [(cell, 8), (cell, 8)]).status_code == 422
    assert upload_compact(client, [(cell, 9)]).status_code == 422


def test_same_file_twice_is_a_conflict(client):
    assert upload_compact(client, [(level8_cell(), 8)]).status_code == 201
    assert upload_compact(client, [(level8_cell(), 8)]).status_code == 409


def test_compact_join_is_hidden_unless_internal(client, monkeypatch):
    monkeypatch.delenv(INTERNAL_ENV, raising=False)
    stored = upload_compact(client, [(level8_cell(), 8)])
    dataset = create_values(client, [(gs.children(level8_cell())[0], "2")])
    response = client.post(
        "/query/compact-join",
        json={"compact_id": stored.json()["dataset_id"], "dataset_id": dataset, "sum": ["population"]},
    )
    assert response.status_code == 404


def test_compact_join_rejects_unknown_compact_id(client, monkeypatch):
    monkeypatch.setenv(INTERNAL_ENV, "1")
    dataset = create_values(client, [(gs.children(level8_cell())[0], "2")])
    response = client.post(
        "/query/compact-join",
        json={"compact_id": "ID-COMPACT-000000000000", "dataset_id": dataset, "sum": ["population"]},
    )
    assert response.status_code == 404
