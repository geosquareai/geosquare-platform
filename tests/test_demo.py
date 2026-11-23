from __future__ import annotations

import os
from pathlib import Path

import geosquare_engine as gs
import pytest

from geosquare_platform.demo import DemoFailed, main, run


def _small_csv(path: Path) -> Path:
    rows = [
        ("gid", "share"),
        (gs.encode(9, 10, 3), "0.25"),
        (gs.encode(9, 11, 3), "1"),
        (gs.encode(9, 12, 3), "0.5"),
    ]
    path.write_text("\n".join(",".join(row) for row in rows) + "\n", encoding="utf-8")
    return path


def test_demo_passes_on_a_small_csv(tmp_path, capsys):
    csv_path = _small_csv(tmp_path / "cells.csv")
    stored = run(csv_path, tmp_path / "cells", numeric=["share"], license_text="internal-test", share_policy="internal")

    assert stored["rows"] == 3 and stored["level"] == 9
    output = capsys.readouterr().out
    assert "Detected: GID column 'gid', level 9, 3 rows" in output
    assert output.rstrip().endswith("PASS")
    assert "GSQ_PLATFORM_DATA" not in os.environ


def test_demo_refuses_a_second_run_of_the_same_file(tmp_path):
    csv_path = _small_csv(tmp_path / "cells.csv")
    run(csv_path, tmp_path / "cells", numeric=["share"], license_text="internal-test", share_policy="internal")
    with pytest.raises(DemoFailed, match="409"):
        run(csv_path, tmp_path / "cells", numeric=["share"], license_text="internal-test", share_policy="internal")


def test_demo_needs_a_value_column(tmp_path):
    csv_path = _small_csv(tmp_path / "cells.csv")
    with pytest.raises(DemoFailed, match="value column"):
        run(csv_path, tmp_path / "cells", license_text="internal-test", share_policy="internal")
    assert main(
        [str(csv_path), "--data-root", str(tmp_path / "cells"), "--license", "internal-test", "--share-policy", "internal"]
    ) == 1


@pytest.mark.skipif("GSQ_KYL_CSV" not in os.environ, reason="set GSQ_KYL_CSV to the KYL cell CSV")
def test_demo_on_kyl_cells(tmp_path):
    stored = run(
        Path(os.environ["GSQ_KYL_CSV"]),
        tmp_path / "cells",
        numeric=["share_in_aoi"],
        license_text="internal-test",
        share_policy="internal",
    )
    assert stored["level"] == 12
    assert stored["rows"] == 50676
