from __future__ import annotations

import json

import geosquare_engine as gs
import pytest
from fastapi.testclient import TestClient

from geosquare_platform.app import INTERNAL_ENV, app
from geosquare_platform.crosswalk import CrosswalkRejected, validate_crosswalk


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GSQ_PLATFORM_DATA", str(tmp_path / "data"))
    return TestClient(app)


def gid(x: int) -> str:
    return gs.encode(9, x, 3)


def crosswalk_csv(rows) -> bytes:
    return ("sumber_id,gid,bobot\n" + "\n".join(f"{source},{cell},{weight}" for source, cell, weight in rows) + "\n").encode()


def dataset_csv(rows, column="value") -> bytes:
    return (f"gid,{column}\n" + "\n".join(f"{cell},{value}" for cell, value in rows) + "\n").encode()


def create_dataset(client, rows, column="value") -> str:
    response = client.post(
        "/datasets",
        files={"file": ("data.csv", dataset_csv(rows, column), "text/csv")},
        data={
            "license": "internal-test",
            "share_policy": "internal",
            "value_fields": json.dumps(
                [{"name": column, "logical_type": "numeric", "semantics": "COUNT", "unit": None}]
            ),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["dataset_id"]


def test_crosswalk_rules_valid_sum_duplicate_and_wrong_level():
    valid = validate_crosswalk(
        crosswalk_csv([("a", gid(10), 0.4), ("a", gid(11), 0.6)]),
        domain_code="ID",
        level=9,
        weight_kind="luas",
    )
    assert valid.summary() == {"sources": 1, "rows": 2, "cells": 2}
    with pytest.raises(CrosswalkRejected, match="sum to"):
        validate_crosswalk(crosswalk_csv([("a", gid(10), 0.4)]), domain_code="ID", level=9, weight_kind="luas")
    with pytest.raises(CrosswalkRejected, match="duplicate"):
        validate_crosswalk(
            crosswalk_csv([("a", gid(10), 0.5), ("a", gid(10), 0.5)]),
            domain_code="ID",
            level=9,
            weight_kind="luas",
        )
    with pytest.raises(CrosswalkRejected, match="expected level 8"):
        validate_crosswalk(crosswalk_csv([("a", gid(10), 1)]), domain_code="ID", level=8, weight_kind="luas")


def test_crosswalk_apply_unknown_id_and_conservation(client):
    name = "ID-L9-aaaaaaaaaaaa"
    rows = [("desa-a", gid(10), 0.5), ("desa-a", gid(11), 0.5), ("desa-b", gid(12), 1.0)]
    payload = {
        "name": name,
        "domain_code": "ID",
        "level": "9",
        "weight_kind": "luas",
    }
    created = client.post("/crosswalks", files={"file": ("crosswalk.csv", crosswalk_csv(rows))}, data=payload)
    assert created.status_code == 201, created.text
    assert created.json()["sources"] == 2
    assert client.post("/crosswalks", files={"file": ("crosswalk.csv", crosswalk_csv(rows))}, data=payload).status_code == 409
    assert client.get(f"/crosswalks/{name}").json()["rows"] == 3

    unknown = client.post(
        f"/crosswalks/{name}/apply",
        files={"file": ("source.csv", b"sumber_id,pop\ndesa-x,3\n")},
        data={"dataset_id": "ID-L9-bbbbbbbbbbbb", "value_columns": "pop", "license": "internal-test", "share_policy": "internal"},
    )
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["unknown_sumber_ids"] == ["desa-x"]

    applied = client.post(
        f"/crosswalks/{name}/apply",
        files={"file": ("source.csv", b"sumber_id,pop\ndesa-a,10\ndesa-b,4\n")},
        data={"dataset_id": "ID-L9-bbbbbbbbbbbb", "value_columns": "pop", "license": "internal-test", "share_policy": "internal"},
    )
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["summary"]["dataset_id"] == "ID-L9-bbbbbbbbbbbb"
    assert body["conservation"]["pop"]["source_sum"] == pytest.approx(14)
    assert body["conservation"]["pop"]["allocated_sum"] == pytest.approx(14)


def test_rollup_totals_and_join(client, monkeypatch):
    monkeypatch.setenv(INTERNAL_ENV, "1")
    first = create_dataset(client, [(gid(10), "2"), (gid(11), "3"), (gid(12), "5")], "population")
    second = create_dataset(client, [(gid(10), "7"), (gid(11), "11")], "population")

    rolled = client.get(f"/datasets/{first}/rollup", params={"level": 8, "sum": "population"})
    assert rolled.status_code == 200, rolled.text
    assert rolled.json()["rows"][0]["population"] == pytest.approx(10)
    assert rolled.json()["rows"][0]["cells"] == 3

    joined = client.post("/query/join", json={"dataset_ids": [first, second], "level": 8, "sum": ["population"]})
    assert joined.status_code == 200, joined.text
    row = joined.json()["rows"][0]
    assert joined.json()["cells_joined"] == 2
    assert row[f"{first}.population"] == pytest.approx(5)
    assert row[f"{second}.population"] == pytest.approx(18)


def test_missing_and_invalid_query_paths(client, monkeypatch):
    monkeypatch.setenv(INTERNAL_ENV, "1")
    assert client.get("/crosswalks/ID-L9-aaaaaaaaaaaa").status_code == 404
    assert client.get("/crosswalks/not-a-name").status_code == 404
    dataset = create_dataset(client, [(gid(10), "2")])
    assert client.get(f"/datasets/{dataset}/rollup", params={"level": 10, "sum": "value"}).status_code == 422
    assert client.get(f"/datasets/{dataset}/rollup", params={"level": 9, "sum": "missing"}).status_code == 422
    assert client.post("/query/join", json={"dataset_ids": [dataset], "level": 9, "sum": ["value"]}).status_code == 422


def test_crosswalk_rejects_unknown_domain_and_out_of_range_level():
    from geosquare_platform.crosswalk import CrosswalkRejected, validate_crosswalk as check

    data = b"sumber_id,gid,bobot\nDESA-001,22,1\n"
    with pytest.raises(CrosswalkRejected, match="domain_code must be one of"):
        check(data, domain_code="XX", level=2, weight_kind="luas")
    with pytest.raises(CrosswalkRejected, match="integer from 0 to 14"):
        check(data, domain_code="ID", level=99, weight_kind="luas")
    assert check(data, domain_code="ID", level=2, weight_kind="luas").summary() == {"sources": 1, "rows": 1, "cells": 1}


def test_rollup_and_join_are_hidden_unless_internal(client, monkeypatch):
    monkeypatch.delenv(INTERNAL_ENV, raising=False)
    first = create_dataset(client, [(gid(10), "2")], "population")
    second = create_dataset(client, [(gid(10), "7")], "population")
    assert client.get(f"/datasets/{first}/rollup", params={"level": 8, "sum": "population"}).status_code == 404
    assert client.post("/query/join", json={"dataset_ids": [first, second], "level": 8, "sum": ["population"]}).status_code == 404
    monkeypatch.setenv(INTERNAL_ENV, "0")
    assert client.get(f"/datasets/{first}/rollup", params={"level": 8, "sum": "population"}).status_code == 404
