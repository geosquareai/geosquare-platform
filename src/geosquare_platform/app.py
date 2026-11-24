from __future__ import annotations

import csv
import io
import json
import math
import os
from collections.abc import Mapping
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
import geosquare_engine as gs
from geosquare_v2 import read_cell_dataset
from geosquare_v2.errors import GeosquareError

from geosquare_engine.compact import contains

from .coverage import COVERAGE_DIR, compact_coverage
from .cellstore import (
    DATASET_ID_PATTERN,
    NAME_PATTERN,
    NAMED_DIR,
    DatasetExists,
    Governance,
    ValueField,
    is_stale,
    list_versions,
    parse_governance,
    parse_value_fields,
    read_dataset,
    store_csv,
)
from .compactstore import COMPACT_ID_PATTERN, read_compact, store_compact_csv
from .timestore import TIME_ID_PATTERN, join_time, read_time_dataset, store_time_csv
from .crosswalk import CrosswalkRejected, crosswalk_csv, load_crosswalk, validate_crosswalk
from .importer import ImportRejected, RowsRejected, analyse, read_csv
from .registry import load_registry
from .map_routes import _parse_bbox
from .map_routes import router as map_router

app = FastAPI(title="GeoSquare Platform", version="0.1.0")
app.include_router(map_router)

DATA_ENV = "GSQ_PLATFORM_DATA"  # folder for stored datasets; default data/cells
INTERNAL_ENV = "GSQ_INTERNAL_ROUTES"  # set to "1" only on an internal host


def _require_internal() -> None:
    """Hide routes that return grouped or per-cell values. They answer 404 unless enabled."""
    if os.environ.get(INTERNAL_ENV) != "1":
        raise HTTPException(status_code=404, detail="Not Found")


def data_root() -> Path:
    return Path(os.environ.get(DATA_ENV, "data/cells"))


def _dataset_path(dataset_id: str) -> Path:
    if not DATASET_ID_PATTERN.fullmatch(dataset_id):
        raise HTTPException(status_code=404, detail="no such dataset")
    path = data_root() / dataset_id
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="no such dataset")
    return path


