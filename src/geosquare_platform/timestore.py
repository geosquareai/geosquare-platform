"""Store and join `cell_time_values` datasets (L7) for the platform.

The rules come from geosquare-v2: `timecube` for labels, intervals and rows, and
`time_store` for the stored files. This module parses the upload and calls them.
Nothing is repaired. The join is internal, like rollup.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from geosquare_v2.errors import CellDatasetError
from geosquare_v2.time_store import read_time_values, write_time_values
from geosquare_v2.timecube import TimeCubeError, relation

from .importer import ImportRejected

TIME_ID_PATTERN = re.compile(r"TIME-[0-9a-f]{12}")
DOMAIN = "ID"
DOMAIN_ID = 1
JOIN_RELATIONS = ("equal", "contained", "contains", "overlap")


def parse_time_csv(data: bytes, value_fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Read `gid,period` plus the value columns. Numeric values must be finite."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImportRejected("file is not UTF-8 text") from exc
    reader = csv.DictReader(io.StringIO(text))
    names = [field["name"] for field in value_fields]
    if reader.fieldnames is None or reader.fieldnames != ["gid", "period", *names]:
        raise ImportRejected(f"columns must be exactly gid, period, {', '.join(names)}")
    rows: list[dict[str, Any]] = []
    for number, record in enumerate(reader, start=2):
        row: dict[str, Any] = {"gid": record["gid"] or "", "period": record["period"] or ""}
        if not row["gid"] or not row["period"]:
            raise ImportRejected(f"row {number}: gid and period are required")
        for field in value_fields:
            cell = record[field["name"]]
            if cell is None or cell == "":
                raise ImportRejected(f"row {number}, column {field['name']!r}: value is empty")
            if field["semantics"] == "CATEGORICAL":
                row[field["name"]] = cell
                continue
            try:
                number_value = float(cell)
            except ValueError as exc:
                raise ImportRejected(f"row {number}, column {field['name']!r}: {cell!r} is not a number") from exc
            if not math.isfinite(number_value):
                raise ImportRejected(f"row {number}, column {field['name']!r}: {cell!r} is not finite")
            row[field["name"]] = number_value
        rows.append(row)
    if not rows:
        raise ImportRejected("file has no rows")
    return rows


def store_time_csv(
    root: Path,
    data: bytes,
    *,
    level: int,
    period_type: str,
    period_start: str,
    period_end: str,
    value_fields: list[dict[str, Any]],
    bucket_hours: int | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Check the upload and write it under `root`. The ID comes from the content and the settings."""
    rows = parse_time_csv(data, value_fields)
    settings = json.dumps(
        {
            "level": level,
            "period_type": period_type,
            "bucket_hours": bucket_hours,
            "period_start": period_start,
            "period_end": period_end,
            "value_fields": value_fields,
        },
        sort_keys=True,
    )
    dataset_id = "TIME-" + hashlib.sha256(data + settings.encode("utf-8")).hexdigest()[:12]
    destination = Path(root) / dataset_id
    if destination.exists():
        raise ImportRejected(f"dataset {dataset_id} already exists")
    try:
        manifest = write_time_values(
            destination,
            rows,
            domain_code=DOMAIN,
            domain_id=DOMAIN_ID,
            level=level,
            period_type=period_type,
            period_start=period_start,
            period_end=period_end,
            value_fields=value_fields,
            bucket_hours=bucket_hours,
            created_at=created_at,
        )
    except CellDatasetError as exc:
        raise ImportRejected(str(exc)) from exc
    return {
        "dataset_id": dataset_id,
        "dataset_type": "cell_time_values",
        "rows": manifest["storage"]["row_count"],
        "period_type": period_type,
        "bucket_hours": bucket_hours,
        "valid": True,
    }


def read_time_dataset(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        return read_time_values(path)
    except CellDatasetError as exc:
        raise ImportRejected(str(exc)) from exc


def join_time(
    left: tuple[dict[str, Any], list[dict[str, Any]]],
    right: tuple[dict[str, Any], list[dict[str, Any]]],
    relation_name: str,
    columns: list[str],
) -> dict[str, Any]:
    """Count left rows that have a same-cell right row in `relation_name`, and sum `columns` over them.

    The relation compares the two UTC intervals. `contained` means the left period lies inside the right period.
    """
    if relation_name not in JOIN_RELATIONS:
        raise ImportRejected(f"relation must be one of {list(JOIN_RELATIONS)}")
    left_manifest, left_rows = left
    right_manifest, right_rows = right
    by_gid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for other in right_rows:
        by_gid[other["gid"]].append(other)
    left_side = (left_manifest["period_type"], left_manifest.get("bucket_hours"))
    right_side = (right_manifest["period_type"], right_manifest.get("bucket_hours"))
    matched: list[dict[str, Any]] = []
    for row in left_rows:
        for other in by_gid.get(row["gid"], []):
            try:
                found = relation((row["period"], *left_side), (other["period"], *right_side))
            except TimeCubeError as exc:
                raise ImportRejected(str(exc)) from exc
            if found == relation_name:
                matched.append(row)
                break
    return {
        "matched_rows": len(matched),
        "sums": {column: sum(float(row[column]) for row in matched) for column in columns},
    }
