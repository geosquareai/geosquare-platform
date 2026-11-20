from __future__ import annotations

import random

import pytest
from geosquare_grid import GeosquareGrid as V1Grid
from geosquare_v2.migration import LegacyV1Decoder


def test_v1_bounds_match_decoder_for_200_random_asean_points() -> None:
    grid = V1Grid()
    decoder = LegacyV1Decoder()
    rng = random.Random(20261119)
    for _ in range(200):
        longitude = rng.uniform(95.0, 141.0)
        latitude = rng.uniform(-11.0, 6.0)
        level = rng.randint(1, 14)
        gid = grid.lonlat_to_gid(longitude, latitude, level)
        assert grid.gid_to_bound(gid) == pytest.approx(decoder.decode(gid).bounds, abs=1e-9)
