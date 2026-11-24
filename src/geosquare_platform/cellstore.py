"""Step 2 of the platform: store a checked GID table as a cell dataset with a manifest.

The layout is the cell dataset contract in geosquare-v2 (format 1.1): one directory with
`manifest.json` and `data/*.parquet`. The reference writer validates the table and the
manifest, and reading a dataset back validates it again.

The writer fills the machine fields itself: column statistics, the covered extent, and the
content hash of the data file. The uploader must state the governance fields: the licence and
the sharing policy. See geosquare-v2 docs/dataset-metadata.md.

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
import pyarrow.compute as pc
from geosquare_v2 import CellDataset, DomainRegistry, GeosquareGrid
from geosquare_v2.cell_metadata import data_file_entry
from geosquare_v2.crs import wgs84_to_grid_transformer
from geosquare_v2.errors import GeosquareError
from pyproj.enums import TransformDirection

from .importer import ImportRejected, analyse, read_csv

DOMAIN = "ID"
REGISTRY_VERSION = "2.0.0"  # the registry loader accepts only this version
MANIFEST_VERSION = "1.1"
SPEC_VERSION = "2.0"
DATASET_ID_PATTERN = re.compile(r"ID-L(\d{1,2})-[0-9a-f]{12}")
NAMED_DIR = "named"  # versioned datasets: <data root>/named/<name>/v<N>
NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_VERSION_FOLDER = re.compile(r"v(\d+)")
SHARE_POLICIES = frozenset(("internal", "k_anon_only", "public"))
REFRESH_RATES = frozenset(("once", "daily", "weekly", "monthly", "yearly", "irregular"))
# Default rollup rule per semantics (docs/dataset-metadata.md, section 4). The uploader can change it.
# RATE has no default weight, so it defaults to "none" (not rolled up) until the uploader names one.
DEFAULT_SPACE_AGGREGATION: dict[str, tuple[str, str | None]] = {
    "COUNT": ("sum", None),
    "TOTAL": ("sum", None),
    "DENSITY": ("weighted_mean", "area"),
    "MEASUREMENT": ("median", None),
    "CATEGORICAL": ("majority_weighted", None),
}
# A dataset is treated as stale after this many days without an update, per refresh rhythm.
STALE_AFTER_DAYS = {"daily": 2, "weekly": 14, "monthly": 62, "yearly": 400}


def generator_label() -> str:
    """Names the platform and the libraries that wrote the dataset. The manifest keeps it."""
    return (
        f"geosquare-platform {version('geosquare-platform')} "
        f"(geosquare-engine {version('geosquare-engine')}, geosquare-v2 {version('geosquare-v2')})"
    )


# Logical type from the upload -> Parquet type. The reference checks the semantics for each type.
_PARQUET_TYPES = {"numeric": "float64", "categorical": "string"}
_FIELD_KEYS = frozenset(("name", "logical_type", "semantics", "unit", "space_aggregation", "space_weight"))
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
    space_aggregation: str = "none"  # how child cells combine into a parent cell
    space_weight: str | None = None  # null, "area", or another COUNT or TOTAL column

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "logical_type": self.logical_type,
            "semantics": self.semantics,
            "unit": self.unit,
            "space_aggregation": self.space_aggregation,
            "space_weight": self.space_weight,
        }


@dataclass(frozen=True)
class Governance:
    """Who may see the data and under which licence. The uploader states it; the platform refuses a missing one."""

    license: str
    share_policy: str
    title: str | None = None
    owner: str | None = None
    source_name: str | None = None
    refresh: str = "once"

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "owner": self.owner,
            "source_name": self.source_name,
            "license": self.license,
            "share_policy": self.share_policy,
            "refresh": self.refresh,
        }


@dataclass(frozen=True)
class StoredDataset:
    dataset_id: str
    path: Path
    level: int
    rows: int
    unique_cells: int
    value_fields: tuple[str, ...]
    dropped_columns: tuple[str, ...]
    version: int | None = None  # set only for a named upload

    def summary(self) -> dict[str, Any]:
        body = {
            "dataset_id": self.dataset_id,
            "level": self.level,
            "rows": self.rows,
            "unique_cells": self.unique_cells,
            "value_fields": list(self.value_fields),
            "dropped_columns": list(self.dropped_columns),
            "valid": True,
        }
        if self.version is not None:
            body["name"] = self.dataset_id
            body["version"] = self.version
        return body


def _text_or_none(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ImportRejected(f"{label} must be text")
    stripped = value.strip()
    return stripped or None


def parse_governance(
    license_text: Any,
    share_policy: Any,
    *,
    title: Any = None,
    owner: Any = None,
    source_name: Any = None,
    refresh: Any = None,
) -> Governance:
    """Check the governance fields from the upload. The licence and sharing policy are required."""
    licence = _text_or_none(license_text, "license")
    if licence is None:
        raise ImportRejected("license is required: state the licence or terms of the source data")
    if share_policy not in SHARE_POLICIES:
        raise ImportRejected(f"share_policy is required and must be one of {sorted(SHARE_POLICIES)}")
    rhythm = _text_or_none(refresh, "refresh") or "once"
    if rhythm not in REFRESH_RATES:
        raise ImportRejected(f"refresh must be one of {sorted(REFRESH_RATES)}")
    return Governance(
        license=licence,
        share_policy=share_policy,
        title=_text_or_none(title, "title"),
        owner=_text_or_none(owner, "owner"),
        source_name=_text_or_none(source_name, "source_name"),
        refresh=rhythm,
    )


def parse_value_fields(raw: Sequence[Any]) -> list[ValueField]:
    """Check the value field list from the upload. Nothing is repaired. A missing rollup rule gets its default."""
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
        aggregation = item.get("space_aggregation")
        weight = item.get("space_weight")
        if aggregation is None:
            aggregation, default_weight = DEFAULT_SPACE_AGGREGATION.get(semantics, ("none", None))
            weight = default_weight if weight is None else weight
        elif not isinstance(aggregation, str) or not aggregation:
            raise ImportRejected(f"{where}.space_aggregation must be text")
        if weight is not None and (not isinstance(weight, str) or not weight):
            raise ImportRejected(f"{where}.space_weight must be text or null")
        if name in names:
            raise ImportRejected(f"{where}.name {name!r} is listed twice")
        names.add(name)
        fields.append(ValueField(name, logical, semantics, unit, aggregation, weight))
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


def cell_extent_wgs84(registry: DomainRegistry, gids: Sequence[str]) -> list[float] | None:
    """Bounding box in WGS84 of the cells, as [min_lon, min_lat, max_lon, max_lat].

    The corners of each cell go through the same explicit operation as the forward index, so the
    extent matches the GIDs. Returns None when there are no cells.
    """
    if not gids:
        return None
    profile = registry.get(DOMAIN)
    grid = GeosquareGrid(profile)
    transformer = wgs84_to_grid_transformer(profile)
    xs: list[float] = []
    ys: list[float] = []
    for gid in gids:
        bounds = grid.projected_bounds(gid)
        xs.extend((bounds.min_x, bounds.max_x, bounds.min_x, bounds.max_x))
        ys.extend((bounds.min_y, bounds.min_y, bounds.max_y, bounds.max_y))
    lon, lat = transformer.transform(xs, ys, direction=TransformDirection.INVERSE)
    return [float(min(lon)), float(min(lat)), float(max(lon)), float(max(lat))]


def _column_statistics(table: pa.Table, field: ValueField) -> tuple[float, float | None, float | None]:
    """Null share, and the smallest and largest value for numeric columns (None otherwise)."""
    column = table.column(field.name)
    rows = table.num_rows
    null_share = column.null_count / rows if rows else 0.0
    if field.logical_type != "numeric" or column.null_count == rows:
        return null_share, None, None
    return null_share, pc.min(column).as_py(), pc.max(column).as_py()


def build_manifest(
    registry: DomainRegistry,
    *,
    level: int,
    fields: Sequence[ValueField],
    table: pa.Table,
    governance: Governance,
    extent_bbox: list[float] | None,
    created_at: str,
) -> dict[str, Any]:
    """Manifest 1.1 for a `cell_values` dataset: one row per cell, GIDs from the file."""
    profile = registry.get(DOMAIN)
    value_fields = []
    for field in fields:
        null_share, low, high = _column_statistics(table, field)
        value_fields.append(
            {
                "name": field.name,
                "parquet_type": _PARQUET_TYPES[field.logical_type],
                "logical_type": field.logical_type,
                "semantics": field.semantics,
                "unit": field.unit,
                "nullable": False,
                # How the source became the cell value. Nothing is aggregated on a CSV import.
                "aggregation_rule": None,
                "space_aggregation": field.space_aggregation,
                "space_weight": field.space_weight,
                "null_share": null_share,
                "min_value": low,
                "max_value": high,
            }
        )
    row_count = table.num_rows
    return {
        "manifest_version": MANIFEST_VERSION,
        "dataset_type": "cell_values",
        "grid_system": "geosquare",
        "grid_version": "v2",
        "domain_code": profile.domain_code,
        "domain_id": profile.domain_id,
        "level": level,
        "profile_version": profile.profile_version,
        "registry_version": REGISTRY_VERSION,
        "value_fields": value_fields,
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
        "extent": {"bbox_wgs84": extent_bbox, "compact_set_path": None},
        "governance": governance.as_dict(),
        "storage": {
            "parquet_compression": "zstd",
            "geometry_column": None,
            "geometry_format": None,
            "geometry_crs": None,
            "partitioning": [],
            "sorted_by": ["gid"],
            "row_count": row_count,
            "unique_cell_count": row_count,
            "data_files": [
                data_file_entry(table, [field.name for field in fields], path="data/part-00000.parquet")
            ],
        },
        "provenance": {
            "created_at": created_at,
            "created_by": generator_label(),
            "generator": "csv_import",
            "aggregation_rule": None,
            "value_semantics": None,
            "notes": "one Parquet file; partitioning not used",
            "spec_version": SPEC_VERSION,
            "derived_from": [],
        },
    }


def is_stale(created_at: str | None, refresh: str | None, *, now: datetime | None = None) -> bool | None:
    """True when a dataset is older than its refresh rhythm allows. None when no rhythm is recorded."""
    if refresh is None:
        return None
    limit = STALE_AFTER_DAYS.get(refresh)
    if limit is None or not created_at:
        return False
    created = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return (current - created).days > limit


def store_csv(
    registry: DomainRegistry,
    root: Path,
    data: bytes,
    *,
    value_fields: Sequence[ValueField],
    governance: Governance,
    confirm_digit_gid: bool = False,
    created_at: str | None = None,
    dataset_id: str | None = None,
    name: str | None = None,
) -> StoredDataset:
    """Detect the GID column, build the cell table, validate it, and write it under `root`.

    With `name`, the upload becomes the next version of that name (see `_next_version`).
    """
    report = analyse(data, confirm_digit_gid=confirm_digit_gid)
    if not report.ok:
        raise ImportRejected(f"{report.status}: {report.reason}")

    columns = read_csv(data)
    table = build_table(columns, report.key_column, value_fields)
    manifest = build_manifest(
        registry,
        level=report.level,
        fields=value_fields,
        table=table,
        governance=governance,
        extent_bbox=cell_extent_wgs84(registry, table.column("gid").to_pylist()),
        created_at=created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    new_version: int | None = None
    if name is not None:
        # A named upload is a new version of that name. Same content as any version is refused.
        if not NAME_PATTERN.fullmatch(name):
            raise ImportRejected("name must be 1 to 64 characters: a-z, 0-9, _ or -, starting with a letter or digit")
        dataset_id = name
        destination, new_version = _next_version(root, name, manifest["storage"]["data_files"][0]["content_sha256"])
    else:
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
    destination.parent.mkdir(parents=True, exist_ok=True)
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
        version=new_version,
    )


def list_versions(root: Path, name: str) -> list[tuple[int, Path]]:
    """Versions of a named dataset, oldest first. Each is `<root>/named/<name>/v<N>`."""
    folder = Path(root) / NAMED_DIR / name
    if not folder.is_dir():
        return []
    found = []
    for path in folder.iterdir():
        match = _VERSION_FOLDER.fullmatch(path.name)
        if match and path.is_dir():
            found.append((int(match.group(1)), path))
    return sorted(found)


def _next_version(root: Path, name: str, content_hash: str) -> tuple[Path, int]:
    versions = list_versions(root, name)
    for number, path in versions:
        stored = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if stored["storage"]["data_files"][0]["content_sha256"] == content_hash:
            raise DatasetExists(f"{name} version {number} already has this content")
    number = versions[-1][0] + 1 if versions else 1
    return Path(root) / NAMED_DIR / name / f"v{number}", number


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
