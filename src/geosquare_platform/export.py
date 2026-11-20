"""K-anonymous dataset export."""

from __future__ import annotations

import math
from typing import Any

import geosquare_engine as gs
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


_SUPPORTED_SEMANTICS = frozenset({"COUNT", "TOTAL"})


def _number(value: Any, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"field {field!r} must be numeric for export") from exc
    if not math.isfinite(number):
        raise HTTPException(status_code=422, detail=f"field {field!r} must be finite for export")
    return number


def _coarsest_level(gid: str) -> int:
    """Find the root level through the engine hierarchy, without grid math."""
    current = gid
    while True:
        parent = gs.parent(current)
        if parent is None:
            return len(current)
        current = parent


def _aggregate(row: dict[str, Any], count_field: str, total_fields: list[str]) -> dict[str, Any]:
    return {
        "count": _number(row[count_field], field=count_field),
        "totals": {field: _number(row[field], field=field) for field in total_fields},
        "source_cell_count": 1,
    }


def _merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    target["count"] += source["count"]
    for field, value in source["totals"].items():
        target["totals"][field] += value
    target["source_cell_count"] += source["source_cell_count"]


@router.get("/datasets/{dataset_id}/export")
def export_dataset(
    dataset_id: str,
    k: int = Query(5),
    count_field: str = Query(..., alias="count"),
) -> dict[str, Any]:
    """Export a disjoint k-anonymous rollup of one cell dataset.

    The dataset's declared COUNT field is the anonymity count. Declared TOTAL
    fields are summed through parent cells. Other semantics are dropped and
    reported; no geometry or grid math is performed by the platform.

    k defaults to 5 and must be at least 2.
    """
    if k < 2:
        raise HTTPException(status_code=422, detail="k must be at least 2")

    # Importing here keeps app.py's router registration at the end and avoids a
    # module import cycle while the application defines its shared dataset helper.
    from .app import _dataset_table

    metadata, source_rows = _dataset_table(dataset_id)
    fields = metadata["manifest"]["value_fields"]
    fields_by_name = {field["name"]: field for field in fields}
    count_metadata = fields_by_name.get(count_field)
    if count_metadata is None or count_metadata.get("semantics") != "COUNT":
        raise HTTPException(status_code=422, detail=f"count field {count_field!r} must have COUNT semantics")

    total_fields = [field["name"] for field in fields if field.get("semantics") == "TOTAL"]
    dropped_fields = [field["name"] for field in fields if field.get("semantics") not in _SUPPORTED_SEMANTICS]
    residual = {row["gid"]: _aggregate(row, count_field, total_fields) for row in source_rows}
    input_level = metadata["level"]
    coarsest_level = _coarsest_level(next(iter(residual), ""))
    current_level = input_level
    emitted: list[dict[str, Any]] = []
    suppressed_cell_count = 0
    suppressed_count = 0.0

    while residual:
        next_residual: dict[str, dict[str, Any]] = {}
        for gid in sorted(residual):
            aggregate = residual[gid]
            if aggregate["count"] >= k:
                emitted.append(
                    {
                        "gid": gid,
                        "level": current_level,
                        "count": aggregate["count"],
                        "totals": aggregate["totals"],
                        "source_cell_count": aggregate["source_cell_count"],
                    }
                )
            elif current_level == coarsest_level:
                suppressed_cell_count += aggregate["source_cell_count"]
                suppressed_count += aggregate["count"]
            else:
                parent_gid = gs.parent(gid)
                if parent_gid is None:
                    raise HTTPException(status_code=422, detail="dataset hierarchy is inconsistent")
                parent = next_residual.setdefault(
                    parent_gid,
                    {"count": 0.0, "totals": {field: 0.0 for field in total_fields}, "source_cell_count": 0},
                )
                _merge(parent, aggregate)

        if not next_residual:
            break
        residual = next_residual
        current_level -= 1

    input_count = sum(_number(row[count_field], field=count_field) for row in source_rows)
    emitted_count = sum(row["count"] for row in emitted)
    assert len({row["gid"] for row in emitted}) == len(emitted)
    assert all(row["count"] >= k for row in emitted)
    assert math.isclose(emitted_count + suppressed_count, input_count, rel_tol=1e-9, abs_tol=1e-9)

    return {
        "k": k,
        "count_field": count_field,
        "input_level": input_level,
        "rows": emitted,
        "suppressed_cell_count": suppressed_cell_count,
        "suppressed_count": suppressed_count,
        "dropped_fields": dropped_fields,
    }
