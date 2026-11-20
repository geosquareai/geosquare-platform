"""Crosswalk format 1.0 validation and storage helpers.

The same rules belong in ``geosquare_v2.crosswalk`` (roadmap S6), so the
platform can import that module later instead of maintaining a second validator.
"""

from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import dataclass
from typing import Any

import geosquare_engine as gs

from .cellstore import DATASET_ID_PATTERN
from .importer import ImportRejected, read_csv

CROSSWALK_FORMAT_VERSION = "1.0"
WEIGHT_KINDS = frozenset(("area", "luas", "bangunan", "populasi", "lainnya"))
# The eleven ASEAN domains in registry 2.0.0 (same list as geosquare_v2.crosswalk).
KNOWN_DOMAIN_CODES = frozenset(("BN", "ID", "KH", "LA", "MM", "MY", "PH", "SG", "TH", "TL", "VN"))
_REQUIRED_COLUMNS = frozenset(("sumber_id", "gid", "bobot"))


class CrosswalkRejected(ValueError):
    """The crosswalk does not satisfy format 1.0."""


@dataclass(frozen=True)
class Crosswalk:
    manifest: dict[str, Any]
    rows: tuple[tuple[str, str, float], ...]

    @property
    def sources(self) -> int:
        return len({source_id for source_id, _, _ in self.rows})

    @property
    def cells(self) -> int:
        return len({gid for _, gid, _ in self.rows})

    def summary(self) -> dict[str, Any]:
        return {"sources": self.sources, "rows": len(self.rows), "cells": self.cells}


def _level(value: Any) -> int:
    if isinstance(value, bool):
        raise CrosswalkRejected("level must be an integer")
    try:
        level = int(value)
    except (TypeError, ValueError) as exc:
        raise CrosswalkRejected("level must be an integer") from exc
    if level < 0 or level > 14:
        raise CrosswalkRejected("level must be an integer from 0 to 14")
    if isinstance(value, str) and str(level) != value.strip():
        raise CrosswalkRejected("level must be a non-negative integer")
    return level


def validate_crosswalk(
    data: bytes,
    *,
    domain_code: str,
    level: int,
    weight_kind: str,
) -> Crosswalk:
    """Validate CSV rows and return normalized crosswalk data."""
    if not isinstance(domain_code, str) or domain_code not in KNOWN_DOMAIN_CODES:
        raise CrosswalkRejected(f"domain_code must be one of {sorted(KNOWN_DOMAIN_CODES)}")
    level = _level(level)
    if weight_kind not in WEIGHT_KINDS:
        raise CrosswalkRejected(f"weight_kind must be one of {sorted(WEIGHT_KINDS)}")
    try:
        columns = read_csv(data)
    except ImportRejected as exc:
        raise CrosswalkRejected(str(exc)) from exc
    if set(columns) != _REQUIRED_COLUMNS:
        missing = sorted(_REQUIRED_COLUMNS - set(columns))
        extra = sorted(set(columns) - _REQUIRED_COLUMNS)
        detail = []
        if missing:
            detail.append(f"missing columns {missing}")
        if extra:
            detail.append(f"unknown columns {extra}")
        raise CrosswalkRejected("; ".join(detail))
    if not columns["sumber_id"]:
        raise CrosswalkRejected("crosswalk has no rows")

    rows: list[tuple[str, str, float]] = []
    seen: set[tuple[str, str]] = set()
    totals: dict[str, float] = {}
    for row_number, (source_id, gid, raw_weight) in enumerate(
        zip(columns["sumber_id"], columns["gid"], columns["bobot"], strict=True), start=2
    ):
        if not source_id or not gid or raw_weight is None:
            raise CrosswalkRejected(f"row {row_number}: sumber_id, gid, and bobot are required")
        try:
            decoded_level = gs.decode(gid)
        except Exception as exc:  # engine exposes several codec-specific errors
            raise CrosswalkRejected(f"row {row_number}: invalid gid {gid!r}") from exc
        if decoded_level[0] != level:
            raise CrosswalkRejected(
                f"row {row_number}: gid {gid!r} is level {decoded_level[0]}, expected level {level}"
            )
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError) as exc:
            raise CrosswalkRejected(f"row {row_number}: bobot must be numeric") from exc
        if not math.isfinite(weight) or weight <= 0:
            raise CrosswalkRejected(f"row {row_number}: bobot must be finite and > 0")
        key = (source_id, gid)
        if key in seen:
            raise CrosswalkRejected(f"row {row_number}: duplicate (sumber_id, gid)")
        seen.add(key)
        rows.append((source_id, gid, weight))
        totals[source_id] = totals.get(source_id, 0.0) + weight

    bad = [source_id for source_id, total in totals.items() if abs(total - 1.0) > 1e-9]
    if bad:
        raise CrosswalkRejected(f"weights for sumber_id {bad[0]!r} sum to {totals[bad[0]]:.17g}, not 1")

    manifest = {
        "format_version": CROSSWALK_FORMAT_VERSION,
        "domain_code": domain_code,
        "level": level,
        "weight_kind": weight_kind,
        "sources": len(totals),
        "rows": len(rows),
        "cells": len({gid for _, gid, _ in rows}),
    }
    return Crosswalk(manifest=manifest, rows=tuple(rows))


def crosswalk_csv(crosswalk: Crosswalk) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("sumber_id", "gid", "bobot"))
    for source_id, gid, weight in crosswalk.rows:
        writer.writerow((source_id, gid, repr(weight)))
    return output.getvalue().encode("utf-8")


def load_crosswalk(path) -> Crosswalk:
    """Read and validate a stored crosswalk and its manifest."""
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format_version") != CROSSWALK_FORMAT_VERSION:
        raise CrosswalkRejected("unsupported crosswalk format_version")
    if not DATASET_ID_PATTERN.fullmatch(path.name):
        raise CrosswalkRejected("invalid crosswalk name")
    return validate_crosswalk(
        (path / "crosswalk.csv").read_bytes(),
        domain_code=manifest.get("domain_code"),
        level=manifest.get("level"),
        weight_kind=manifest.get("weight_kind"),
    )
