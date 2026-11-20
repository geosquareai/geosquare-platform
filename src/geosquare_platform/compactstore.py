"""Store and read `cell_compact_set` datasets (E13) for the platform.

The writer and reader are geosquare-v2's `compact_store`. Every write and every
read is checked against the compact-set rules. This module parses the upload,
maps errors to upload errors, and keeps the platform's dataset IDs.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from pathlib import Path
from typing import Any

from geosquare_v2.compact_store import read_compact_set, write_compact_set
from geosquare_v2.errors import CellDatasetError

from .cellstore import DatasetExists
from .importer import ImportRejected

COMPACT_ID_PATTERN = re.compile(r"ID-COMPACT-[0-9a-f]{12}")
DOMAIN_CODE = "ID"
DOMAIN_ID = 1
COLUMNS = ["gid", "level"]


def parse_compact_csv(data: bytes) -> list[tuple[str, int]]:
    """Read `gid,level` rows. Nothing is repaired or guessed."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImportRejected("file is not UTF-8 text") from exc
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != COLUMNS:
        raise ImportRejected("columns must be exactly gid,level")
    rows: list[tuple[str, int]] = []
    for number, record in enumerate(reader, start=2):
        gid = record.get("gid") or ""
        raw_level = record.get("level") or ""
        if not gid:
            raise ImportRejected(f"row {number}: GID is empty")
        if not raw_level.isdigit():
            raise ImportRejected(f"row {number}: level must be a whole number")
        rows.append((gid, int(raw_level)))
    if not rows:
        raise ImportRejected("file has no rows")
    return rows


def store_compact_csv(root: Path, data: bytes, *, created_at: str | None = None) -> dict[str, Any]:
    """Check a compact set and write it under `root`. The ID comes from the file content."""
    rows = parse_compact_csv(data)
    dataset_id = "ID-COMPACT-" + hashlib.sha256(data).hexdigest()[:12]
    destination = Path(root) / dataset_id
    if destination.exists():
        raise DatasetExists(f"dataset {dataset_id} already exists")
    try:
        manifest = write_compact_set(
            destination,
            rows,
            domain_code=DOMAIN_CODE,
            domain_id=DOMAIN_ID,
            created_at=created_at,
        )
    except CellDatasetError as exc:
        raise ImportRejected(str(exc)) from exc
    return {
        "dataset_id": dataset_id,
        "dataset_type": "cell_compact_set",
        "rows": manifest["storage"]["row_count"],
        "levels_present": manifest["levels_present"],
        "valid": True,
    }


def read_compact(path: Path) -> list[tuple[str, int]]:
    """Read a stored compact set. A stored set that breaks a rule is rejected."""
    try:
        _, rows = read_compact_set(path)
    except CellDatasetError as exc:
        raise ImportRejected(str(exc)) from exc
    return list(rows)
