# Data format

The platform stores each dataset as one folder. The folder holds `manifest.json` and one or more Parquet files.

## Location

```text
<GSQ_PLATFORM_DATA>/
  ID-L9-0123456789ab/          cell dataset (cell_values)
    manifest.json
    data/<...>.parquet
  ID-COMPACT-0123456789ab/     compact set
    manifest.json
    data/compact.parquet
  TIME-0123456789ab/           time dataset (cell_time_values)
    manifest.json
    data/time_values.parquet
  crosswalks/
    <crosswalk-name>/
```

If `GSQ_PLATFORM_DATA` is not set, the base folder is `data/cells`.

## ID patterns

| Type | Pattern | Example |
|---|---|---|
| Cell dataset | `ID-L<level>-<12 hex>` | `ID-L9-0123456789ab` |
| Compact set | `ID-COMPACT-<12 hex>` | `ID-COMPACT-0123456789ab` |
| Time dataset | `TIME-<12 hex>` | `TIME-0123456789ab` |

The ID is built from the file content and the upload settings. So the same file with the same settings always gets the same ID. A second upload of it returns 409.

## Three dataset types

### Cell dataset (`cell_values`)

One row per cell. GIDs are unique.

Important manifest fields:

- `dataset_type`: `cell_values`
- `level`: level of all GIDs
- `domain_code`, `domain_id`
- `value_fields`: name, `logical_type`, `semantics`, `unit`
- `identity`: `gid_column` and `unique_gid: true`
- `storage`: `row_count`, `unique_cell_count`, `sorted_by: ["gid"]`
- `provenance`: `created_by`, `created_at`, and the generator

A cell dataset can also be `cell_contributions`, for source data that is not merged yet. The platform currently writes `cell_values`.

### Compact set (`cell_compact_set`)

Rows with `gid` and `level`. Levels can be mixed. The set must be canonical.

Important manifest fields:

- `dataset_type`: `cell_compact_set`
- `levels_present`: list of levels in the set
- `identity`: `mixed_levels: true`
- `storage`: `row_count`, `sorted_by: ["gid"]`

For the full rules, see the [engine docs](https://github.com/geosquareai/geosquare-engine/blob/main/docs/compact-sets.md).

### Time dataset (`cell_time_values`)

Rows with `gid`, `period`, and value columns. Each `(gid, period)` pair is unique.

Important manifest fields:

- `dataset_type`: `cell_time_values`
- `level`
- `period_type`: `year`, `month`, `day`, or `hour`
- `bucket_hours`: only for `hour`
- `timezone`: always `UTC`
- `period_start` (included) and `period_end` (not included)
- `identity`: `unique_gid: false` and `unique_key: ["gid", "period"]`
- `storage`: `sorted_by: ["gid", "period"]`
- `value_fields`: each item has `semantics`, and `time_aggregation` for `MEASUREMENT`

Storage is not partitioned yet. An empty `partitioning` means one file.

## Reserved names

- The column `period` is reserved in time datasets. You cannot use that name for a value.
- The column `coverage_ratio` is reserved by the contract. Rename it in the CSV before upload.

## Reading again

Every read checks the manifest and the rows again. A dataset that breaks the rules is rejected. The platform does not repair it.

---

## Ringkasan Bahasa Indonesia

Setiap dataset disimpan sebagai satu folder berisi `manifest.json` dan file Parquet. Ada tiga jenis: dataset sel (`cell_values`), compact set (`cell_compact_set`), dan dataset waktu (`cell_time_values`). ID dibuat dari isi file dan pengaturan upload. Kolom `period` dan `coverage_ratio` tidak boleh dipakai untuk nilai. Setiap pembacaan memeriksa ulang manifest dan baris.
