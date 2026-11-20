from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from geosquare_platform.app import INTERNAL_ENV, app
from geosquare_v2.codec import canonical_to_gid

GID_A = canonical_to_gid(9, 0, 0)
GID_B = canonical_to_gid(9, 0, 1)
DAILY_FIELDS = [{"name": "rain_mm", "semantics": "MEASUREMENT", "time_aggregation": "mean"}]
ANNUAL_FIELDS = [{"name": "population", "semantics": "COUNT"}]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GSQ_PLATFORM_DATA", str(tmp_path / "data"))
    return TestClient(app)


def upload(client, body: str, fields, *, period_type, period_start, period_end, bucket_hours=None):
    data = {
        "level": "9",
        "period_type": period_type,
        "period_start": period_start,
        "period_end": period_end,
        "value_fields": json.dumps(fields),
    }
    if bucket_hours is not None:
        data["bucket_hours"] = str(bucket_hours)
    names = [field["name"] for field in fields]
    header = ",".join(["gid", "period", *names])
    response = client.post(
        "/time-datasets",
        files={"file": ("values.csv", f"{header}\n{body}".encode(), "text/csv")},
        data=data,
    )
    return response


def daily_body() -> str:
    return "\n".join(
        [
            f"{GID_A},2026-11-01,12.5",
            f"{GID_A},2026-11-02,0.0",
            f"{GID_B},2026-11-01,3.0",
        ]
    ) + "\n"


def test_daily_upload_is_stored(client):
    response = upload(client, daily_body(), DAILY_FIELDS, period_type="day", period_start="2026-11-01", period_end="2026-12-01")
    assert response.status_code == 201, response.text
    assert response.json()["rows"] == 3
    assert response.json()["dataset_id"].startswith("TIME-")


def test_bad_period_is_rejected(client):
    body = f"{GID_A},2026-12-01,1.0\n"
    response = upload(client, body, DAILY_FIELDS, period_type="day", period_start="2026-11-01", period_end="2026-12-01")
    assert response.status_code == 422
    assert "outside" in response.json()["detail"]


def test_join_counts_daily_rows_inside_a_year(client, monkeypatch):
    monkeypatch.setenv(INTERNAL_ENV, "1")
    daily = upload(client, daily_body(), DAILY_FIELDS, period_type="day", period_start="2026-11-01", period_end="2026-12-01").json()
    annual_body = f"{GID_A},2026,500\n"
    annual = upload(client, annual_body, ANNUAL_FIELDS, period_type="year", period_start="2026", period_end="2027")
    assert annual.status_code == 201, annual.text

    joined = client.post(
        "/query/time-join",
        json={"left_id": daily["dataset_id"], "right_id": annual.json()["dataset_id"], "relation": "contained", "sum": ["rain_mm"]},
    )

    assert joined.status_code == 200, joined.text
    assert joined.json()["matched_rows"] == 2
    assert joined.json()["sums"] == {"rain_mm": pytest.approx(12.5)}


def test_time_join_is_hidden_unless_internal(client, monkeypatch):
    monkeypatch.delenv(INTERNAL_ENV, raising=False)
    daily = upload(client, daily_body(), DAILY_FIELDS, period_type="day", period_start="2026-11-01", period_end="2026-12-01").json()
    response = client.post(
        "/query/time-join",
        json={"left_id": daily["dataset_id"], "right_id": daily["dataset_id"], "relation": "equal", "sum": ["rain_mm"]},
    )
    assert response.status_code == 404


def test_hour_bucket_upload_and_unknown_relation(client, monkeypatch):
    monkeypatch.setenv(INTERNAL_ENV, "1")
    body = f"{GID_A},2026-11-19T00,5.0\n{GID_A},2026-11-19T04,7.0\n"
    hours = upload(
        client,
        body,
        [{"name": "rain_mm", "semantics": "MEASUREMENT", "time_aggregation": "mean"}],
        period_type="hour",
        period_start="2026-11-19T00",
        period_end="2026-11-20T00",
        bucket_hours=4,
    )
    assert hours.status_code == 201, hours.text
    response = client.post(
        "/query/time-join",
        json={"left_id": hours.json()["dataset_id"], "right_id": hours.json()["dataset_id"], "relation": "touches", "sum": ["rain_mm"]},
    )
    assert response.status_code == 422
