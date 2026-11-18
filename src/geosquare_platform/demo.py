"""Phase 2 demo. Runs the pass criteria on one CSV through the platform API.

Criteria covered here:
1. Upload a CSV that has a GID column.
2. The GID column and its level are detected.
4. The data is stored as cells with a valid manifest.

The map criterion (3) is in Phase 3.

Usage:
    uv run python -m geosquare_platform.demo cells.csv --numeric share
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from fastapi.testclient import TestClient

from .app import DATA_ENV, app


class DemoFailed(RuntimeError):
    """A step did not pass. The message says which step and why."""


def _value_fields(numeric: Sequence[str], categorical: Sequence[str]) -> list[dict]:
    fields = [{"name": name, "logical_type": "numeric", "semantics": "MEASUREMENT", "unit": None} for name in numeric]
    fields += [
        {"name": name, "logical_type": "categorical", "semantics": "CATEGORICAL", "unit": None} for name in categorical
    ]
    return fields


def run(csv_path: Path, data_root: Path, *, numeric: Sequence[str] = (), categorical: Sequence[str] = ()) -> dict:
    """Run the steps in order and print each result. Raise DemoFailed on the first failure."""
    csv_path = Path(csv_path)
    data = csv_path.read_bytes()
    fields = _value_fields(numeric, categorical)
    if not fields:
        raise DemoFailed("give at least one value column with --numeric or --categorical")

    previous = os.environ.get(DATA_ENV)
    os.environ[DATA_ENV] = str(data_root)
    try:
        client = TestClient(app)

        print(f"Upload: {csv_path.name} ({len(data):,} bytes)")
        detected = client.post("/import/analyse", files={"file": (csv_path.name, data)}).json()
        if not detected["ok"]:
            raise DemoFailed(f"detection: {detected['status']}: {detected['reason']}")

        print(
            f"Detected: GID column {detected['key_column']!r}, "
            f"level {detected['level']}, {detected['rows']:,} rows"
        )

        created = client.post(
            "/datasets",
            files={"file": (csv_path.name, data)},
            data={"value_fields": json.dumps(fields)},
        )
        if created.status_code != 201:
            raise DemoFailed(f"store: HTTP {created.status_code}: {created.text}")
        stored = created.json()
        print(
            f"Stored: {stored['dataset_id']}, {stored['rows']:,} cells, "
            f"value fields {', '.join(stored['value_fields'])}"
        )
        if stored["dropped_columns"]:
            print(f"   Not stored (not listed as value fields): {', '.join(stored['dropped_columns'])}")

        checked = client.get(f"/datasets/{stored['dataset_id']}")
        if checked.status_code != 200 or checked.json()["valid"] is not True:
            raise DemoFailed(f"manifest check: HTTP {checked.status_code}: {checked.text}")
        print("Manifest: valid")
        print("PASS")
        return stored
    finally:
        if previous is None:
            os.environ.pop(DATA_ENV, None)
        else:
            os.environ[DATA_ENV] = previous


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", type=Path, help="CSV file with a GID column")
    parser.add_argument("--data-root", type=Path, default=Path("data/demo/cells"), help="where datasets are stored")
    parser.add_argument("--numeric", action="append", default=[], help="value column that holds numbers")
    parser.add_argument("--categorical", action="append", default=[], help="value column that holds text labels")
    args = parser.parse_args(argv)
    try:
        run(args.csv, args.data_root, numeric=args.numeric, categorical=args.categorical)
    except DemoFailed as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
