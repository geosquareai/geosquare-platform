"""Store and read `cell_compact_set` datasets (E13).

A compact set is a list of `gid, level` rows. The rules live in
`geosquare_engine.compact`. This module checks the rows with those rules and
writes one manifest and one Parquet file.

It does not use the CellDataset reader from geosquare-v2 yet. That package does
not know this dataset type. Add it to geosquare-v2 before a release.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from geosquare_engine.compact import validate_compact_set

from .cellstore import DatasetExists, generator_label
from .importer import ImportRejected

COMPACT_ID_PATTERN = re.compile(r"ID-COMPACT-[0-9a-f]{12}")
DOMAIN = "ID"
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


def check_rows(rows: list[tuple[str, int]]) -> None:
    """Apply the cell_compact_set rules from geosquare-engine. Any error rejects the file."""
    try:
        validate_compact_set(rows)
    except ValueError as exc:  # CompactSetError and invalid GIDs are both ValueError
        raise ImportRejected(str(exc)) from exc


def _manifest(rows: list[tuple[str, int]], created_at: str) -> dict[str, Any]:
    levels = sorted({level for _, level in rows})
    return {
        "manifest_version": "1.0",
        "dataset_type": "cell_compact_set",
        "grid_system": "geosquare",
        "grid_version": "v2",
        "domain_code": DOMAIN,
        "domain_id": DOMAIN_ID,
        "levels_present": levels,
        "identity": {"gid_column": "gid", "level_column": "level", "unique_gid": True, "mixed_levels": True},
        "storage": {
            "parquet_compression": "zstd",
            "sorted_by": ["gid"],
            "row_count": len(rows),
            "partitioning": [],
        },
        "provenance": {
            "created_at": created_at,
            "created_by": generator_label(),
            "generator": "compact_csv_import",
            "rules": "geosquare-v2 docs/cell-dataset-contract.md section 2; geosquare-engine compact helpers",
        },
    }


def store_compact_csv(root: Path, data: bytes, *, created_at: str | None = None) -> dict[str, Any]:
    """Check a compact set and write it under `root`. The ID comes from the file content."""
    rows = parse_compact_csv(data)
    check_rows(rows)
    dataset_id = "ID-COMPACT-" + hashlib.sha256(data).hexdigest()[:12]
    destination = Path(root) / dataset_id
    if destination.exists():
        raise DatasetExists(f"dataset {dataset_id} already exists")

    stored = sorted(rows)
    table = pa.table(
        {
            "gid": pa.array([gid for gid, _ in stored], type=pa.string()),
            "level": pa.array([level for _, level in stored], type=pa.int8()),
        }
    )
    stamp = created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = _manifest(stored, stamp)
    staging = destination.with_name(destination.name + ".tmp")
    (staging / "data").mkdir(parents=True, exist_ok=False)
    pq.write_table(table, staging / "data" / "compact.parquet", compression="zstd")
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    staging.rename(destination)
    return {
        "dataset_id": dataset_id,
        "dataset_type": "cell_compact_set",
        "rows": len(rows),
        "levels_present": manifest["levels_present"],
        "valid": True,
    }


def read_compact(path: Path) -> list[tuple[str, int]]:
    """Read a stored compact set and check it again. A stored file that breaks the rules is rejected."""
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImportRejected("manifest is missing or not JSON") from exc
    if manifest.get("dataset_type") != "cell_compact_set":
        raise ImportRejected("dataset is not a cell_compact_set")
    table = pq.read_table(path / "data" / "compact.parquet")
    rows = list(zip(table.column("gid").to_pylist(), table.column("level").to_pylist()))
    if len(rows) != manifest["storage"]["row_count"]:
        raise ImportRejected("row count does not match the manifest")
    check_rows(rows)
    return rows
