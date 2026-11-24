# Development

This page is for people who change the platform code.

## Setup

```zsh
uv sync --extra test
```

This command reads `uv.lock`. The locked versions are the ones the tests run against.

## Version pins

These pins match `pyproject.toml`.

| Package | Version | Why |
|---|---|---|
| `geosquare-engine` | `>=0.3.0,<0.4` | Current PyPI release. The starter notebook and the `df.gsq` accessors use it |
| `geosquare-v2[table,registry,geo]` | `==1.3.0` | Exact version. `time_store` and `compact_store` exist since 1.2.0 |
| `geosquare-grid` | `==1.1.1` (test extra only) | V1 parity tests. This package is GPL, so it is not in the runtime |

When a pin changes, run `uv lock --upgrade-package <package>`, then run the full test suite.

## Tests

```zsh
uv run pytest -q
```

Two files need `geosquare-grid` from the test extra:

- `tests/test_v1_parity.py`
- `tests/test_migrate_v1.py`

If that package is not installed, these two files fail at collection. To run the other tests:

```zsh
uv run pytest -q --ignore=tests/test_v1_parity.py --ignore=tests/test_migrate_v1.py
```

Tests use synthetic data. Do not commit real data. The `data/` folder is in `.gitignore`.

## Code rules

- Do not rewrite grid rules here. Call `geosquare-engine`.
- Do not rewrite dataset rules here. Call `geosquare-v2`.
- Invalid input must fail with a clear message. Do not fix it silently.
- Results that contain per-cell values may only go through internal routes.
- If a test uses a package version, read the version from the installed package. Do not write a fixed number.

## Run the server locally

```zsh
GSQ_PLATFORM_DATA=data/cells uv run uvicorn geosquare_platform.app:app --reload
```

To try the internal routes on your own machine:

```zsh
GSQ_INTERNAL_ROUTES=1 GSQ_PLATFORM_DATA=data/cells uv run uvicorn geosquare_platform.app:app --reload
```

The browser map at `/map` reads the Parquet file through an internal route. Without `GSQ_INTERNAL_ROUTES=1`, the map cannot load the data.

Do not run the server with this flag on a network that the public can reach.

## Contributing

- Branch from `main`. Do not work directly on `main`.
- Open a pull request. Wait for review before merging.
- Update the pages in `docs/` when behaviour changes.

## Browser map (WASM)

The map at `/map` reads Parquet files directly in the browser, then rolls them up to the level for the zoom (E19).

The WASM package from `geosquare-engine` is not on npm yet. Copy the local build into `web/map/pkg/`. This folder is not committed:

```zsh
cp -R ../geosquare-engine/bindings/wasm/pkg web/map/pkg
```

`web/map/js/rollup.mjs` is a copy of `geosquare-engine/bindings/wasm/js/rollup.mjs`. Keep it the same as the engine file.

The map page loads maplibre and hyparquet from CDNs, so the browser needs internet access.

---

## Ringkasan Bahasa Indonesia

Halaman ini untuk pengembang. Setup dengan `uv sync --extra test`. Tes dengan `uv run pytest -q`. Dua file tes butuh `geosquare-grid` dari extra `test`. Peta di `/map` butuh paket WASM di `web/map/pkg/` dan `GSQ_INTERNAL_ROUTES=1` di mesin lokal. Jangan pernah menjalankan server dengan flag itu di jaringan publik.