def _dataset_table(dataset_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = _dataset_path(dataset_id)
    try:
        metadata = read_dataset(load_registry(), path)
        table = read_cell_dataset(path, registry=load_registry()).table
    except GeosquareError as exc:
        raise HTTPException(status_code=422, detail=f"dataset fails validation: {exc}") from exc
    return metadata, table.to_pylist()


def _csv_bytes(rows: list[dict[str, Any]], columns: list[str]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _numeric(value: Any, *, row: int, column: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"row {row}, column {column!r} must be numeric") from exc
    if not math.isfinite(number):
        raise HTTPException(status_code=422, detail=f"row {row}, column {column!r} must be finite")
    return number


def _requested_columns(value_columns: str) -> list[str]:
    columns = [value.strip() for value in value_columns.split(",") if value.strip()]
    if not columns or len(set(columns)) != len(columns):
        raise HTTPException(status_code=422, detail="value_columns must list each column once")
    return columns


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/import/analyse")
async def import_analyse(file: UploadFile = File(...), confirm_digit_gid: bool = False) -> dict:
    """Read a CSV and report its GID column and level. Nothing is stored yet."""
    try:
        return analyse(await file.read(), confirm_digit_gid=confirm_digit_gid).as_dict()
    except ImportRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/datasets", status_code=201)
async def create_dataset(
    file: UploadFile = File(...),
    value_fields: str = Form(..., description="JSON array: name, logical_type, semantics, unit, optional space_aggregation and space_weight"),
    licence: str | None = Form(None, alias="license", description="Required. Licence or terms of the source data."),
    share_policy: str | None = Form(None, description="Required. internal, k_anon_only or public."),
    owner: str | None = Form(None),
    title: str | None = Form(None),
    source_name: str | None = Form(None),
    refresh: str | None = Form(None, description="once (default), daily, weekly, monthly, yearly or irregular."),
    confirm_digit_gid: bool = False,
    name: str | None = Form(None, description="Optional. Upload as the next version of this name."),
) -> dict:
    """Detect the GID column and level, check every row, and store the cells with a manifest."""
    try:
        raw = json.loads(value_fields)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="value_fields is not valid JSON") from exc
    if not isinstance(raw, list):
        raise HTTPException(status_code=422, detail="value_fields must be a JSON array")
    try:
        governance = parse_governance(
            licence, share_policy, title=title, owner=owner, source_name=source_name, refresh=refresh
        )
        stored = store_csv(
            load_registry(),
            data_root(),
            await file.read(),
            value_fields=parse_value_fields(raw),
            governance=governance,
            confirm_digit_gid=confirm_digit_gid,
            name=name,
        )
    except DatasetExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RowsRejected as exc:
        raise HTTPException(status_code=422, detail={"reason": str(exc), "problems": exc.problems}) from exc
    except (ImportRejected, GeosquareError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return stored.summary()


def _named_versions(name: str) -> list[tuple[int, Path]]:
    if not NAME_PATTERN.fullmatch(name):
        raise HTTPException(status_code=404, detail="no such dataset")
    versions = list_versions(data_root(), name)
    if not versions:
        raise HTTPException(status_code=404, detail="no such dataset")
    return versions


def _read_named(name: str, version: int | None) -> dict:
    versions = _named_versions(name)
    if version is None:
        number, path = versions[-1]
    else:
        match = [item for item in versions if item[0] == version]
        if not match:
            raise HTTPException(status_code=404, detail="no such version")
        number, path = match[0]
    try:
        body = read_dataset(load_registry(), path)
    except GeosquareError as exc:
        raise HTTPException(status_code=422, detail=f"dataset fails validation: {exc}") from exc
    return {**body, "dataset_id": name, "name": name, "version": number}


@app.get("/named/{name}")
def get_named_latest(name: str) -> dict:
    """The latest version of a named dataset, checked the same way as a dataset by ID."""
    return _read_named(name, None)


@app.get("/named/{name}/versions")
def list_named_versions(name: str) -> dict:
    versions = _named_versions(name)
    rows = []
    for number, path in versions:
        manifest = _read_catalog_manifest(path)
        rows.append(
            {
                "version": number,
                "level": manifest["level"],
                "rows": manifest["storage"]["row_count"],
                "content_sha256": manifest["storage"]["data_files"][0]["content_sha256"],
                "created_at": manifest["provenance"]["created_at"],
            }
        )
    return {"name": name, "latest": versions[-1][0], "versions": rows}


@app.get("/named/{name}/versions/{version}")
def get_named_version(name: str, version: int) -> dict:
    return _read_named(name, version)


@app.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: str) -> dict:
    """Read a stored dataset again and check its manifest. Returns 422 if the check fails."""
    path = _dataset_path(dataset_id)
    try:
        return read_dataset(load_registry(), path)
    except GeosquareError as exc:
        raise HTTPException(status_code=422, detail=f"dataset fails validation: {exc}") from exc


@app.post("/crosswalks", status_code=201)
async def create_crosswalk(
    file: UploadFile = File(...),
    name: str = Form(...),
    domain_code: str = Form(...),
    level: int = Form(...),
    weight_kind: str = Form(...),
) -> dict:
    if not DATASET_ID_PATTERN.fullmatch(name):
        raise HTTPException(status_code=422, detail="name does not match the dataset ID pattern")
    destination = data_root() / "crosswalks" / name
    if destination.exists():
        raise HTTPException(status_code=409, detail=f"crosswalk {name} already exists")
    try:
        crosswalk = validate_crosswalk(
            await file.read(), domain_code=domain_code, level=level, weight_kind=weight_kind
        )
    except CrosswalkRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "crosswalk.csv").write_bytes(crosswalk_csv(crosswalk))
    (destination / "manifest.json").write_text(
        json.dumps(crosswalk.manifest, indent=2) + "\n", encoding="utf-8"
    )
    return {"name": name, **crosswalk.summary()}


@app.get("/crosswalks/{name}")
def get_crosswalk(name: str) -> dict:
    if not DATASET_ID_PATTERN.fullmatch(name):
        raise HTTPException(status_code=404, detail="no such crosswalk")
    path = data_root() / "crosswalks" / name
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="no such crosswalk")
    try:
        crosswalk = load_crosswalk(path)
    except (CrosswalkRejected, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"crosswalk fails validation: {exc}") from exc
    return {"name": name, "manifest": crosswalk.manifest, **crosswalk.summary()}



