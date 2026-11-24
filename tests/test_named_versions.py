from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

import geosquare_engine as gs
import pytest
from fastapi.testclient import TestClient

from geosquare_platform.app import app

VALUE_FIELDS_JSON = (
    '[{"name": "share", "logical_type": "numeric", "semantics": "MEASUREMENT", "unit": "ratio"},'
    ' {"name": "zone", "logical_type": "categorical", "semantics": "CATEGORICAL", "unit": null}]'
)
GOV = {"license": "internal-test", "share_policy": "internal"}


def csv(rows) -> bytes:
    lines = ["gid,share,zone"] + [",".join(row) for row in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


V1 = csv([(gs.encode(9, 10, 3), "0.25", "A"), (gs.encode(9, 11, 3), "1", "B")])
V2 = csv([(gs.encode(9, 10, 3), "0.75", "A"), (gs.encode(9, 12, 3), "0.5", "B"), (gs.encode(9, 13, 3), "0", "A")])


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GSQ_PLATFORM_DATA", str(tmp_path / "cells"))
    return TestClient(app)


def upload(client, data: bytes, name: str | None = "kyl-share"):
    form = {"value_fields": VALUE_FIELDS_JSON, **GOV}
    if name is not None:
        form["name"] = name
    return client.post("/datasets", files={"file": ("cells.csv", data)}, data=form)


def test_versions_keep_their_rows_and_hash(client):
    first = upload(client, V1)
    second = upload(client, V2)
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["version"] == 1 and second.json()["version"] == 2

    latest = client.get("/named/kyl-share").json()
    assert latest["version"] == 2 and latest["rows"] == 3

    old = client.get("/named/kyl-share/versions/1")
    assert old.status_code == 200, old.text
    old_body = old.json()
    assert old_body["version"] == 1 and old_body["rows"] == 2
    old_hash = old_body["manifest"]["storage"]["data_files"][0]["content_sha256"]
    assert len(old_hash) == 64
    assert old_hash != latest["manifest"]["storage"]["data_files"][0]["content_sha256"]

    listing = client.get("/named/kyl-share/versions").json()
    assert listing["latest"] == 2
    assert [item["version"] for item in listing["versions"]] == [1, 2]
    assert [item["rows"] for item in listing["versions"]] == [2, 3]
    assert listing["versions"][0]["content_sha256"] == old_hash
    same_as_v1 = upload(client, V1, name=None).json()["dataset_id"]
    legacy_hash = client.get(f"/datasets/{same_as_v1}").json()["manifest"]["storage"]["data_files"][0]["content_sha256"]
    assert old_hash == legacy_hash


def test_same_content_as_an_earlier_version_is_refused(client):
    assert upload(client, V1).status_code == 201
    assert upload(client, V2).status_code == 201
    again = upload(client, V1)
    assert again.status_code == 409
    assert client.get("/named/kyl-share").json()["version"] == 2


def test_unknown_name_version_and_bad_name(client):
    assert upload(client, V1).status_code == 201
    assert client.get("/named/nothing-here").status_code == 404
    assert client.get("/named/kyl-share/versions/9").status_code == 404
    assert client.get("/named/Bad_Name!").status_code == 404
    assert upload(client, V2, name="Bad Name").status_code == 422


def test_legacy_dataset_without_a_name_still_loads(client):
    legacy = upload(client, V1, name=None)
    assert legacy.status_code == 201
    dataset_id = legacy.json()["dataset_id"]
    assert "version" not in legacy.json()
    assert client.get(f"/datasets/{dataset_id}").status_code == 200
    assert upload(client, V1, name=None).status_code == 409


def test_catalog_lists_latest_named_version_and_legacy(client):
    legacy = upload(client, V1, name=None).json()["dataset_id"]
    assert upload(client, V1).status_code == 201
    assert upload(client, V2).status_code == 201

    rows = client.get("/catalog").json()
    named = [row for row in rows if row.get("name") == "kyl-share"]
    assert len(named) == 1
    assert named[0]["dataset_id"] == "kyl-share" and named[0]["version"] == 2
    assert named[0]["row_count"] == 3
    assert [row["dataset_id"] for row in rows if "name" not in row] == [legacy]

    gid_only_in_v2 = gs.encode(9, 13, 3)
    found = client.get("/catalog", params={"gid": gid_only_in_v2}).json()
    assert [row["name"] for row in found] == ["kyl-share"]
    assert client.get(f"/datasets/{legacy}").status_code == 200


def test_catalog_skips_non_canonical_version_folders(client):
    assert upload(client, V1).status_code == 201
    folder = client.get("/named/kyl-share").json()
    version_folder = Path(os.environ["GSQ_PLATFORM_DATA"]) / "named" / "kyl-share"
    for odd in ("v0", "v01"):
        shutil.copytree(version_folder / "v1", version_folder / odd)

    listing = client.get("/named/kyl-share/versions").json()
    assert [item["version"] for item in listing["versions"]] == [1]
    assert folder["version"] == 1


def test_concurrent_uploads_to_one_name_get_201_or_409_and_no_overwrite(client):
    threads_count = 6
    barrier = threading.Barrier(threads_count)
    statuses: list[int] = []
    created: list[int] = []

    def worker(index: int) -> None:
        thread_client = TestClient(app, raise_server_exceptions=False)
        data = csv([(gs.encode(9, 10, 3), f"0.{index + 1}", "A"), (gs.encode(9, 11, 3), "1", "B")])
        barrier.wait()
        response = upload(thread_client, data)
        statuses.append(response.status_code)
        if response.status_code == 201:
            created.append(response.json()["version"])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(threads_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert set(statuses) <= {201, 409}, statuses
    assert len(created) == statuses.count(201) >= 1
    assert len(set(created)) == len(created)
    listing = client.get("/named/kyl-share/versions").json()
    assert sorted(item["version"] for item in listing["versions"]) == sorted(created)
    assert len({item["content_sha256"] for item in listing["versions"]}) == len(created)
