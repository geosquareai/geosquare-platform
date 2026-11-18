"""Loads the signed ID registry. The cell store checks every dataset against it."""

from __future__ import annotations

import base64
from functools import lru_cache

from geosquare_v2 import DbRegistryLoader, DomainRegistry

# Public Ed25519 key for the registry release. Same value as release/registry-trust.json in geosquare-v2.
TRUST = {"geosquare-registry-2026-09": base64.b64decode("T6ikwn74d12UFk21VLKMFDhl/b+mLV6qo8MRHkg9E3Q=")}


@lru_cache(maxsize=1)
def load_registry() -> DomainRegistry:
    """Verify the signed registry once per process and return it."""
    return DbRegistryLoader(TRUST).load()