def apply_crosswalk_data(
    crosswalk,
    source_data: bytes,
    *,
    dataset_id: str,
    value_columns: list[str],
    field_semantics: Mapping[str, str] | None = None,
    governance: Governance,
) -> dict[str, Any]:
    """Apply a validated crosswalk and store the resulting numeric dataset.

    This is the shared implementation for the HTTP route and the v1 migration tool.
    Values are allocated only by the stored crosswalk weights, and conservation is
    checked before the dataset is written.
    """
    if crosswalk.manifest["domain_code"] != "ID":
        raise HTTPException(status_code=422, detail="platform datasets currently support domain_code ID only")
    columns = value_columns
    if not columns or len(set(columns)) != len(columns):
        raise HTTPException(status_code=422, detail="value_columns must list each column once")
    try:
        source = read_csv(source_data)
    except ImportRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if "sumber_id" not in source:
        raise HTTPException(status_code=422, detail="source CSV needs a sumber_id column")
    missing_columns = [column for column in columns if column not in source]
    if missing_columns:
        raise HTTPException(status_code=422, detail=f"unknown value columns: {missing_columns}")

    mapping: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for source_id, gid, weight in crosswalk.rows:
        mapping[source_id].append((gid, weight))
    unknown: list[str] = []
    unknown_seen: set[str] = set()
    allocated: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    source_totals = {column: 0.0 for column in columns}
    source_rows = len(source["sumber_id"])
    for row_number in range(source_rows):
        source_id = source["sumber_id"][row_number]
        if not source_id or source_id not in mapping:
            if source_id not in unknown_seen:
                unknown.append(source_id or "")
                unknown_seen.add(source_id or "")
            continue
        values = {
            column: _numeric(source[column][row_number], row=row_number + 2, column=column) for column in columns
        }
        for column, value in values.items():
            source_totals[column] += value
        for gid, weight in mapping[source_id]:
            for column, value in values.items():
                allocated[gid][column] += value * weight
    if unknown:
        raise HTTPException(status_code=422, detail={"unknown_sumber_ids": unknown[:10]})

    conservation: dict[str, dict[str, float | bool]] = {}
    for column in columns:
        allocated_total = sum(values[column] for values in allocated.values())
        source_total = source_totals[column]
        tolerance = 1e-6 * max(abs(source_total), 1e-12)
        ok = abs(allocated_total - source_total) <= tolerance
        conservation[column] = {
            "source_sum": source_total,
            "allocated_sum": allocated_total,
            "difference": allocated_total - source_total,
            "within_tolerance": ok,
        }
        if not ok:
            raise HTTPException(status_code=422, detail={"conservation": conservation})

    output_rows = []
    for gid in sorted(allocated):
        output_rows.append({"gid": gid, **{column: allocated[gid][column] for column in columns}})
    output = _csv_bytes(output_rows, ["gid", *columns])
    semantics = field_semantics or {}
    fields = [ValueField(column, "numeric", semantics.get(column, "COUNT"), None) for column in columns]
    try:
        stored = store_csv(
            load_registry(),
            data_root(),
            output,
            value_fields=fields,
            governance=governance,
            dataset_id=dataset_id,
            confirm_digit_gid=True,
        )
    except DatasetExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ImportRejected, GeosquareError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"summary": stored.summary(), "conservation": conservation}


@app.post("/crosswalks/{name}/apply")
async def apply_crosswalk(
    name: str,
    file: UploadFile = File(...),
    dataset_id: str = Form(...),
    value_columns: str = Form(...),
    licence: str | None = Form(None, alias="license", description="Required. Licence or terms of the source data."),
    share_policy: str | None = Form(None, description="Required. internal, k_anon_only or public."),
) -> dict:
    if not DATASET_ID_PATTERN.fullmatch(name):
        raise HTTPException(status_code=404, detail="no such crosswalk")
    if not DATASET_ID_PATTERN.fullmatch(dataset_id):
        raise HTTPException(status_code=422, detail="dataset_id does not match the dataset ID pattern")
    path = data_root() / "crosswalks" / name
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="no such crosswalk")
    try:
        crosswalk = load_crosswalk(path)
    except (CrosswalkRejected, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"crosswalk fails validation: {exc}") from exc
    try:
        governance = parse_governance(licence, share_policy)
    except ImportRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return apply_crosswalk_data(
        crosswalk,
        await file.read(),
        dataset_id=dataset_id,
        value_columns=_requested_columns(value_columns),
        governance=governance,
    )


