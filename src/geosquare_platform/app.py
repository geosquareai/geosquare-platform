from __future__ import annotations

import csv
import io
import json
import math
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from geosquare_v2 import read_cell_dataset
from geosquare_v2.errors import GeosquareError

from .cellstore import DATASET_ID_PATTERN, DatasetExists, ValueField, parse_value_fields, read_dataset, store_csv
from .crosswalk import CrosswalkRejected, crosswalk_csv, load_crosswalk, validate_crosswalk
from .importer import ImportRejected, analyse, read_csv
from .registry import load_registry
from .map_routes import router as map_router

app = FastAPI(title="GeoSquare Platform", version="0.1.0")
app.include_router(map_router)

DATA_ENV = "GSQ_PLATFORM_DATA"  # folder for stored datasets; default data/cells


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
    value_fields: str = Form(..., description="JSON array: name, logical_type, semantics, unit"),
    confirm_digit_gid: bool = False,
) -> dict:
    """Detect the GID column and level, check every row, and store the cells with a manifest."""
    try:
        raw = json.loads(value_fields)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="value_fields is not valid JSON") from exc
    if not isinstance(raw, list):
        raise HTTPException(status_code=422, detail="value_fields must be a JSON array")
    try:
        stored = store_csv(
            load_registry(),
            data_root(),
            await file.read(),
            value_fields=parse_value_fields(raw),
            confirm_digit_gid=confirm_digit_gid,
        )
    except DatasetExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ImportRejected, GeosquareError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return stored.summary()


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


@app.post("/crosswalks/{name}/apply")
async def apply_crosswalk(
    name: str,
    file: UploadFile = File(...),
    dataset_id: str = Form(...),
    value_columns: str = Form(...),
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
    if crosswalk.manifest["domain_code"] != "ID":
        raise HTTPException(status_code=422, detail="platform datasets currently support domain_code ID only")
    columns = _requested_columns(value_columns)
    try:
        source = read_csv(await file.read())
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
    fields = [ValueField(column, "numeric", "COUNT", None) for column in columns]
    try:
        stored = store_csv(
            load_registry(),
            data_root(),
            output,
            value_fields=fields,
            dataset_id=dataset_id,
            confirm_digit_gid=True,
        )
    except DatasetExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ImportRejected, GeosquareError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"summary": stored.summary(), "conservation": conservation}


@app.get("/datasets/{dataset_id}/rollup")
def rollup_dataset(
    dataset_id: str,
    level: int = Query(...),
    sum_columns: str = Query(..., alias="sum"),
) -> dict:
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
