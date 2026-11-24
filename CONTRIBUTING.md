# Contributing

Thank you for your interest in geosquare-platform. This page explains how to make a change.

## Before you start

- Read the [development guide](docs/development.md) for the code rules and version pins.
- For a security problem, do not open a public issue. Follow [SECURITY.md](SECURITY.md).

## Workflow

1. Branch from `main`. Do not commit to `main`.
2. Make small, focused commits. Write the message in the same style as the history: `feat:`, `fix:`, `docs:`, `test:`, or `chore:`, then a short summary.
3. Update the pages in `docs/` when behaviour changes.
4. Open a pull request against `main`.
5. Wait for review. Do not merge your own pull request.

## Run the test suite locally

This repository has no GitHub Actions or other CI. Tests run on your machine only, so run them before you open a pull request.

```zsh
uv sync --extra test
uv run pytest -q
```

The `test` extra installs `geosquare-grid`, which is needed for the V1 parity tests. See [Development](docs/development.md#tests).

Paste the summary line of the run into the pull request description. The line looks like this:

```text
<passed> passed, <skipped> skipped, <failed> failed
```

Use the real counts from your run. If a test fails or you skip one, say which one and why.

## Rules for the test data

- Tests use synthetic data only.
- Do not commit real datasets, CSV exports, or Parquet files. The `data/` folder is in `.gitignore`.
- Do not commit `web/map/pkg/`. It is a local copy of the engine WASM build.

## Code rules

- Do not rewrite grid rules here. Call `geosquare-engine`.
- Do not rewrite dataset rules here. Call `geosquare-v2`.
- Invalid input must fail with a clear message. Do not fix it silently.
- Per-cell results may only go through internal routes, unless the change is explicitly about a public route. Read [Privacy and internal routes](docs/privacy-and-internal-routes.md) first.

## Documentation language

- Pages in `docs/` are written in English. Add a short Bahasa Indonesia summary at the bottom of a page when it helps the team.
- The README keeps English first, with the Bahasa Indonesia text at the bottom.
