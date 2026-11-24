# Pengembangan

Halaman ini untuk orang yang mengubah kode platform.

## Setup

```zsh
uv sync --extra test
```

Perintah ini membaca `uv.lock`. Versi yang terkunci sama dengan yang diuji.

## Pin versi

| Paket | Versi | Alasan |
|---|---|---|
| `geosquare-engine` | `>=0.2.0,<0.3` | Fungsi compact set ada sejak 0.2.0 |
| `geosquare-v2[table,registry,geo]` | `==1.2.0` | `time_store` dan `compact_store` ada sejak 1.2.0 |
| `geosquare-grid` | `==1.1.1` (hanya extra `test`) | Tes paritas V1. Ini lisensi GPL, jadi tidak ikut di runtime |

Saat pin berubah, jalankan `uv lock --upgrade-package <paket>`, lalu tes penuh.

## Tes

```zsh
uv run pytest -q
```

Dua file membutuhkan `geosquare-grid` dari extra `test`:

- `tests/test_v1_parity.py`
- `tests/test_migrate_v1.py`

Jika paket itu tidak terpasang, dua file ini gagal saat dikumpulkan. Jalankan tes lain dengan:

```zsh
uv run pytest -q --ignore=tests/test_v1_parity.py --ignore=tests/test_migrate_v1.py
```

Tes memakai data sintetis. Jangan commit data asli. Folder `data/` ada di `.gitignore`.

## Aturan kode

- Jangan tulis ulang aturan grid di sini. Panggil `geosquare-engine`.
- Jangan tulis ulang aturan dataset di sini. Panggil `geosquare-v2`.
- Input salah harus gagal dengan pesan yang jelas. Jangan memperbaiki diam-diam.
- Hasil yang berisi nilai per sel hanya boleh lewat rute internal.
- Jika tes memakai versi paket, baca versi dari paket yang terpasang. Jangan tulis angka tetap.

## Menjalankan server lokal

```zsh
GSQ_PLATFORM_DATA=data/cells uv run uvicorn geosquare_platform.app:app --reload
```

Untuk mencoba rute internal di mesin sendiri:

```zsh
GSQ_INTERNAL_ROUTES=1 GSQ_PLATFORM_DATA=data/cells uv run uvicorn geosquare_platform.app:app --reload
```

Jangan jalankan server dengan flag ini di jaringan yang bisa diakses publik.

## Kontribusi

- Buat cabang dari `main`. Jangan kerja langsung di `main`.
- Buka pull request. Tunggu review sebelum merge.
- Update dokumen di folder `docs/` jika perilaku berubah.

## Peta di browser (WASM)

Peta di `/map` membaca file Parquet langsung di browser, lalu melipatnya ke level sesuai zoom (E19).

Paket WASM dari `geosquare-engine` belum ada di npm. Salin hasil build lokal ke `web/map/pkg/` (folder ini tidak di-commit):

```zsh
cp -R ../geosquare-engine/bindings/wasm/pkg web/map/pkg
```

`web/map/js/rollup.mjs` adalah salinan dari `geosquare-engine/bindings/wasm/js/rollup.mjs`. Jaga agar tetap sama dengan engine.

Rute `GET /datasets/{id}/parquet` hanya aktif dengan `GSQ_INTERNAL_ROUTES=1`, karena file-nya berisi nilai per sel. Tanpa flag itu, peta memakai rute `/cells`.

## Raster ke sel (L14)

`raster_to_cells` di `src/geosquare_platform/raster.py` membaca GeoTIFF atau COG dengan `read_raster` dari engine. Nilai tiap sel adalah rata-rata piksel yang titik tengahnya ada di dalam bbox.

Fungsi ini butuh extension DuckDB dari engine (belum ada di PyPI). Build dulu di `geosquare-engine/bindings/duckdb`, lalu:

```zsh
export GSQ_DUCKDB_EXTENSION=/path/to/geosquare_v2.duckdb_extension
uv sync --extra test
uv run --extra test pytest -q tests/test_raster_cells.py
```

Tanpa `GSQ_DUCKDB_EXTENSION`, fungsi menolak dengan pesan yang jelas. Tes referensi akan dilewati.

Sengaja tidak ada rute HTTP. Input-nya path file di server, jadi rute seperti itu bisa membaca file sembarang.
