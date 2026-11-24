# geosquare-platform

Server API untuk dataset sel GeoSquare. Platform ini menerima CSV, memeriksa setiap baris, menyimpan hasilnya sebagai dataset sel, lalu menjawab query dan export.

Platform ini memakai dua paket inti:

- `geosquare-engine` (0.3.0 atau lebih baru, dari PyPI) untuk hitung grid, seperti GID, level, dan deteksi kolom.
- `geosquare-v2` (1.2.0 atau lebih baru) untuk format dataset, manifest, dan aturan spec.

Platform tidak menulis ulang aturan grid. Semua aturan ada di dua paket itu.

## Mulai cepat

```zsh
uv sync --extra test
uv run pytest -q
uv run uvicorn geosquare_platform.app:app --reload
```

Buka dokumentasi interaktif di `http://127.0.0.1:8000/docs`.

## Variabel lingkungan

| Variabel | Fungsi | Default |
|---|---|---|
| `GSQ_PLATFORM_DATA` | Folder tempat dataset disimpan | `data/cells` |
| `GSQ_INTERNAL_ROUTES` | Isi `1` untuk membuka rute internal. Selain itu, rute internal menjawab 404 | tidak diset |

Jangan isi `GSQ_INTERNAL_ROUTES=1` di server yang bisa diakses publik. Gerbang ini bukan autentikasi. Lihat [Privasi dan rute internal](docs/privacy-and-internal-routes.md).

## Dokumentasi

| Halaman | Isi |
|---|---|
| [API](docs/api.md) | Semua endpoint, parameter, dan jawaban |
| [Format data](docs/data-format.md) | Folder dataset, manifest, dan tiga jenis dataset |
| [Privasi dan rute internal](docs/privacy-and-internal-routes.md) | Aturan k, export, dan rute yang disembunyikan |
| [Alat](docs/tools.md) | Demo, konversi V1 ke V2, dan builder crosswalk |
| [Pengembangan](docs/development.md) | Setup, tes, dan pin versi |

## Aturan utama

1. Matematika grid tidak ditulis ulang di sini. Deteksi dan hitung lewat `geosquare-engine`.
2. Format dan validasi dataset lewat `geosquare-v2`.
3. Nilai tidak pernah diubah diam-diam. Baris yang salah membuat upload gagal, dan pesannya menyebut nomor baris.
4. Manifest mencatat versi profil, versi registry, dan versi library yang menulis dataset.
5. Dataset tidak pernah ditimpa. Upload yang sama untuk kedua kalinya dapat 409.

## Batasan saat ini

- Satu file Parquet per dataset. Partisi belum dibuat.
- Domain yang didukung di profil sel hanya ID.
- Penyimpanan hanya lokal. Penyimpanan S3 belum ada.
