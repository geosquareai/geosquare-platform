from __future__ import annotations

import csv
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from geosquare_grid import GeosquareGrid as V1Grid
from geosquare_v2 import read_cell_dataset

from geosquare_platform.crosswalk import load_crosswalk
from geosquare_platform.migrate_v1 import convert_v1, main


FIELDS = json.dumps(
    [{"name": "count", "logical_type": "numeric", "semantics": "COUNT", "unit": None}]
)


def write_input(path: Path, rows: list[tuple[str, str]], gid_column: str = "gid_v1") -> None:
    path.write_text(
        f"{gid_column},count\n" + "\n".join(f"{gid},{count}" for gid, count in rows) + "\n",
        encoding="utf-8",
    )


def test_known_indonesia_conversion_normalizes_weights_and_writes_provenance(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GSQ_PLATFORM_DATA", str(tmp_path / "cells"))
    gid = V1Grid().lonlat_to_gid(106.8456, -6.2088, 9)
    source = tmp_path / "synthetic.csv"
    write_input(source, [(gid, "10")])

    result = convert_v1(
        input_path=source,
        domain="ID",
        level=9,
        name="ID-L9-0123456789ab",
        value_fields_json=FIELDS,
    )

    crosswalk = load_crosswalk(tmp_path / "cells" / "crosswalks" / result["crosswalk"])
    weights = [weight for source_id, _, weight in crosswalk.rows if source_id == gid]
    assert sum(weights) == pytest.approx(1.0, abs=1e-12)
    assert result["conservation"]["count"]["source_sum"] == 10.0
    assert result["conservation"]["count"]["allocated_sum"] == pytest.approx(10.0, abs=1e-9)
    assert 0 <= result["migration_report"]["per_source_u"][gid] < 1e-6

    report_path = tmp_path / "cells" / "ID-L9-0123456789ab" / "migration_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report == result["migration_report"]
    assert report["source_file_name"] == "synthetic.csv"
    assert report["input_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert report["input_row_count"] == 1
    assert report["distinct_v1_gid_count"] == 1
    assert report["v1_reference"] == "geosquare-grid==1.1.1 (verified by tests/test_v1_parity.py)"
    assert report["geosquare_v2_version"] == "1.0.0"
    assert report["profile_version"]
    assert report["registry_version"] == "2.0.0"
    assert report["domain"] == "ID"
    assert report["level"] == 9
    assert report["method"] == "v1_cell_area_crosswalk"
    assert report["max_unallocated"] == 1e-6
    assert report["sources_with_unallocated"] == []

    stored = read_cell_dataset(tmp_path / "cells" / "ID-L9-0123456789ab").table.to_pylist()
    assert sum(row["count"] for row in stored) == pytest.approx(10.0, abs=1e-9)


def test_root_edge_straddling_coverage_is_refused_by_default_and_allowed_with_threshold(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("GSQ_PLATFORM_DATA", str(tmp_path / "cells"))
    # N3N is a V1 L3 cell found by searching levels 2-5 near the ID root edge.
    gid = "N3N"
    source = tmp_path / "straddling.csv"
    write_input(source, [(gid, "10")])

    assert (
        main(
            [
                "--input",
                str(source),
                "--domain",
                "ID",
                "--level",
                "5",
                "--name",
                "ID-L5-abcdefabcdef",
                "--value-fields",
                FIELDS,
            ]
        )
        == 2
    )
    error = capsys.readouterr().err
    assert "unallocated coverage" in error
    assert gid in error
    assert "u=" in error
    assert not (tmp_path / "cells" / "ID-L5-abcdefabcdef").exists()

    result = convert_v1(
        input_path=source,
        domain="ID",
        level=5,
        name="ID-L5-abcdefabcdef",
        value_fields_json=FIELDS,
        max_unallocated=1.0,
    )
    assert result["migration_report"]["per_source_u"][gid] > 0
    assert result["migration_report"]["sources_with_unallocated"] == [gid]


def test_jakarta_l9_to_v2_l12_has_negligible_unallocated_coverage(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GSQ_PLATFORM_DATA", str(tmp_path / "cells"))
    gid = V1Grid().lonlat_to_gid(106.8456, -6.2088, 9)
    source = tmp_path / "jakarta-l9.csv"
    write_input(source, [(gid, "10")])

    result = convert_v1(
        input_path=source,
        domain="ID",
        level=12,
        name="ID-L12-0123456789ab",
        value_fields_json=FIELDS,
    )

    u = result["migration_report"]["per_source_u"][gid]
    assert abs(u) < 1e-6
    assert result["migration_report"]["sources_with_unallocated"] == []


def test_failure_after_crosswalk_write_removes_outputs_and_allows_retry(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GSQ_PLATFORM_DATA", str(tmp_path / "cells"))
    gid = V1Grid().lonlat_to_gid(106.8456, -6.2088, 9)
    source = tmp_path / "retry.csv"
    write_input(source, [(gid, "10")])
    dataset_name = "ID-L9-555555555555"
    crosswalk_name = f"ID-L9-{hashlib.sha256(source.read_bytes()).hexdigest()[:12]}"

    def fail_after_crosswalk(*args, **kwargs):
        raise RuntimeError("forced failure after crosswalk")

    with monkeypatch.context() as patch:
        patch.setattr("geosquare_platform.migrate_v1.apply_crosswalk_data", fail_after_crosswalk)
        with pytest.raises(RuntimeError, match="forced failure"):
            convert_v1(
                input_path=source,
                domain="ID",
                level=9,
                name=dataset_name,
                value_fields_json=FIELDS,
            )

    assert not (tmp_path / "cells" / "crosswalks" / crosswalk_name).exists()
    assert not (tmp_path / "cells" / dataset_name).exists()

    result = convert_v1(
        input_path=source,
        domain="ID",
        level=9,
        name=dataset_name,
        value_fields_json=FIELDS,
    )
    assert result["crosswalk"] == crosswalk_name


def test_refusals_cover_level_15_bad_gid_and_intensive_field(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("GSQ_PLATFORM_DATA", str(tmp_path / "cells"))
    source = tmp_path / "input.csv"
    write_input(source, [("J3N2M822M222222", "1")])
    assert main(
        [
            "--input",
            str(source),
            "--domain",
            "ID",
            "--level",
            "9",
            "--name",
            "ID-L9-111111111111",
            "--value-fields",
            FIELDS,
            "--max-unallocated",
            "1",
        ]
    ) == 2
    assert "level 15" in capsys.readouterr().err

    write_input(source, [("not-a-v1-gid", "1")])
    assert main(
        [
            "--input",
            str(source),
            "--domain",
            "ID",
            "--level",
            "9",
            "--name",
            "ID-L9-222222222222",
            "--value-fields",
            FIELDS,
        ]
    ) == 2
    assert "invalid v1 GID" in capsys.readouterr().err

    write_input(source, [(V1Grid().lonlat_to_gid(106.8, -6.2, 9), "1")])
    intensive = json.dumps(
        [{"name": "mean", "logical_type": "numeric", "semantics": "MEASUREMENT", "unit": None}]
    )
    assert main(
        [
            "--input",
            str(source),
            "--domain",
            "ID",
            "--level",
            "9",
            "--name",
            "ID-L9-333333333333",
            "--value-fields",
            intensive,
        ]
    ) == 2
    assert "only COUNT or TOTAL" in capsys.readouterr().err


def test_existing_name_and_missing_required_flags_are_refused(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("GSQ_PLATFORM_DATA", str(tmp_path / "cells"))
    source = tmp_path / "input.csv"
    write_input(source, [(V1Grid().lonlat_to_gid(106.8, -6.2, 9), "1")])
    args = [
        "--input",
        str(source),
        "--domain",
        "ID",
        "--level",
        "9",
        "--name",
        "ID-L9-444444444444",
        "--value-fields",
        FIELDS,
        "--max-unallocated",
        "1",
    ]
    assert main(args) == 0
    capsys.readouterr()
    assert main(args) == 2
    assert "already exists" in capsys.readouterr().err

    completed = subprocess.run(
        [sys.executable, "-m", "geosquare_platform.migrate_v1", "--input", str(source)],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "--domain" in completed.stderr or "--level" in completed.stderr
