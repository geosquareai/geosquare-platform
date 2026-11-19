from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from geosquare_v2.errors import GeosquareError

from .cellstore import DATASET_ID_PATTERN, DatasetExists, parse_value_fields, read_dataset, store_csv
from .importer import ImportRejected, analyse
from .registry import load_registry
from .map_routes import router as map_router

app = FastAPI(title="GeoSquare Platform", version="0.1.0")
app.include_router(map_router)

DATA_ENV = "GSQ_PLATFORM_DATA"  # folder for stored datasets; default data/cells


def data_root() -> Path:
    return Path(os.environ.get(DATA_ENV, "data/cells"))


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
    if not DATASET_ID_PATTERN.fullmatch(dataset_id):
        raise HTTPException(status_code=404, detail="no such dataset")
    path = data_root() / dataset_id
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="no such dataset")
    try:
        return read_dataset(load_registry(), path)
    except GeosquareError as exc:
        raise HTTPException(status_code=422, detail=f"dataset fails validation: {exc}") from exc
