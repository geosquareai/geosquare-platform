# Format data

Platform menyimpan setiap dataset sebagai satu folder. Folder itu berisi `manifest.json` dan satu atau lebih file Parquet.

## Lokasi

```text
<GSQ_PLATFORM_DATA>/
  ID-L9-0123456789ab/          dataset sel (cell_values)
    manifest.json
    data/<...>.parquet
  ID-COMPACT-0123456789ab/     compact set
    manifest.json
    data/compact.parquet
  TIME-0123456789ab/           dataset waktu (cell_time_values)
    manifest.json
    data/time_values.parquet
  crosswalks/
    <nama-crosswalk>/
```

Jika `GSQ_PLATFORM_DATA` tidak diisi, folder dasarnya `data/cells`.

## Pola ID

| Jenis | Pola | Contoh |
|---|---|---|
| Dataset sel | `ID-L<level>-<12 heks>` | `ID-L9-0123456789ab` |
| Compact set | `ID-COMPACT-<12 heks>` | `ID-COMPACT-0123456789ab` |
| Dataset waktu | `TIME-<12 heks>` | `TIME-0123456789ab` |

ID dibuat dari isi file dan pengaturan upload. Jadi file yang sama dengan pengaturan yang sama selalu menghasilkan ID yang sama. Upload kedua menghasilkan 409.

## Tiga jenis dataset

### Dataset sel (`cell_values`)

Satu baris per sel. GID unik.

Manifest penting:

- `dataset_type`: `cell_values`
- `level`: level semua GID
- `domain_code`, `domain_id`
- `value_fields`: nama, `logical_type`, `semantics`, `unit`
- `identity`: `gid_column` dan `unique_gid: true`
- `storage`: `row_count`, `unique_cell_count`, `sorted_by: ["gid"]`
- `provenance`: `created_by`, `created_at`, dan generator

Dataset sel bisa `cell_contributions` untuk data sumber yang belum digabung. Platform saat ini menulis `cell_values`.

### Compact set (`cell_compact_set`)

Baris `gid` dan `level`. Level boleh campur. Set harus kanonik.

Manifest penting:

- `dataset_type`: `cell_compact_set`
- `levels_present`: daftar level yang ada
- `identity`: `mixed_levels: true`
- `storage`: `row_count`, `sorted_by: ["gid"]`

Aturan lengkap ada di [dokumentasi engine](https://github.com/geosquareai/geosquare-engine/blob/main/docs/compact-sets.md).

### Dataset waktu (`cell_time_values`)

Baris `gid`, `period`, dan kolom nilai. Pasangan `(gid, period)` unik.

Manifest penting:

- `dataset_type`: `cell_time_values`
- `level`
- `period_type`: `year`, `month`, `day`, atau `hour`
- `bucket_hours`: hanya untuk `hour`
- `timezone`: selalu `UTC`
- `period_start` (dimasukkan) dan `period_end` (tidak dimasukkan)
- `identity`: `unique_gid: false` dan `unique_key: ["gid", "period"]`
- `storage`: `sorted_by: ["gid", "period"]`
- `value_fields`: setiap item punya `semantics`, dan `time_aggregation` untuk `MEASUREMENT`

Penyimpanan belum dipartisi. `partitioning` kosong berarti satu file.

## Nilai yang dicadangkan

- Kolom `period` dicadangkan di dataset waktu. Nama itu tidak boleh dipakai untuk nilai.
- Kolom `coverage_ratio` dicadangkan kontrak. Ganti nama di CSV sebelum upload.

## Membaca ulang

Setiap pembacaan memeriksa ulang manifest dan baris. Dataset yang melanggar aturan ditolak. Platform tidak memperbaikinya.