@app.get("/datasets/{dataset_id}/rollup")
def rollup_dataset(
    dataset_id: str,
    level: int = Query(...),
    sum_columns: str = Query(..., alias="sum"),
) -> dict:
    """Internal route for grouped values. External exports must use ``/export``."""
    _require_internal()
    metadata, source_rows = _dataset_table(dataset_id)
    dataset_level = metadata["level"]
    if level < 0 or level > dataset_level:
        raise HTTPException(status_code=422, detail="level must be between 0 and the dataset level")
    columns = _requested_columns(sum_columns)
    available = set(metadata["value_fields"])
    unknown = [column for column in columns if column not in available]
    if unknown:
        raise HTTPException(status_code=422, detail={"unknown_columns": unknown})
    groups: dict[str, dict[str, Any]] = {}
    for source_row in source_rows:
        parent = source_row["gid"][:level]
        group = groups.setdefault(parent, {"parent_gid": parent, "cells": 0, **{column: 0.0 for column in columns}})
        group["cells"] += 1
        for column in columns:
            group[column] += float(source_row[column])
    return {"rows": [groups[key] for key in sorted(groups)], "cells": len(source_rows)}


@app.post("/query/join")
def join_query(payload: dict = Body(...)) -> dict:
    _require_internal()
    dataset_ids = payload.get("dataset_ids")
    level = payload.get("level")
    columns = payload.get("sum")
    prefixes = payload.get("gid_prefixes")
    if not isinstance(dataset_ids, list) or len(dataset_ids) < 2 or not all(isinstance(item, str) for item in dataset_ids):
        raise HTTPException(status_code=422, detail="dataset_ids must contain at least two dataset IDs")
    if len(set(dataset_ids)) != len(dataset_ids):
        raise HTTPException(status_code=422, detail="dataset_ids must be unique")
    if not isinstance(level, int) or isinstance(level, bool) or level < 0:
        raise HTTPException(status_code=422, detail="level must be a non-negative integer")
    if not isinstance(columns, list) or not columns or not all(isinstance(item, str) and item for item in columns):
        raise HTTPException(status_code=422, detail="sum must be a non-empty list of column names")
    if len(set(columns)) != len(columns):
        raise HTTPException(status_code=422, detail="sum must not contain duplicate columns")
    if prefixes is not None and (not isinstance(prefixes, list) or not all(isinstance(item, str) for item in prefixes)):
        raise HTTPException(status_code=422, detail="gid_prefixes must be a list of strings")

    datasets = []
    for dataset_id in dataset_ids:
        metadata, rows = _dataset_table(dataset_id)
        if level > metadata["level"]:
            raise HTTPException(status_code=422, detail="level is greater than a dataset level")
        datasets.append((dataset_id, metadata, {row["gid"]: row for row in rows}))
    domains = {metadata["manifest"]["domain_code"] for _, metadata, _ in datasets}
    levels = {metadata["level"] for _, metadata, _ in datasets}
    if len(domains) != 1 or len(levels) != 1:
        raise HTTPException(status_code=422, detail="datasets must have the same domain_code and level")
    available = set().union(*(set(metadata["value_fields"]) for _, metadata, _ in datasets))
    unknown = [column for column in columns if column not in available]
    if unknown:
        raise HTTPException(status_code=422, detail={"unknown_columns": unknown})

    common = set(datasets[0][2])
    for _, _, rows in datasets[1:]:
        common &= set(rows)
    if prefixes is not None:
        common = {gid for gid in common if any(gid.startswith(prefix) for prefix in prefixes)}
    column_counts = {
        column: sum(column in metadata["value_fields"] for _, metadata, _ in datasets) for column in columns
    }
    groups: dict[str, dict[str, Any]] = {}
    for gid in sorted(common):
        parent = gid[:level]
        group = groups.setdefault(parent, {"parent_gid": parent, "cells": 0})
        group["cells"] += 1
        for dataset_id, metadata, rows in datasets:
            for column in columns:
                if column not in metadata["value_fields"]:
                    continue
                key = f"{dataset_id}.{column}" if column_counts[column] > 1 else column
                group[key] = group.get(key, 0.0) + float(rows[gid][column])
    return {"rows": [groups[key] for key in sorted(groups)], "cells_joined": len(common)}


