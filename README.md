# geosquare-platform

Status: Fase 2, langkah 1 (importer) dan 2 (cell store) sudah ada.
Peta (L3) pindah ke Fase 3, karena butuh konversi sel ke polygon di browser (CRS, E8).

## Fungsi repo ini

- Importer: upload CSV, lalu deteksi kolom GID dan level.
- Cell store: simpan data sebagai dataset sel, lengkap dengan manifest.

## Cara jalankan

```zsh
uv sync --extra test
uv run pytest -q
uv run uvicorn geosquare_platform.app:app --reload
```

## Demo

Demo menjalankan langkah upload, deteksi, simpan, dan cek manifest lewat API.

```zsh
uv run python -m geosquare_platform.demo cells.csv --numeric share
```

Data KYL (50.676 sel, level 12). Kolom `coverage_ratio` diganti nama jadi `share_in_aoi`,
karena nama itu dicadangkan kontrak. Nilainya tidak berubah.

```zsh
uv run --no-project --with geopandas python -c "
import geopandas as gpd
g = gpd.read_file('grid_cells_50m_jakbar.gpkg')
g[['gid', 'coverage_ratio']].rename(columns={'coverage_ratio': 'share_in_aoi'}).to_csv('kyl_cells_50m.csv', index=False)
"
uv run python -m geosquare_platform.demo kyl_cells_50m.csv --numeric share_in_aoi
GSQ_KYL_CSV=kyl_cells_50m.csv uv run pytest -q tests/test_demo.py
```

## API

- `POST /import/analyse`: cek CSV, kembalikan kolom GID dan level. Tidak menyimpan apa pun.
- `POST /datasets`: cek CSV, lalu simpan sebagai dataset. Field `value_fields` berisi JSON, misalnya
  `[{"name": "share", "logical_type": "numeric", "semantics": "MEASUREMENT", "unit": "ratio"}]`.
- `GET /datasets/{dataset_id}`: baca dataset lagi dan cek manifest-nya.

Dataset disimpan di `data/cells`. Ganti lokasi dengan variabel `GSQ_PLATFORM_DATA`.

## Aturan

1. Matematika grid tidak ditulis ulang di sini. Deteksi lewat `geosquare-engine`. Format dan validasi dataset lewat `geosquare-v2` 1.0.0, referensi spec.
2. Nilai tidak pernah diubah diam-diam. Baris yang salah membuat upload gagal, dan pesannya menyebut nomor baris.
3. Manifest mencatat versi profil, versi registry, dan versi library yang menulis dataset.
4. Nama kolom nilai tidak boleh memakai nama yang dicadangkan kontrak, misalnya `coverage_ratio`.
5. Dataset tidak pernah ditimpa. Upload yang sama kedua kalinya dapat 409.

## Batasan saat ini

- Satu file Parquet per dataset. Partisi per prefix GID belum dibuat.
- Hanya domain ID.
- Hanya penyimpanan lokal. S3 nanti.

## Referensi

- Spec: `geosquare-v2/internal/architecture/V2SPECS.md`, bagian 14 dan 15.
- Kontrak dataset: `geosquare-v2/docs/cell-dataset-contract.md`.

## Phase 3 query and crosswalk API (English)

The platform now exposes `GET /datasets/{dataset_id}/rollup?level=L&sum=col1,col2` for internal hierarchical sums and `POST /query/join` for inner joins followed by a rollup. The internal rollup route remains for compatibility; external exports must use `GET /datasets/{dataset_id}/export?k=<int>&count=<field>`. The export applies the requested k-anonymity threshold and reports suppressed cells. The join request accepts `dataset_ids`, `level`, `sum`, and optional `gid_prefixes`.

Crosswalk administration uses `POST /crosswalks` with a CSV containing `sumber_id,gid,bobot`, plus `name`, `domain_code`, `level`, and `weight_kind` form fields. `GET /crosswalks/{name}` reads its manifest and counts. `POST /crosswalks/{name}/apply` allocates numeric source columns to cells, checks conservation, and stores the resulting COUNT dataset.

## L10 V1 to V2 converter (tool only)

The converter reads one caller-supplied V1 CSV and writes a new V2 cell dataset. It does not scan, select, or migrate team data. Runtime conversion uses only `geosquare-v2`, including its `LegacyV1Decoder` and `V1MigrationAdapter`; it does not need `geosquare-grid`. The test suite uses `geosquare-grid` as a test-only dependency to verify decoder bounds parity for random ASEAN points. The dataset directory contains `migration_report.json` with the source hash, versions, coverage gaps, and conversion settings.

```zsh
GSQ_PLATFORM_DATA=data/cells uv run python -m geosquare_platform.migrate_v1 \\
  --input synthetic-v1.csv \\
  --domain ID \\
  --level 9 \\
  --name ID-L9-0123456789ab \\
  --value-fields '[{"name":"population","logical_type":"numeric","semantics":"COUNT","unit":null}]'
```

Required flags are `--input`, `--domain`, `--level`, `--name`, and `--value-fields`. The optional `--gid-column` defaults to `gid_v1`; `--max-unallocated` defaults to `1e-6`. Value fields must be numeric and have `COUNT` or `TOTAL` semantics. The command refuses level-15 V1 GIDs, invalid V1 GIDs, unsupported value semantics, missing required flags, existing dataset names, excessive unallocated coverage, and empty area overlap. It refuses names outside the platform dataset ID contract instead of guessing a storage path. The converter no longer checks parity against `geosquare-grid` on each run; the test suite performs that check.

Tests and examples use synthetic CSVs only. Do not run this tool on `geosquare-risk`, `pandaigadai`, `dukcapil`, or `bank_tanah`.

## Bahasa Indonesia

API Fase 3 sekarang menyediakan rollup, join inner, dan administrasi crosswalk. Crosswalk memvalidasi bobot positif, jumlah bobot per sumber sama dengan 1, GID sesuai level manifest, serta tidak ada pasangan sumber-GID ganda. Proses apply menolak sumber yang tidak dikenal dan memeriksa konservasi jumlah sebelum menyimpan dataset sel.

Tool L10 hanya membaca satu CSV yang diberikan pengguna. Tool ini tidak memindahkan data tim. GID level 15, GID tidak valid, bobot yang tidak aman, dan nama dataset yang sudah ada akan ditolak.
