"""Map data routes that keep cell geometry in the browser."""

from __future__ import annotations

import math
import os
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from geosquare_v2 import GeosquareGrid, read_cell_dataset
from geosquare_v2.errors import GeosquareError
from pyproj import Transformer

from .cellstore import DATASET_ID_PATTERN
from .registry import load_registry

router = APIRouter()
DATA_ENV = "GSQ_PLATFORM_DATA"
MAP_ROOT = Path(__file__).resolve().parents[2] / "web" / "map"
MAP_PACKAGE = MAP_ROOT / "pkg"


def _data_root() -> Path:
    return Path(os.environ.get(DATA_ENV, "data/cells"))


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    parts = raw.split(",")
    if len(parts) != 4:
        raise HTTPException(status_code=422, detail="bbox must be min_lon,min_lat,max_lon,max_lat")
    try:
        min_lon, min_lat, max_lon, max_lat = (float(part) for part in parts)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="bbox values must be numbers") from exc
    if not all(math.isfinite(value) for value in (min_lon, min_lat, max_lon, max_lat)):
        raise HTTPException(status_code=422, detail="bbox values must be finite")
    if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180):
        raise HTTPException(status_code=422, detail="longitude must be between -180 and 180")
    if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        raise HTTPException(status_code=422, detail="latitude must be between -90 and 90")
    if min_lon > max_lon or min_lat > max_lat:
        raise HTTPException(status_code=422, detail="bbox minimums must not exceed maximums")
    return min_lon, min_lat, max_lon, max_lat


@lru_cache(maxsize=16)
def _wgs84_transformer(crs_wkt2: str) -> Transformer:
    return Transformer.from_crs(crs_wkt2, "EPSG:4326", always_xy=True)


def _cell_centre_wgs84(grid: GeosquareGrid, gid: str, transformer: Transformer) -> tuple[float, float]:
    bounds = grid.projected_bounds(gid)
    x = (bounds.min_x + bounds.max_x) / 2
    y = (bounds.min_y + bounds.max_y) / 2
    return transformer.transform(x, y)


@router.get("/datasets/{dataset_id}/cells")
def get_map_cells(
    dataset_id: str,
    bbox: str = Query(..., description="min_lon,min_lat,max_lon,max_lat"),
    limit: int = Query(1000, ge=1),
) -> dict[str, list[dict]]:
    """Return GIDs and value columns whose grid-cell centres fall in a WGS84 bbox."""
    if not DATASET_ID_PATTERN.fullmatch(dataset_id):
        raise HTTPException(status_code=404, detail="no such dataset")
    path = _data_root() / dataset_id
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="no such dataset")

    min_lon, min_lat, max_lon, max_lat = _parse_bbox(bbox)
    try:
        dataset = read_cell_dataset(path, registry=load_registry())
        manifest = dataset.manifest
        profile = load_registry().get(manifest.domain_code)
        grid = GeosquareGrid(profile)
        transformer = _wgs84_transformer(profile.crs_wkt2)
    except GeosquareError as exc:
        raise HTTPException(status_code=422, detail=f"dataset fails validation: {exc}") from exc

    value_names = [field["name"] for field in manifest.value_fields]
    table = dataset.table.select(["gid", *value_names])
    rows: list[dict] = []
    for row in table.to_pylist():
        lon, lat = _cell_centre_wgs84(grid, row["gid"], transformer)
        if min_lon <= lon <= max_lon and min_lat <= lat <= max_lat:
            rows.append(row)
            if len(rows) >= limit:
                break
    return {"cells": rows}


@router.get("/map")
def map_page() -> FileResponse:
    """Serve the browser map page."""
    return FileResponse(MAP_ROOT / "index.html", media_type="text/html")


@router.get("/map/pkg/{filename:path}")
def map_package(filename: str) -> FileResponse:
    """Serve the locally copied, ignored WASM package files."""
    return _map_file(MAP_PACKAGE, filename)


@router.get("/map/js/{filename:path}")
def map_script(filename: str) -> FileResponse:
    """Serve the vendored browser modules, such as the engine's rollup.mjs."""
    return _map_file(MAP_ROOT / "js", filename)


def _map_file(folder: Path, filename: str) -> FileResponse:
    candidate = (folder / filename).resolve()
    try:
        candidate.relative_to(folder.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="no such map asset") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="no such map asset")
    return FileResponse(candidate)
