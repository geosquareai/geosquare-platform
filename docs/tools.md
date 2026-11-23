# Alat

Alat ini dijalankan dari terminal. Semua contoh memakai `uv run`.

## Demo

Demo menjalankan langkah upload, deteksi, simpan, dan cek manifest lewat API.

```zsh
uv run python -m geosquare_platform.demo cells.csv --numeric share
```

Untuk data KYL (50.676 sel, level 12), ganti nama kolom `coverage_ratio` menjadi `share_in_aoi`. Nilainya tidak berubah.

```zsh
uv run python -m geosquare_platform.demo kyl_cells_50m.csv --numeric share_in_aoi
GSQ_KYL_CSV=kyl_cells_50m.csv uv run pytest -q tests/test_demo.py
```

## Konversi V1 ke V2

Alat ini mengubah satu CSV GID V1 menjadi satu dataset V2. Alat ini hanya membaca file yang Anda berikan. Alat ini tidak memindahkan data tim.

```zsh
GSQ_PLATFORM_DATA=data/cells uv run python -m geosquare_platform.migrate_v1 \
  --input synthetic-v1.csv \
  --domain ID \
  --level 9 \
  --name ID-L9-0123456789ab \
  --value-fields '[{"name":"population","logical_type":"numeric","semantics":"COUNT","unit":null}]'
```

Flag wajib: `--input`, `--domain`, `--level`, `--name`, dan `--value-fields`.

Flag opsional:

- `--gid-column`: default `gid_v1`.
- `--max-unallocated`: default `1e-6`.

Aturan:

- Kolom nilai harus numerik, dengan `COUNT` atau `TOTAL`.
- GID V1 level 15 ditolak. GID V1 yang tidak valid juga ditolak.
- Nama dataset yang sudah ada ditolak.
- Jika area yang tidak teralokasi lebih besar dari batas, konversi berhenti.

Hasilnya selalu menyertakan `migration_report.json` di folder dataset. Isinya hash file sumber, versi, celah cakupan, dan pengaturan konversi.

Konversi runtime memakai `geosquare-v2`, termasuk `LegacyV1Decoder`. Test memakai `geosquare-grid` hanya untuk memeriksa paritas decoder.

Jangan jalankan alat ini pada data proyek lain yang belum diizinkan. Contoh yang tercatat: `geosquare-risk`, `pandaigadai`, `dukcapil`, dan `bank_tanah`.

## Builder crosswalk

Builder crosswalk ada di repo `geosquare-v2`. Alat ini membuat crosswalk dari poligon sumber, misalnya batas kelurahan.

```zsh
cd /path/to/geosquare-v2
.venv/bin/python tools/build_polygon_crosswalk.py path/to/sumber.parquet \
  --id-column kode_desa \
  --value-column jumlah_penduduk \
  --domain ID \
  --level 11 \
  --weight-kind luas \
  --description "Keterangan sumber. Pakai untuk analisis internal." \
  --out-dir path/to/output
```

Hasilnya:

- `crosswalk.csv`: kolom `sumber_id,gid,bobot`. Bobot dihitung dari luas area di CRS grid. Jumlah bobot per sumber sama dengan 1.
- `crosswalk_manifest.json`: metadata format.
- `sources.csv`: `sumber_id` dan kolom nilai, tanpa perubahan.

Pilih kolom id yang tidak kosong dan unik. Kolom id yang kosong menghentikan builder.

Setelah dibuat, crosswalk bisa diunggah ke platform lewat `POST /crosswalks`.
