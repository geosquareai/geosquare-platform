"""Runs the Node tests for the browser rollup (web/map/js/rollup.mjs).

Needs node and the local WASM build in web/map/pkg (copied from the engine, not in git).
Without them the test skips. Run the Node tests alone with: node --test tests/js/rollup.test.mjs
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.skipif(not (ROOT / "web/map/pkg/geosquare_wasm.js").is_file(), reason="WASM build not copied to web/map/pkg")
def test_browser_rollup_matches_hand_values():
    result = subprocess.run(
        ["node", "--test", "tests/js/rollup.test.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
