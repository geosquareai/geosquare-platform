# geosquare-platform

Status: kerangka saja. Belum ada kode. Kode mulai ditulis di Fase 2.

## Fungsi repo ini

Repo ini adalah produk GeoSquare V2 untuk tim dan klien. Isinya:

- importer: upload CSV atau Parquet, deteksi kolom GID, lalu langsung jadi peta;
- cell store: dataset sel di S3, dengan manifest;
- API: join, rollup, filter area, dan profil satu sel;
- peta: layer yang menggambar sel dari `gid`, tanpa polygon dikirim dari server;
- katalog dataset.

## Aturan utama

1. Matematika grid tidak ditulis ulang di sini. Semua perhitungan lewat library `geosquare-engine`.
2. Deteksi kolom mengikuti spec 2.0 bagian 14 (`geosquare-v2/internal/architecture/V2SPECS.md`).
3. Setiap dataset wajib menyimpan versi spec, versi registry, dan versi engine di manifest.
4. Susunan file sel mengikuti spec 2.0 bagian 15. Level partisi dipilih dengan aturan ukuran 512 MiB.

## Referensi

- Spec: `geosquare-v2/internal/architecture/V2SPECS.md`.
- Hasil benchmark partisi: `geosquare-v2/internal/research/PARTITION_LEVEL_BENCHMARK.md`.
- Rencana: `plan/` di folder workspace.