@app.post("/datasets/compact", status_code=201)
async def create_compact_dataset(file: UploadFile = File(...)) -> dict:
    """Store a cell_compact_set (gid, level). The rows must already be canonical. Nothing is repaired."""
    try:
        return store_compact_csv(data_root(), await file.read())
    except DatasetExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ImportRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _compact_path(compact_id: str) -> Path:
    if not COMPACT_ID_PATTERN.fullmatch(compact_id):
        raise HTTPException(status_code=404, detail="no such compact set")
    path = data_root() / compact_id
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="no such compact set")
    return path


@app.post("/query/compact-join")
def compact_join(payload: dict = Body(...)) -> dict:
    """Sum value columns over the cells a compact set covers. Each cell is matched by ancestor lookup."""
    _require_internal()
    compact_id = payload.get("compact_id")
    dataset_id = payload.get("dataset_id")
    columns = payload.get("sum")
    if not isinstance(compact_id, str) or not isinstance(dataset_id, str):
        raise HTTPException(status_code=422, detail="compact_id and dataset_id must be text")
    if not isinstance(columns, list) or not columns or not all(isinstance(item, str) and item for item in columns):
        raise HTTPException(status_code=422, detail="sum must be a non-empty list of column names")
    try:
        compact_rows = read_compact(_compact_path(compact_id))
    except ImportRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    metadata, source_rows = _dataset_table(dataset_id)
    unknown = [column for column in columns if column not in set(metadata["value_fields"])]
    if unknown:
        raise HTTPException(status_code=422, detail={"unknown_columns": unknown})
    members = frozenset(gid for gid, _ in compact_rows)
    matched = [row for row in source_rows if contains(members, row["gid"])]
    return {
        "compact_id": compact_id,
        "dataset_id": dataset_id,
        "matched_cells": len(matched),
        "sums": {column: sum(float(row[column]) for row in matched) for column in columns},
    }


_SEMANTICS = ("COUNT", "TOTAL", "MEASUREMENT", "CATEGORICAL")


@app.post("/time-datasets", status_code=201)
async def create_time_dataset(
    file: UploadFile = File(...),
    level: int = Form(...),
    period_type: str = Form(...),
    period_start: str = Form(...),
    period_end: str = Form(...),
    value_fields: str = Form(..., description="JSON array: name, semantics, unit, time_aggregation"),
    bucket_hours: int | None = Form(None),
) -> dict:
    """Store one cell_time_values dataset (one row per cell and period). Rules come from geosquare-v2."""
    try:
        fields = json.loads(value_fields)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="value_fields is not valid JSON") from exc
    if not isinstance(fields, list) or not fields:
        raise HTTPException(status_code=422, detail="value_fields must be a non-empty JSON array")
    for field in fields:
        if not isinstance(field, dict) or not isinstance(field.get("name"), str) or field.get("semantics") not in _SEMANTICS:
            raise HTTPException(status_code=422, detail="each value field needs a name and a known semantics")
    try:
        return store_time_csv(
            data_root(),
            await file.read(),
            level=level,
            period_type=period_type,
            period_start=period_start,
            period_end=period_end,
            value_fields=fields,
            bucket_hours=bucket_hours,
        )
    except ImportRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _time_path(dataset_id: str) -> Path:
    if not TIME_ID_PATTERN.fullmatch(dataset_id):
        raise HTTPException(status_code=404, detail="no such time dataset")
    path = data_root() / dataset_id
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="no such time dataset")
    return path


@app.post("/query/time-join")
def time_join(payload: dict = Body(...)) -> dict:
    """Internal. Count left rows that have a same-cell right row in an interval relation, and sum columns."""
    _require_internal()
    left_id = payload.get("left_id")
    right_id = payload.get("right_id")
    relation_name = payload.get("relation", "contained")
    columns = payload.get("sum")
    if not isinstance(left_id, str) or not isinstance(right_id, str) or not isinstance(relation_name, str):
        raise HTTPException(status_code=422, detail="left_id, right_id and relation must be text")
    if not isinstance(columns, list) or not columns or not all(isinstance(item, str) and item for item in columns):
        raise HTTPException(status_code=422, detail="sum must be a non-empty list of column names")
    try:
        left = read_time_dataset(_time_path(left_id))
        right = read_time_dataset(_time_path(right_id))
        left_names = {field["name"] for field in left[0]["value_fields"]}
        unknown = [column for column in columns if column not in left_names]
        if unknown:
            raise HTTPException(status_code=422, detail={"unknown_columns": unknown})
        result = join_time(left, right, relation_name, columns)
    except ImportRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"left_id": left_id, "right_id": right_id, "relation": relation_name, **result}


