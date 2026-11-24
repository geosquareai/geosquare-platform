"""Step 5f of the platform: a raster (GeoTIFF or COG) becomes a cell dataset (L14).

Pixels come from the engine's DuckDB `read_raster`, which reads only the tiles the bbox touches.
Each pixel centre gets its cell from `point_to_cell`. A cell gets the mean of its pixel centres,
as the hazard tables were built. The cells go through `store_csv`, so the same checks and manifest
apply as for any CSV upload.

`read_raster` lives in the engine's DuckDB extension, which is not on PyPI. Set
`GSQ_DUCKDB_EXTENSION` to a built `geosquare_v2.duckdb_extension` (DuckDB 1.5.x). It must come from
engine commit 7e6da90 or newer (PR #41, not on engine main yet): older builds keep pixels outside the bbox.

shortcut: a cell on the edge of the bbox gets the mean of the pixels inside the bbox only.
Cut the raster to the area first. Upgrade with a share_in_aoi column when edges matter.
"""

from __future__ import annotations

import csv
import io
import os
from collections.abc import Sequence
from pathlib import Path

import duckdb

from .cellstore import Governance, StoredDataset, ValueField, store_csv
from .importer import ImportRejected
from .registry import load_registry

EXTENSION_ENV = "GSQ_DUCKDB_EXTENSION"
DOMAIN = "ID"


def pixel_cells(raster: Path, *, level: int, bbox: Sequence[float], extension: str) -> list[tuple[str, float]]:
    """Mean raster value per cell at `level`, for pixel centres inside `bbox` = [min_lon, min_lat, max_lon, max_lat]."""
    min_lon, min_lat, max_lon, max_lat = (float(v) for v in bbox)
    if not isinstance(level, int) or not 0 <= level <= 15:
        raise ImportRejected("level must be a whole number from 0 to 15")
    con = duckdb.connect(config={"allow_unsigned_extensions": "true"})
    try:
        con.execute(f"LOAD {_sql_string(extension)}")
        rows = con.execute(
            f"SELECT point_to_cell('{DOMAIN}', lon, lat, {level}).gid AS gid, avg(value) AS value "
            "FROM read_raster(?, bbox := [?, ?, ?, ?]) "
            "WHERE value IS NOT NULL GROUP BY gid ORDER BY gid",
            [str(raster), min_lon, min_lat, max_lon, max_lat],
        ).fetchall()
    except duckdb.Error as exc:
        raise ImportRejected(f"raster could not be read: {str(exc).splitlines()[0]}") from exc
    finally:
        con.close()
    return [(gid, float(value)) for gid, value in rows]


def raster_to_cells(
    root: Path,
    raster: Path,
    *,
    level: int,
    bbox: Sequence[float],
    value_field: ValueField,
    governance: Governance,
    extension: str | None = None,
) -> StoredDataset:
    """Store the raster's cell means as a cell dataset under `root`. The uploader states the governance."""
    extension = extension or os.environ.get(EXTENSION_ENV)
    if not extension:
        raise ImportRejected(f"set {EXTENSION_ENV} to the built geosquare_v2.duckdb_extension")
    if value_field.logical_type != "numeric":
        raise ImportRejected("a raster value must be numeric")
    cells = pixel_cells(raster, level=level, bbox=bbox, extension=extension)
    if not cells:
        raise ImportRejected("the raster has no valid pixels inside the bbox")
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["gid", value_field.name])
    writer.writerows(cells)
    # The GIDs come from point_to_cell, not from a user file, so the all-digit confirmation does not apply.
    return store_csv(
        load_registry(),
        root,
        buffer.getvalue().encode("utf-8"),
        value_fields=[value_field],
        governance=governance,
        confirm_digit_gid=True,
    )


def _sql_string(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"
