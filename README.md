# geosquare-platform

geosquare-platform is a server API for GeoSquare cell datasets. It takes a CSV with a GID column, checks every row, stores the result as a cell dataset, and answers queries and exports.

It uses two core packages:

- `geosquare-engine` 0.3.0 (from PyPI) for grid math: GIDs, levels, and column detection.
- `geosquare-v2` 1.3.0 for the dataset format, the manifest, and the spec rules.

The platform does not rewrite grid rules. Both packages hold them.

## What it does

- **Upload with GID check.** Each row is checked before storage. A bad row fails the upload, and the error names the row number.
- **Versions.** An upload with a name creates `v2`, `v3`, and so on. Old versions stay.
- **Catalog.** Lists datasets from their manifests. Filter by box (`bbox`) or by exact GID (`gid`).
- **Map.** A browser map at `/map` reads the Parquet file and rolls cells up to the zoom level. It needs the internal routes flag (see the quick start).
- **Raster to cells.** `geosquare_platform.raster.raster_to_cells` turns a GeoTIFF or COG into a cell dataset. It is a Python function, not an HTTP route. It needs the engine's DuckDB extension.
- **Cell to admin table.** `python -m geosquare_platform.admin_reference` builds one row per cell and admin unit, with an area weight.
- **Starter notebook.** `notebooks/starter.ipynb` loads a sample dataset, rolls it up with `df.gsq`, draws a map, and exports a CSV.

## Install

You need Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```zsh
uv sync
```

Optional extras:

- `--extra test`: test tools and `geosquare-grid` for the parity tests.
- `--extra notebook`: packages for the starter notebook.
- `--extra raster`: DuckDB for raster input.

## Run the server

```zsh
uv run uvicorn geosquare_platform.app:app --reload
```

Open the interactive API docs at `http://127.0.0.1:8000/docs`.

For a 5-minute walkthrough (upload a CSV, read the catalog, open the map), read [docs/quickstart.md](docs/quickstart.md).

## Environment variables

| Variable | What it does | Default |
|---|---|---|
| `GSQ_PLATFORM_DATA` | Folder where datasets are stored | `data/cells` |
| `GSQ_INTERNAL_ROUTES` | Set to `1` to turn on internal routes. Otherwise internal routes answer 404 | not set |

Do not set `GSQ_INTERNAL_ROUTES=1` on a server that the public can reach. This flag is not authentication. See [Privacy and internal routes](docs/privacy-and-internal-routes.md).

## Documentation

Start here:

| Page | Contents |
|---|---|
| [Quick start](docs/quickstart.md) | Run the server and upload a sample CSV |
| [API](docs/api.md) | Every endpoint, parameter, and response |
| [Data format](docs/data-format.md) | Dataset folders, manifests, and the three dataset types |
| [Tools](docs/tools.md) | Demo, V1 to V2 conversion, crosswalk builder, and admin table |

Operations:

| Page | Contents |
|---|---|
| [Privacy and internal routes](docs/privacy-and-internal-routes.md) | The k-anonymity rule, internal routes, and risks |
| [Development](docs/development.md) | Setup, tests, version pins, and code rules |

Also see [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## Core rules

1. The grid math is not rewritten here. Detection and computation go through `geosquare-engine`.
2. Dataset format and validation go through `geosquare-v2`.
3. Values are never changed silently. A bad row fails the upload, and the error names the row.
4. The manifest records the profile version, the registry version, and the library version that wrote the dataset.
5. Datasets are never overwritten. Uploading the same file with the same settings again returns 409.

## Known limits

- One Parquet file per dataset. Partitions are not built yet.
- The cell profile supports only the ID domain.
- Storage is local only. S3 storage is not built yet.
- `/datasets/{id}/cells` and `/cells/{gid}/profile` are public. They return values per cell. They do not check `share_policy`, so do not expose a server with private datasets.
- The map loads maplibre and hyparquet from CDNs, so the browser needs internet access.

## License

Apache-2.0. See [LICENSE](LICENSE).

---

## Bahasa Indonesia

Server API untuk dataset sel GeoSquare. Platform ini menerima CSV, memeriksa setiap baris, menyimpan hasilnya sebagai dataset sel, lalu menjawab query dan export.

Platform ini memakai dua paket inti:

- `geosquare-engine` (0.3.0 atau lebih baru, dari PyPI) untuk hitung grid, seperti GID, level, dan deteksi kolom.
- `geosquare-v2` (1.3.0) untuk format dataset, manifest, dan aturan spec.

Platform tidak menulis ulang aturan grid. Semua aturan ada di dua paket itu.

### Mulai cepat

```zsh
uv sync --extra test
uv run pytest -q
uv run uvicorn geosquare_platform.app:app --reload
```

Buka dokumentasi interaktif di `http://127.0.0.1:8000/docs`.

### Variabel lingkungan

| Variabel | Fungsi | Default |
|---|---|---|
| `GSQ_PLATFORM_DATA` | Folder tempat dataset disimpan | `data/cells` |
| `GSQ_INTERNAL_ROUTES` | Isi `1` untuk membuka rute internal. Selain itu, rute internal menjawab 404 | tidak diset |

Jangan isi `GSQ_INTERNAL_ROUTES=1` di server yang bisa diakses publik. Gerbang ini bukan autentikasi. Lihat [Privasi dan rute internal](docs/privacy-and-internal-routes.md).

### Dokumentasi

| Halaman | Isi |
|---|---|
| [Quick start](docs/quickstart.md) | Jalankan server dan unggah CSV contoh |
| [API](docs/api.md) | Semua endpoint, parameter, dan jawaban |
| [Format data](docs/data-format.md) | Folder dataset, manifest, dan tiga jenis dataset |
| [Privasi dan rute internal](docs/privacy-and-internal-routes.md) | Aturan k, export, dan rute yang disembunyikan |
| [Alat](docs/tools.md) | Demo, konversi V1 ke V2, dan builder crosswalk |
| [Pengembangan](docs/development.md) | Setup, tes, dan pin versi |

### Aturan utama

1. Matematika grid tidak ditulis ulang di sini. Deteksi dan hitung lewat `geosquare-engine`.
2. Format dan validasi dataset lewat `geosquare-v2`.
3. Nilai tidak pernah diubah diam-diam. Baris yang salah membuat upload gagal, dan pesannya menyebut nomor baris.
4. Manifest mencatat versi profil, versi registry, dan versi library yang menulis dataset.
5. Dataset tidak pernah ditimpa. Upload yang sama untuk kedua kalinya dapat 409.

### Batasan saat ini

- Satu file Parquet per dataset. Partisi belum dibuat.
- Domain yang didukung di profil sel hanya ID.
- Penyimpanan hanya lokal. Penyimpanan S3 belum ada.
- `/datasets/{id}/cells` dan `/cells/{gid}/profile` bersifat publik. Keduanya mengembalikan nilai per sel dan belum memeriksa `share_policy`.
