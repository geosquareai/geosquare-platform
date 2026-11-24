"""Step 1 of the platform: read a CSV, find the GID column and level, check every row.

Uses the geosquare-v2 reference detector. It never changes a value.
The platform serves one domain for now: ID.
"""

from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass, field

import geosquare_engine as gs
from geosquare_engine import detect_table

DOMAIN = "ID"
KIND_GID, KIND_URI = "gid", "uri"
STATUS_CERTAIN, STATUS_NEEDS_CONFIRMATION = "certain", "needs_confirmation"
MAX_ROWS = 2_000_000
MAX_PROBLEMS = 100  # row problems returned in one reply; the reason gives the total
# Bare GIDs that are all digits could be plain codes. We accept them only if the user says so.
_ACCEPTED = {STATUS_CERTAIN}


class ImportRejected(ValueError):
    """The file cannot be imported. The message says why."""


class RowsRejected(ImportRejected):
    """Some GID rows fail the checks. `problems` names each bad row, one line each."""

    def __init__(self, message: str, problems: list[str]) -> None:
        super().__init__(message)
        self.problems = problems


@dataclass
class ImportReport:
    ok: bool
    rows: int
    key_column: str | None
    key_kind: str | None
    level: int | None
    status: str
    reason: str
    columns: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def read_csv(data: bytes) -> dict[str, list[str | None]]:
    """Read CSV bytes into columns of text. Empty cells become None."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportRejected("file is not UTF-8 text") from exc
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ImportRejected("file is empty") from None
    if len(set(header)) != len(header):
        raise ImportRejected("duplicate column names in header")
    cols: dict[str, list[str | None]] = {name: [] for name in header}
    for n, row in enumerate(reader, start=1):
        if n > MAX_ROWS:
            raise ImportRejected(f"more than {MAX_ROWS} rows")
        if len(row) != len(header):
            raise ImportRejected(f"row {n} has {len(row)} cells, header has {len(header)}")
        for name, cell in zip(header, row):
            cols[name].append(cell if cell != "" else None)
    return cols


def _describe(d: dict) -> dict:
    keep = ("column", "kind", "status", "reason", "levels", "invalid_examples")
    return {**{k: d[k] for k in keep}, "valid_ratio": round(d["valid_ratio"], 4)}


def gid_problems(gids: list[str | None]) -> list[str]:
    """Check each GID row, with the rules of `gsq check`: empty, invalid, duplicate, mixed level.

    Returns one line per bad row. An empty list means the column passes.
    """
    problems: list[str] = []
    rows_by_gid: dict[str, list[int]] = {}
    levels: dict[int, list[int]] = {}
    for row, gid in enumerate(gids, start=1):
        if gid is None:
            problems.append(f"row {row}: GID is empty")
            continue
        try:
            level, _, _ = gs.decode(gid)
        except ValueError:
            problems.append(f"row {row}: invalid GID {gid!r}")
            continue
        rows_by_gid.setdefault(gid, []).append(row)
        levels.setdefault(level, []).append(row)
    for gid, rows in rows_by_gid.items():
        if len(rows) > 1:
            problems.append(f"duplicate GID {gid}: rows {', '.join(map(str, rows))}")
    if len(levels) > 1:
        first = min(levels, key=lambda lv: levels[lv][0])
        for level, rows in levels.items():
            if level != first:
                problems.extend(f"row {row}: level {level}, the first GID is level {first}" for row in rows)
    return problems


def analyse(data: bytes, *, confirm_digit_gid: bool = False) -> ImportReport:
    """Find the one key column. Report it, or say why there is none."""
    cols = read_csv(data)
    rows = len(next(iter(cols.values()))) if cols else 0
    if rows == 0:
        raise ImportRejected("file has a header but no rows")

    # Text columns only. A CSV has no types, so every column is read as text (spec 14).
    found = detect_table({k: ("string", v) for k, v in cols.items()}, domain=DOMAIN)
    keys = [d for d in found if d["kind"] in (KIND_GID, KIND_URI) and d["status"] != "rejected"]
    described = [_describe(d) for d in found]

    if not keys:
        # No column passed the 99% test. Still list the rows of the column closest to a GID column.
        best = max((d for d in found if d["kind"] == KIND_GID), key=lambda d: d["valid_ratio"], default=None)
        if best is not None and best["valid_ratio"] > 0:
            problems = gid_problems(cols[best["column"]])
            if problems:
                reason = f"{len(problems)} row problem(s), first: {problems[0]}"
                return ImportReport(False, rows, best["column"], KIND_GID, None, "invalid_rows", reason, described,
                                    problems[:MAX_PROBLEMS])
        return ImportReport(False, rows, None, None, None, "rejected",
                            "no column holds valid GIDs", described)
    if len(keys) > 1:
        names = ", ".join(d["column"] for d in keys)
        return ImportReport(False, rows, None, None, None, "ambiguous",
                            f"more than one key column: {names}", described)

    key = keys[0]
    if key["status"] == STATUS_NEEDS_CONFIRMATION and not confirm_digit_gid:
        return ImportReport(False, rows, key["column"], key["kind"], None, key["status"], key["reason"], described)
    if key["status"] not in _ACCEPTED | {STATUS_NEEDS_CONFIRMATION}:
        return ImportReport(False, rows, key["column"], key["kind"], None, key["status"], key["reason"], described)
    if key["kind"] == KIND_GID:
        problems = gid_problems(cols[key["column"]])
        if problems:
            reason = f"{len(problems)} row problem(s), first: {problems[0]}"
            return ImportReport(False, rows, key["column"], key["kind"], None, "invalid_rows", reason, described,
                                problems[:MAX_PROBLEMS])
    if len(key["levels"]) != 1:
        return ImportReport(False, rows, key["column"], key["kind"], None, "mixed_levels",
                            f"column mixes levels {key['levels']}; one level per file", described)
    if key["kind"] == KIND_URI and tuple(key["domain_codes"]) != (DOMAIN,):
        return ImportReport(False, rows, key["column"], key["kind"], None, "wrong_domain",
                            f"URIs are in domain {key['domain_codes']}, platform serves {DOMAIN}", described)
    if key["valid_ratio"] < 1.0:
        return ImportReport(False, rows, key["column"], key["kind"], None, "invalid_rows",
                            f"{key['valid_ratio']:.2%} valid; examples: {key['invalid_examples']}", described)
    return ImportReport(True, rows, key["column"], key["kind"], key["levels"][0], "certain",
                        "key column and level found", described)