# Catalog and cell-profile routes intentionally read only the manifest until a profile
# query needs row values. Grid identity and hierarchy stay in geosquare-engine.
def _catalog_sources() -> list[tuple[Path, str, int | None]]:
    """(folder, dataset_id, version) for each catalog row. A named dataset lists its latest version only."""
    root = data_root()
    if not root.is_dir():
        return []
    sources: list[tuple[Path, str, int | None]] = [
        (path, path.name, None)
        for path in sorted(root.iterdir())
        if path.is_dir() and path.name not in {"crosswalks", COVERAGE_DIR, NAMED_DIR}
    ]
    named = root / NAMED_DIR
    if named.is_dir():
        for folder in sorted(named.iterdir()):
            if folder.is_dir() and NAME_PATTERN.fullmatch(folder.name):
                versions = list_versions(root, folder.name)
                if versions:
                    number, path = versions[-1]
                    sources.append((path, folder.name, number))
    return sources


def _read_catalog_manifest(path: Path) -> dict[str, Any]:
    with (path / "manifest.json").open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, dict):
        raise ValueError("manifest must contain a JSON object")
    return manifest


def _touches(extent: Any, query: tuple[float, float, float, float]) -> bool:
    """True when a manifest extent's bounding box overlaps the query box. False when there is no extent."""
    if not isinstance(extent, Mapping):
        return False
    box = extent.get("bbox_wgs84")
    if not isinstance(box, list) or len(box) != 4:
        return False
    min_lon, min_lat, max_lon, max_lat = box
    q_min_lon, q_min_lat, q_max_lon, q_max_lat = query
    return min_lon <= q_max_lon and max_lon >= q_min_lon and min_lat <= q_max_lat and max_lat >= q_min_lat


def _refresh_of(manifest: Mapping[str, Any]) -> str | None:
    """The refresh rhythm a manifest 1.1 declares. None for manifest 1.0, which has no governance block."""
    governance = manifest.get("governance")
    if not isinstance(governance, Mapping):
        return None
    refresh = governance.get("refresh")
    return refresh if isinstance(refresh, str) else None


def _catalog_entry(path: Path, dataset_id: str) -> dict[str, Any]:
    try:
        manifest = _read_catalog_manifest(path)
        storage = manifest.get("storage", {})
        provenance = manifest.get("provenance", {})
        value_fields = manifest.get("value_fields", [])
        if not isinstance(storage, Mapping) or not isinstance(provenance, Mapping):
            raise ValueError("manifest storage and provenance must be objects")
        if not isinstance(value_fields, list) or not all(isinstance(field, Mapping) for field in value_fields):
            raise ValueError("manifest value_fields must be a list of objects")
        return {
            "dataset_id": dataset_id,
            "domain_code": manifest.get("domain_code"),
            "level": manifest.get("level"),
            "dataset_type": manifest.get("dataset_type"),
            "row_count": storage.get("row_count"),
            "unique_cell_count": storage.get("unique_cell_count"),
            "profile_version": manifest.get("profile_version"),
            "registry_version": manifest.get("registry_version"),
            "created_at": provenance.get("created_at"),
            "governance": manifest.get("governance"),
            "extent": manifest.get("extent"),
            "stale": is_stale(provenance.get("created_at"), _refresh_of(manifest)),
            "value_fields": [
                {
                    "name": field.get("name"),
                    "semantics": field.get("semantics"),
                    "unit": field.get("unit"),
                    "nullable": field.get("nullable"),
                    "space_aggregation": field.get("space_aggregation"),
                }
                for field in value_fields
            ],
        }
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"dataset_id": dataset_id, "error": f"cannot read manifest: {exc}"}


def _catalog_entries() -> list[dict[str, Any]]:
    entries = []
    for path, dataset_id, version in _catalog_sources():
        entry = _catalog_entry(path, dataset_id)
        if version is not None:
            entry = {
                **entry,
                "name": dataset_id,
                "version": version,
                "read_path": f"/named/{dataset_id}/versions/{version}",
            }
        entries.append(entry)
    return entries


def _catalog_key(entry: Mapping[str, Any]) -> str:
    """The entry's folder under the data root, the path its coverage is read from."""
    if "version" in entry:
        return f"{NAMED_DIR}/{entry['name']}/v{entry['version']}"
    return str(entry["dataset_id"])


