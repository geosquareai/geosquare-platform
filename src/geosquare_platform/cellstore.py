"""Step 2 of the platform: store a checked GID table as a cell dataset with a manifest.

The layout is the cell dataset contract in geosquare-v2 (format 1.0): one directory with
`manifest.json` and `data/*.parquet`. The reference writer validates the table and the
manifest, and reading a dataset back validates it again.

This version writes one Parquet file per dataset. Partitioning by GID prefix is not used yet.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pyarrow as pa
from geosquare_v2 import CellDataset, DomainRegistry
from geosquare_v2.errors import GeosquareError

from .importer import ImportRejected, analyse, read_csv

DOMAIN = "ID"
REGISTRY_VERSION = "2.0.0"  # the registry loader accepts only this version
DATASET_ID_PATTERN = re.compile(r"ID-L(\d{1,2})-[0-9a-f]{12}")


def generator_label() -> str:
    """Names the platform and the libraries that wrote the dataset. The manifest keeps it."""
    return (
        f"geosquare-platform {version('geosquare-platform')} "
        f"(geosquare-engine {version('geosquare-engine')}, geosquare-v2 {version('geosquare-v2')})"
    )

# Logical type from the upload -> Parquet type. The reference checks the semantics for each type.
_PARQUET_TYPES = {"numeric": "float64", "categorical": "string"}
_FIELD_KEYS = frozenset(("name", "logical_type", "semantics", "unit"))
_PLAIN_NUMBER = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?")


class DatasetExists(GeosquareError):
    """A dataset with the same content and value fields is already stored."""


@dataclass(frozen=True)
class ValueField:
    """One user value column. The user states its meaning. The platform does not guess it."""

    name: str
    logical_type: str  # "numeric" or "categorical"
    semantics: str  # for example MEASUREMENT, TOTAL, or CATEGORICAL
    unit: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "logical_type": self.logical_type, "semantics": self.semantics, "unit": self.unit}


@dataclass(frozen=True)
class StoredDataset:
    dataset_id: str
    path: Path
    level: int
    rows: int
    unique_cells: int
    value_fields: tuple[str, ...]
    dropped_columns: tuple[str, ...]

    def summary(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "level": self.level,
            "rows": self.rows,
            "unique_cells": self.unique_cells,
            "value_fields": list(self.value_fields),
            "dropped_columns": list(self.dropped_columns),
            "valid": True,
        }


def parse_value_fields(raw: Sequence[Any]) -> list[ValueField]:
    """Check the value field list from the upload. Nothing is repaired or guessed."""
    if not raw:
        raise ImportRejected("value_fields must list at least one column")
    fields: list[ValueField] = []
    names: set[str] = set()
    for index, item in enumerate(raw):
        where = f"value_fields[{index}]"
        if not isinstance(item, Mapping):
            raise ImportRejected(f"{where} must be an object")
        extra = sorted(set(item) - _FIELD_KEYS)
        if extra:
            raise ImportRejected(f"{where} has unknown keys {extra}")
        name, logical, semantics = item.get("name"), item.get("logical_type"), item.get("semantics")
        if not all(isinstance(value, str) and value for value in (name, logical, semantics)):
            raise ImportRejected(f"{where} needs non-empty text for name, logical_type and semantics")
        if logical not in _PARQUET_TYPES:
            raise ImportRejected(f"{where}.logical_type must be one of {sorted(_PARQUET_TYPES)}")
        unit = item.get("unit")
        if unit is not None and not isinstance(unit, str):
            raise ImportRejected(f"{where}.unit must be text or null")
        if name in names:
            raise ImportRejected(f"{where}.name {name!r} is listed twice")
        names.add(name)
        fields.append(ValueField(name, logical, semantics, unit))
    return fields


def _parse_number(column: str, row: int, text: str | None) -> float:
    if text is None or not _PLAIN_NUMBER.fullmatch(text):
        raise ImportRejected(f"row {row}, column {column!r}: {text!r} is not a plain number")
    value = float(text)
    if not math.isfinite(value):
        raise ImportRejected(f"row {row}, column {column!r}: {text!r} is not finite")
    return value


def build_table(columns: Mapping[str, list[str | None]], key_column: str, fields: Sequence[ValueField]) -> pa.Table:
    """Build the Arrow table. The GID column is renamed to `gid`. Values are typed, never trimmed."""
    gids = columns[key_column]
    for row, gid in enumerate(gids, start=1):
        if gid is None:
            raise ImportRejected(f"row {row}: GID is empty")
    arrays: dict[str, pa.Array] = {"gid": pa.array(gids, type=pa.string())}
    for field in fields:
        if field.name not in columns:
            raise ImportRejected(f"value column {field.name!r} is not in the file")
        if field.name == key_column:
            raise ImportRejected(f"value column {field.name!r} is the GID column")
        cells = columns[field.name]
        if field.logical_type == "numeric":
            values = [_parse_number(field.name, row, cell) for row, cell in enumerate(cells, start=1)]
        else:
            for row, cell in enumerate(cells, start=1):
                if cell is None:
                    raise ImportRejected(f"row {row}, column {field.name!r}: value is empty")
            values = cells
        arrays[field.name] = pa.array(values, type=pa.float64() if field.logical_type == "numeric" else pa.string())
    return pa.table(arrays)


def build_manifest(
    registry: DomainRegistry,
    *,
    level: int,
    fields: Sequence[ValueField],
    row_count: int,
    created_at: str,
) -> dict[str, Any]:
    """Manifest for a `cell_values` dataset: one row per cell, GIDs from the file."""
    profile = registry.get(DOMAIN)
    return {
        "manifest_version": "1.0",
        "dataset_type": "cell_values",
        "grid_system": "geosquare",
        "grid_version": "v2",
        "domain_code": profile.domain_code,
        "domain_id": profile.domain_id,
        "level": level,
        "profile_version": profile.profile_version,
        "registry_version": REGISTRY_VERSION,
        "value_fields": [
            {
                "name": field.name,
                "parquet_type": _PARQUET_TYPES[field.logical_type],
                "logical_type": field.logical_type,
                "semantics": field.semantics,
                "unit": field.unit,
                "nullable": False,
                "aggregation_rule": "sum" if field.semantics == "COUNT" else None,
            }
            for field in fields
        ],
        "identity": {
            "gid_column": "gid",
            "uri_prefix": f"geosquare:v2:{profile.domain_code}:",
            "packed_id_column": None,
            "x_idx_column": None,
            "y_idx_column": None,
            "unique_gid": True,
        },
        "coverage": {
            "assignment_method": "given_gid",
            "boundary_policy": None,
            "boundary_sha256": None,
            "coverage_mode": None,
            "min_coverage": None,
            "length_mode": None,
        },
        "source": {
            "format": "csv",
            "geometry_type": None,
            "source_crs": None,
            "source_id_column": None,
            "source_dataset": None,
            "source_row_count": row_count,
        },
        "storage": {
            "parquet_compression": "zstd",
            "geometry_column": None,
            "geometry_format": None,
            "geometry_crs": None,
            "partitioning": [],
            "sorted_by": ["gid"],
            "row_count": row_count,
            "unique_cell_count": row_count,
        },
        "provenance": {
            "created_at": created_at,
            "created_by": generator_label(),
            "generator": "csv_import",
            "aggregation_rule": None,
            "value_semantics": None,
            "notes": "spec 2.0; one Parquet file; partitioning not used",
        },
    }


def store_csv(
    registry: DomainRegistry,
    root: Path,
    data: bytes,
    *,
    value_fields: Sequence[ValueField],
    confirm_digit_gid: bool = False,
    created_at: str | None = None,
    dataset_id: str | None = None,
) -> StoredDataset:
    """Detect the GID column, build the cell table, validate it, and write it under `root`."""
    report = analyse(data, confirm_digit_gid=confirm_digit_gid)
    if not report.ok:
        raise ImportRejected(f"{report.status}: {report.reason}")

    columns = read_csv(data)
    table = build_table(columns, report.key_column, value_fields)
    manifest = build_manifest(
        registry,
        level=report.level,
        fields=value_fields,
        row_count=table.num_rows,
        created_at=created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    # The id comes from the content, the value field list, and the level unless a
    # caller supplies a validated id for an explicit derived dataset.
    identity = hashlib.sha256(data + json.dumps([f.as_dict() for f in value_fields], sort_keys=True).encode())
    generated_id = f"{DOMAIN}-L{report.level}-{identity.hexdigest()[:12]}"
    if dataset_id is None:
        dataset_id = generated_id
    elif not DATASET_ID_PATTERN.fullmatch(dataset_id):
        raise ImportRejected("dataset_id does not match the dataset ID pattern")
    elif int(dataset_id.split("-L", 1)[1].split("-", 1)[0]) != report.level:
        raise ImportRejected("dataset_id level does not match the uploaded GIDs")
    destination = Path(root) / dataset_id
    if destination.exists():
        raise DatasetExists(f"dataset {dataset_id} already exists")

    # from_table validates the table and the manifest against the registry. write() is atomic.
    dataset = CellDataset.from_table(table, manifest, registry=registry)
    dataset.write(destination)
    dropped = tuple(name for name in columns if name != report.key_column and name not in {f.name for f in value_fields})
    return StoredDataset(
        dataset_id=dataset_id,
        path=destination,
        level=report.level,
        rows=table.num_rows,
        unique_cells=table.num_rows,
        value_fields=tuple(f.name for f in value_fields),
        dropped_columns=dropped,
    )


def read_dataset(registry: DomainRegistry, path: Path) -> dict[str, Any]:
    """Read a stored dataset. The reader checks the sidecar, the embedded manifest, and every row."""
    dataset = CellDataset.read(path, registry=registry)
    manifest = dataset.manifest
    return {
        "dataset_id": Path(path).name,
        "level": manifest.level,
        "rows": dataset.num_rows,
        "unique_cells": manifest.storage["unique_cell_count"],
        "value_fields": [field["name"] for field in manifest.value_fields],
        "manifest": manifest.to_dict(),
        "valid": True,
    }
