# Alat

Alat ini dijalankan dari terminal. Semua contoh memakai `uv run`.

## Demo

Demo menjalankan langkah upload, deteksi, simpan, dan cek manifest lewat API.

```zsh
uv run python -m geosquare_platform.demo cells.csv --numeric share --license "lisensi-internal" --share-policy internal
```

Untuk data KYL (50.676 sel, level 12), ganti nama kolom `coverage_ratio` menjadi `share_in_aoi`. Nilainya tidak berubah.

```zsh
uv run python -m geosquare_platform.demo kyl_cells_50m.csv --numeric share_in_aoi --license "lisensi-internal" --share-policy internal
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
  --license "lisensi-internal" \
  --share-policy internal \
  --value-fields '[{"name":"population","logical_type":"numeric","semantics":"COUNT","unit":null}]'
```

Flag wajib: `--input`, `--domain`, `--level`, `--name`, `--license`, `--share-policy`, dan `--value-fields`.

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

## Tabel referensi sel ke admin (L17)

Alat ini membuat satu tabel Parquet: satu baris untuk setiap pasangan sel dan unit admin. Join ke tabel sel lewat `gid`, lalu `GROUP BY` kode admin mana pun.

```zsh
uv run python -m geosquare_platform.admin_reference batas.parquet out/admin_reference_l12.parquet \
  --regency-column kode_kota \
  --level 12 \
  --source-name "Sumber batas" \
  --license "lisensi sumber"
```

Flag wajib: `SOURCE`, `OUT`, `--source-name`, dan `--license`. Tambahkan minimal satu dari `--village-column`, `--district-column`, atau `--regency-column`. Flag opsional: `--province-column` dan `--geometry-column` (default `geom`). Level default 12 (sel 50 m).

Kolom output: `gid`, `village_code`, `district_code`, `regency_code`, `province_code`, dan `area_weight`.

- `area_weight` = luas irisan unit dengan sel dibagi luas sel. Sel di dalam unit punya bobot 1.
- Jumlah `area_weight` per `gid` tidak boleh lebih dari 1. Lebih dari 1 berarti unit admin saling tumpang tindih, dan alat berhenti.
- Jika `province_code` tidak diisi, alat memakai dua digit pertama dari kode kabupaten/kota.
- Alat memakai builder poligon dari `geosquare-v2`. Setiap unit dipotong per tile 10 km (level 12) karena builder menolak lebih dari 100.000 sel kandidat.

Metadata file menyimpan `source_name`, `license`, nama file sumber, level, dan waktu pembuatan. Isi lisensi harus diisi jujur. Jangan pakai file `dukcapil_*` tanpa izin tertulis.