def _profile_values(row: dict[str, Any] | None, value_fields: list[dict[str, Any]]) -> dict[str, Any]:
    if row is None:
        return {}
    return {field["name"]: row.get(field["name"]) for field in value_fields}


@app.get("/catalog")
def catalog(
    bbox: str | None = Query(
        None,
        description="min_lon,min_lat,max_lon,max_lat. Keep datasets whose covered extent touches this box.",
    ),
    gid: str | None = Query(None, description="Keep datasets whose exact coverage includes this GID."),
) -> list[dict[str, Any]]:
    """List dataset metadata without opening parquet data files.

    bbox keeps datasets whose extent (manifest 1.1) touches the box. Datasets without an extent,
    such as manifest 1.0, are left out. This is a box test.

    gid keeps datasets whose exact coverage (canonical compact cell set) includes the cell, either
    stored or under a stored ancestor. This is an exact test. The first call per dataset builds
    its coverage from the data.
    """
    entries = _catalog_entries()
    if bbox is not None:
        query = _parse_bbox(bbox)
        entries = [entry for entry in entries if _touches(entry.get("extent"), query)]
    if gid is not None:
        try:
            gs.decode(gid)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"invalid GID: {exc}") from exc
        entries = [entry for entry in entries if _covers(entry, gid)]
    return entries


def _covers(entry: Mapping[str, Any], gid: str) -> bool:
    if "error" in entry:
        return False
    try:
        coverage = frozenset(compact_coverage(data_root(), _catalog_key(entry)))
    except (OSError, ValueError):
        return False
    return contains(coverage, gid)


@app.get("/datasets/{dataset_id}/coverage")
def dataset_coverage(dataset_id: str) -> dict:
    """Exact coverage of a stored dataset: its canonical compact cell set. Built on first use, then cached."""
    _dataset_path(dataset_id)
    try:
        cells = compact_coverage(data_root(), dataset_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"cannot read the dataset cells: {exc}") from exc
    return {"dataset_id": dataset_id, "cells": list(cells)}


@app.get("/cells/{gid}/profile")
def cell_profile(gid: str) -> dict[str, Any]:
    """Return values at the cell, its ancestors, or a finer-level marker."""
    try:
        level, _, _ = gs.decode(gid)
        parent_gid = gs.parent(gid)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid GID: {exc}") from exc

    datasets: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for entry in _catalog_entries():
        dataset_id = entry["dataset_id"]
        if "error" in entry:
            skipped.append({"dataset_id": dataset_id, "reason": entry["error"]})
            continue
        if "version" in entry:
            # shortcut: the profile reads legacy dataset IDs only, upgrade when a profile needs named versions.
            skipped.append({"dataset_id": dataset_id, "reason": "named dataset versions are not in profiles yet"})
            continue
        if entry["domain_code"] != "ID":
            skipped.append(
                {"dataset_id": dataset_id, "reason": f"domain {entry['domain_code']!r} is not supported in profiles"}
            )
            continue
        dataset_level = entry["level"]
        if not isinstance(dataset_level, int) or isinstance(dataset_level, bool):
            skipped.append({"dataset_id": dataset_id, "reason": "manifest level is not an integer"})
            continue
        if dataset_level > level:
            datasets.append(
                {
                    "dataset_id": dataset_id,
                    "relation": "finer",
                    "present": False,
                    "values": {},
                    "status": "not_applicable",
                    "reason": "finer level; query the children",
                }
            )
            continue

        candidate_gid = gid
        relation = "same"
        for _ in range(level - dataset_level):
            candidate_gid = gs.parent(candidate_gid)
            relation = "ancestor"
            if candidate_gid is None:
                break
        _, rows = _dataset_table(dataset_id)
        row_by_gid = {row["gid"]: row for row in rows}
        row = row_by_gid.get(candidate_gid) if candidate_gid is not None else None
        datasets.append(
            {
                "dataset_id": dataset_id,
                "relation": relation,
                "present": row is not None,
                "values": _profile_values(row, entry["value_fields"]),
            }
        )

    return {
        "gid": gid,
        "level": level,
        "parent_gid": parent_gid,
        "datasets": datasets,
        "skipped_datasets": skipped,
    }

from .export import router as export_router

app.include_router(export_router)
