"""L15: manifest 1.1 written by the platform, governance intake, and catalog metadata."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import geosquare_engine as gs
import pytest
from fastapi.testclient import TestClient
from geosquare_v2 import GeosquareGrid, read_cell_dataset

from geosquare_platform.app import app
from geosquare_platform.cellstore import Governance, is_stale, parse_value_fields, read_dataset, store_csv
from geosquare_platform.map_routes import _cell_centre_wgs84, _wgs84_transformer
from geosquare_platform.registry import load_registry

FORM = {"license": "internal-test", "share_policy": "internal"}
FIELDS = json.dumps([{"name": "share", "logical_type": "numeric", "semantics": "MEASUREMENT", "unit": "ratio"}])


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    root = tmp_path / "cells"
    monkeypatch.setenv("GSQ_PLATFORM_DATA", str(root))
    return root


def gid(x: int, y: int = 3) -> str:
    return gs.encode(9, x, y)


def csv_rows(rows, column="share") -> bytes:
    return (f"gid,{column}\n" + "\n".join(f"{cell},{value}" for cell, value in rows) + "\n").encode()


def upload(client: TestClient, rows, *, form=FORM, fields=FIELDS, column="share"):
    return client.post(
        "/datasets",
        files={"file": ("cells.csv", csv_rows(rows, column), "text/csv")},
        data={"value_fields": fields, **form},
    )


def stored_names(root) -> list:
    return sorted(root.iterdir()) if root.exists() else []


def test_upload_without_licence_is_refused(data_root):
    response = upload(TestClient(app), [(gid(10), "0.2")], form={"share_policy": "internal"})

    assert response.status_code == 422
    assert "license is required" in response.text
    assert stored_names(data_root) == []


def test_upload_without_sharing_policy_is_refused(data_root):
    response = upload(TestClient(app), [(gid(10), "0.2")], form={"license": "internal-test"})

    assert response.status_code == 422
    assert "share_policy is required" in response.text
    assert stored_names(data_root) == []


def test_unknown_sharing_policy_is_refused(data_root):
    response = upload(TestClient(app), [(gid(10), "0.2")], form={"license": "x", "share_policy": "open"})

    assert response.status_code == 422
    assert stored_names(data_root) == []


def test_manifest_is_version_1_1_with_governance_and_machine_fields(data_root):
    client = TestClient(app)
    response = upload(
        client,
        [(gid(10), "0.2"), (gid(11), "0.7")],
        form={**FORM, "owner": "data team", "refresh": "monthly", "title": "Test"},
    )
    assert response.status_code == 201, response.text
    dataset_id = response.json()["dataset_id"]

    manifest = json.loads((data_root / dataset_id / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == "1.1"
    assert manifest["governance"] == {
        "title": "Test",
        "owner": "data team",
        "source_name": None,
        "license": "internal-test",
        "share_policy": "internal",
        "refresh": "monthly",
    }
    assert manifest["extent"]["compact_set_path"] is None
    field = manifest["value_fields"][0]
    assert field["space_aggregation"] == "median"
    assert field["space_weight"] is None
    assert field["aggregation_rule"] is None
    assert field["null_share"] == 0.0
    assert (field["min_value"], field["max_value"]) == (0.2, 0.7)
    assert manifest["provenance"]["spec_version"] == "2.0"
    assert manifest["provenance"]["derived_from"] == []
    assert len(manifest["storage"]["data_files"][0]["content_sha256"]) == 64

    # The geosquare-v2 reader checks the hash, the statistics, the extent and the governance shape.
    assert read_cell_dataset(data_root / dataset_id, registry=load_registry()).manifest.manifest_version == "1.1"


def test_extent_covers_every_uploaded_cell(data_root):
    registry = load_registry()
    rows = [(gid(10), "0.2"), (gid(11), "0.7"), (gid(12, 4), "0.5")]
    fields = parse_value_fields(json.loads(FIELDS))
    stored = store_csv(
        registry,
        data_root,
        csv_rows(rows),
        value_fields=fields,
        governance=Governance(license="internal-test", share_policy="internal"),
    )

    west, south, east, north = read_dataset(registry, stored.path)["manifest"]["extent"]["bbox_wgs84"]
    profile = registry.get("ID")
    grid, transformer = GeosquareGrid(profile), _wgs84_transformer(profile.crs_wkt2)
    for cell, _ in rows:
        lon, lat = _cell_centre_wgs84(grid, cell, transformer)
        assert west <= lon <= east and south <= lat <= north


@pytest.mark.parametrize(
    ("semantics", "logical", "expected", "weight"),
    [
        ("COUNT", "numeric", "sum", None),
        ("TOTAL", "numeric", "sum", None),
        ("DENSITY", "numeric", "weighted_mean", "area"),
        ("MEASUREMENT", "numeric", "median", None),
        ("RATE", "numeric", "none", None),
        ("CATEGORICAL", "categorical", "majority_weighted", None),
    ],
)
def test_default_rollup_rule_follows_the_semantics(semantics, logical, expected, weight):
    field = parse_value_fields([{"name": "x", "logical_type": logical, "semantics": semantics}])[0]

    assert (field.space_aggregation, field.space_weight) == (expected, weight)


def test_rate_with_mean_needs_a_weight(data_root):
    fields = json.dumps(
        [{"name": "rate", "logical_type": "numeric", "semantics": "RATE", "space_aggregation": "weighted_mean"}]
    )
    response = upload(TestClient(app), [(gid(10), "0.12")], fields=fields, column="rate")

    assert response.status_code == 422
    assert "space_weight" in response.text
    assert stored_names(data_root) == []


def test_catalog_shows_governance_extent_and_staleness(data_root):
    registry = load_registry()
    fields = parse_value_fields(json.loads(FIELDS))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    monthly = Governance(license="internal-test", share_policy="internal", refresh="monthly")
    store_csv(registry, data_root, csv_rows([(gid(10), "0.2")]), value_fields=fields, governance=monthly, created_at=now)
    store_csv(
        registry,
        data_root,
        csv_rows([(gid(11), "0.3")]),
        value_fields=fields,
        governance=monthly,
        created_at="2026-01-01T00:00:00Z",
    )

    entries = TestClient(app).get("/catalog").json()

    assert len(entries) == 2
    assert all(entry["governance"]["share_policy"] == "internal" for entry in entries)
    assert all(entry["extent"]["bbox_wgs84"] is not None for entry in entries)
    by_date = {entry["created_at"]: entry["stale"] for entry in entries}
    assert by_date == {now: False, "2026-01-01T00:00:00Z": True}


def test_is_stale_follows_the_refresh_rhythm():
    now = datetime(2026, 11, 23, tzinfo=timezone.utc)

    assert is_stale("2026-01-01T00:00:00Z", "monthly", now=now) is True
    assert is_stale("2026-11-20T00:00:00Z", "daily", now=now) is True
    assert is_stale("2026-11-22T00:00:00Z", "daily", now=now) is False
    assert is_stale("2020-01-01T00:00:00Z", "once", now=now) is False
    assert is_stale("2020-01-01T00:00:00Z", None, now=now) is None


def test_catalog_bbox_keeps_only_datasets_that_touch_the_box(data_root):
    registry = load_registry()
    fields = parse_value_fields(json.loads(FIELDS))
    store_csv(
        registry,
        data_root,
        csv_rows([(gid(10), "0.2")]),
        value_fields=fields,
        governance=Governance(license="internal-test", share_policy="internal"),
    )
    # A folder with an unreadable manifest is listed without bbox, and never matches a box.
    broken = data_root / "ID-L9-broken00000"
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text("{", encoding="utf-8")
    client = TestClient(app)

    everything = client.get("/catalog").json()
    assert len(everything) == 2

    own = next(entry for entry in everything if entry.get("extent"))["extent"]["bbox_wgs84"]
    min_lon, min_lat, max_lon, max_lat = own
    touching = client.get("/catalog", params={"bbox": f"{min_lon},{min_lat},{max_lon},{max_lat}"}).json()
    assert [entry["dataset_id"] for entry in touching] == [
        entry["dataset_id"] for entry in everything if entry.get("extent")
    ]

    away = client.get(
        "/catalog",
        params={"bbox": f"{min_lon + 5},{min_lat + 5},{max_lon + 5},{max_lat + 5}"},
    ).json()
    assert away == []


def test_catalog_bbox_rejects_a_malformed_box(data_root):
    response = TestClient(app).get("/catalog", params={"bbox": "1,2,3"})

    assert response.status_code == 422
