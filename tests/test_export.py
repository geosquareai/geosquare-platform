from __future__ import annotations

import json

import geosquare_engine as gs
import pytest
from fastapi.testclient import TestClient

from geosquare_platform.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GSQ_PLATFORM_DATA", str(tmp_path / "data"))
    return TestClient(app)


def dataset_csv(rows: list[dict[str, object]], fields: list[dict[str, object]]) -> bytes:
    names = [field["name"] for field in fields]
    lines = [",".join(["gid", *names])]
    for row in rows:
        lines.append(",".join(str(row[name]) for name in ["gid", *names]))
    return ("\n".join(lines) + "\n").encode()


def create_dataset(
    client: TestClient,
    rows: list[dict[str, object]],
    fields: list[dict[str, object]],
) -> str:
    response = client.post(
        "/datasets",
        files={"file": ("data.csv", dataset_csv(rows, fields), "text/csv")},
        data={"value_fields": json.dumps(fields)},
    )
    assert response.status_code == 201, response.text
    return response.json()["dataset_id"]


def count_field() -> dict[str, object]:
    return {"name": "people", "logical_type": "numeric", "semantics": "COUNT", "unit": "persons"}


def test_export_requires_k_and_rejects_k_below_two(client):
    parent = gs.encode(8, 0, 4)
    cell = gs.children(parent)[0]
    dataset = create_dataset(client, [{"gid": cell, "people": 3}], [count_field()])

    assert client.get(f"/datasets/{dataset}/export", params={"count": "people"}).status_code == 422
    assert client.get(f"/datasets/{dataset}/export", params={"k": 1, "count": "people"}).status_code == 422


def test_export_requires_count_semantics(client):
    parent = gs.encode(8, 0, 4)
    cell = gs.children(parent)[0]
    fields = [{"name": "value", "logical_type": "numeric", "semantics": "TOTAL", "unit": "units"}]
    dataset = create_dataset(client, [{"gid": cell, "value": 3}], fields)

    response = client.get(f"/datasets/{dataset}/export", params={"k": 2, "count": "value"})

    assert response.status_code == 422


def test_export_partitions_conserves_and_merges_small_siblings(client):
    parent = gs.encode(8, 0, 4)
    large, small_one, small_two = gs.children(parent)[:3]
    fields = [
        count_field(),
        {"name": "population", "logical_type": "numeric", "semantics": "TOTAL", "unit": "persons"},
        {"name": "share", "logical_type": "numeric", "semantics": "MEASUREMENT", "unit": "ratio"},
        {"name": "land_use", "logical_type": "categorical", "semantics": "CATEGORICAL", "unit": None},
    ]
    dataset = create_dataset(
        client,
        [
            {"gid": large, "people": 4, "population": 40, "share": 0.4, "land_use": "urban"},
            {"gid": small_one, "people": 1, "population": 10, "share": 0.1, "land_use": "rural"},
            {"gid": small_two, "people": 2, "population": 20, "share": 0.2, "land_use": "rural"},
        ],
        fields,
    )

    response = client.get(f"/datasets/{dataset}/export", params={"k": 3, "count": "people"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["k"] == 3
    assert body["count_field"] == "people"
    assert body["input_level"] == 9
    assert body["suppressed_cell_count"] == 0
    assert body["suppressed_count"] == 0
    assert body["dropped_fields"] == ["share", "land_use"]
    assert {row["gid"] for row in body["rows"]} == {large, parent}
    large_row = next(row for row in body["rows"] if row["gid"] == large)
    parent_row = next(row for row in body["rows"] if row["gid"] == parent)
    assert large_row["level"] == 9
    assert large_row["count"] == pytest.approx(4)
    assert large_row["source_cell_count"] == 1
    assert parent_row["level"] == 8
    assert parent_row["count"] == pytest.approx(3)
    assert parent_row["totals"] == {"population": pytest.approx(30)}
    assert parent_row["source_cell_count"] == 2
    assert sum(row["count"] for row in body["rows"]) + body["suppressed_count"] == pytest.approx(7)


def test_export_suppresses_all_small_cells(client):
    parent = gs.encode(8, 0, 4)
    first, second = gs.children(parent)[:2]
    dataset = create_dataset(
        client,
        [{"gid": first, "people": 1}, {"gid": second, "people": 1}],
        [count_field()],
    )

    response = client.get(f"/datasets/{dataset}/export", params={"k": 3, "count": "people"})

    assert response.status_code == 200, response.text
    assert response.json()["rows"] == []
    assert response.json()["suppressed_cell_count"] == 2
    assert response.json()["suppressed_count"] == pytest.approx(2)
