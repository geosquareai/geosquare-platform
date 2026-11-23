"""Exact coverage of each dataset: its canonical compact cell set.

The set is built once from the `gid` column of the dataset's Parquet files, then cached as
`<data root>/coverage/<dataset id>.json`. Datasets are never overwritten, so the cache cannot go stale.

This cache is a platform index. It is not part of the dataset contract, and the dataset readers
do not read it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pyarrow.parquet as pq
from geosquare_engine.compact import canonicalize

COVERAGE_DIR = "coverage"


def coverage_file(root: Path, dataset_id: str) -> Path:
    return root / COVERAGE_DIR / f"{dataset_id}.json"


def dataset_gids(dataset_dir: Path) -> list[str]:
    """The GIDs stored in a dataset. Only the `gid` column is read."""
    return pq.read_table(dataset_dir / "data", columns=["gid"]).column("gid").to_pylist()


def compact_coverage(root: Path, dataset_id: str) -> tuple[str, ...]:
    """The canonical compact cell set of a stored dataset. Built on first use, then read from the cache."""
    path = coverage_file(root, dataset_id)
    if path.is_file():
        return tuple(json.loads(path.read_text(encoding="utf-8"))["gids"])
    compact = canonicalize(dataset_gids(root / dataset_id))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"dataset_id": dataset_id, "gids": list(compact)}), encoding="utf-8")
    os.replace(temporary, path)
    return compact
