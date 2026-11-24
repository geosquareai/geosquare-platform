# API

All examples use `http://127.0.0.1:8000`. Uploads use `multipart/form-data`. Queries use JSON or query strings, as the tables show.

Common error codes:

| Code | Meaning |
|---|---|
| 201 | Created |
| 404 | Not found. For an internal route, 404 means the route is off |
| 409 | A dataset with the same content already exists |
| 422 | Input is not valid. The message says why |

## Summary

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/health` | public | Check that the server is up |
| POST | `/import/analyse` | public | Check a CSV without storing it |
| POST | `/datasets` | public | Store a CSV as a cell dataset |
| GET | `/datasets/{id}` | public | Read a dataset again and check its manifest |
| GET | `/datasets/{id}/parquet` | internal | The Parquet file of one dataset, for the browser map |
| GET | `/named/{name}` | public | Read the latest version of a dataset name |
| GET | `/named/{name}/versions` | public | List versions with their content hash |
| GET | `/named/{name}/versions/{version}` | public | Read one exact version |
| GET | `/datasets/{id}/export` | public | Export with k-anonymity. Does not check `share_policy` today |
| GET | `/datasets/{id}/cells` | public | Cells whose centre is inside a WGS 84 box |
| GET | `/catalog` | public | List datasets from manifests. Filters `bbox` (box) and `gid` (exact coverage) |
| GET | `/datasets/{id}/coverage` | public | Exact coverage of a dataset: a compact cell set |
| GET | `/cells/{gid}/profile` | public | Values of one cell and its parent |
| POST | `/crosswalks` | public | Store a crosswalk |
| GET | `/crosswalks/{name}` | public | Read a crosswalk |
| POST | `/crosswalks/{name}/apply` | public | Apply a crosswalk to source data |
| GET | `/datasets/{id}/rollup` | internal | Totals per parent cell |
| POST | `/query/join` | internal | Join several datasets, then sum to a parent level |
| POST | `/datasets/compact` | public | Store a compact set |
| POST | `/query/compact-join` | internal | Sum values in the cells that a compact set covers |
| POST | `/time-datasets` | public | Store a time-value dataset |
| POST | `/query/time-join` | internal | Join two time-value datasets by interval |
| GET | `/map` | public | Map page |

Routes marked "internal" answer 404 unless `GSQ_INTERNAL_ROUTES=1`.

Routes marked "public" that return values per cell (`/datasets/{id}/cells`, `/cells/{gid}/profile`, `/datasets/{id}/export`) are not behind that flag. None of these three checks `share_policy` today. Do not upload data you cannot make public to a server that others can reach.

## Cell datasets

### POST /import/analyse

Check a CSV and report its GID column and level. Nothing is stored.

```zsh
curl -F "file=@cells.csv" "http://127.0.0.1:8000/import/analyse"
```

Query: `confirm_digit_gid` (bool, default `false`). Use `true` when every GID is a number. A number can also be a normal code, so the platform asks for confirmation.

### POST /datasets

Check a CSV, then store it as a cell dataset with a manifest.

Form:

| Field | Value |
|---|---|
| `file` | CSV with a GID column and value columns |
| `value_fields` | JSON array, for example `[{"name":"share","logical_type":"numeric","semantics":"MEASUREMENT","unit":"ratio"}]` |
| `license` | **Required.** License or terms of the source data. Without it, the upload is rejected (422). |
| `share_policy` | **Required.** `internal`, `k_anon_only`, or `public`. Without it, the upload is rejected (422). |
| `owner`, `title`, `source_name` | Optional. Text for the catalog. |
| `refresh` | Optional. `once` (default), `daily`, `weekly`, `monthly`, `yearly`, or `irregular`. Used for the stale badge in the catalog. |
| `name` | Optional. An upload with this name becomes the next version (see "Dataset versions"). Without a name, the dataset is stored as usual. |

Each item in `value_fields` has:

- `name`: column name in the CSV. Names reserved by the contract cannot be used, for example `coverage_ratio`.
- `logical_type`: `numeric` or `categorical`.
- `semantics`: `COUNT`, `TOTAL`, `DENSITY`, `MEASUREMENT`, `RATE`, or `CATEGORICAL`.
- `unit`: text or `null`.
- `space_aggregation` (optional): how child cell values combine when you go up a level. If empty, the default for the `semantics` is used: `COUNT`/`TOTAL` → `sum`, `DENSITY` → `weighted_mean` with weight `area`, `MEASUREMENT` → `median`, `CATEGORICAL` → `majority_weighted`, `RATE` → `none` (needs a denominator weight first).
- `space_weight` (optional): `null`, `area`, or the name of another `COUNT`/`TOTAL` column in the same dataset.

The stored manifest uses format 1.1. The platform fills in column statistics, the coverage (`extent`, a WGS 84 box), and the content hash of the file.

Every upload is also checked the way `gsq check` checks it: empty or broken GIDs, duplicate GIDs, and mixed levels. If a row is wrong, the response is 422 with `detail.problems`: one line per problem, with the row number. Examples: `"row 4: invalid GID 'zz'"` or `"duplicate GID ...: rows 1, 2"`.

Query: `confirm_digit_gid`.

A 201 response includes `dataset_id`, `level`, `rows`, `unique_cells`, `value_fields`, `dropped_columns`, and `valid`.

### GET /datasets/{dataset_id}

Read the dataset again and check its manifest. The response contains the full manifest. If the check fails, the response is 422.

### Dataset versions (L18)

An upload with a `name` that already exists creates a new version (`v2`, `v3`, and so on). Old versions are not overwritten.

- Content that is exactly the same as an existing version (same `content_sha256`) is rejected with 409.
- `GET /named/{name}` returns the latest version. `GET /named/{name}/versions/{version}` returns one version, with its manifest and hash.
- `GET /named/{name}/versions` returns the list of versions. If the name does not exist, the response is 404.
- A dataset without a name can still be read with `/datasets/{dataset_id}`. Nothing changes for old datasets.
- Known limit: the routes that take a dataset ID (`/datasets/{id}/cells`, `/parquet`, `/rollup`, `/export`, `/coverage`) and the map do not find a named dataset yet. They answer 404 for a name. Use an upload without a name for those, or read the named version through `/named/...`.
- Name: 1 to 64 characters, lowercase letters, digits, `_` or `-`, starting with a letter or digit.
- Uploads with the same name at the same time do not overwrite each other. If two uploads pick the same version number, the loser tries the next number (up to 5 times). If it still loses, the response is 409, and that upload can be retried. A race like this never returns 500.
- Only folders with a canonical version number are counted: `v1`, `v2`, and so on. Folders such as `v0` or `v01` are ignored.

### GET /catalog

List metadata for all datasets. It reads manifests only and does not open Parquet files.

Named datasets are listed too, but only the latest version of each name. Each row has a `name` and a `version` field, and `dataset_id` holds the name. Datasets without a name are listed as before. To read another version, use `/named/{name}/versions/{version}`.

Query `bbox=min_lon,min_lat,max_lon,max_lat` (optional): keep only datasets whose coverage box (`extent`, manifest 1.1) touches this box. Datasets without `extent`, for example manifest 1.0, are left out. This is a bounding box test, not a cell-by-cell test. An invalid box returns 422.

Query `gid=<cell>` (optional): keep only datasets whose exact coverage includes this cell, either the cell itself or a cell under a stored parent. This is an exact test. An invalid GID returns 422. The first call for a dataset builds its coverage from the data and saves it to `<data>/coverage/<dataset_id>.json`. That cache is a platform index, not part of the dataset contract.

### GET /datasets/{dataset_id}/coverage

Exact coverage of a stored dataset: its canonical compact cell set. It is built from the `gid` column and cached. The map uses it to draw the coverage.

### GET /cells/{gid}/profile

Values for one cell, its parent, and any dataset with a finer level than that cell. Only ID domain datasets are included. Other datasets are skipped, and the reason is recorded.

An invalid GID returns 422.

### GET /datasets/{dataset_id}/cells

Cells whose centre point is inside a WGS 84 box.

Query:

- `bbox`: `min_lon,min_lat,max_lon,max_lat`. Required.
- `limit`: maximum number of cells, default 1000, minimum 1.

### GET /datasets/{dataset_id}/parquet (internal)

The Parquet file of one dataset. The browser map reads it to roll cells up to the zoom level. It holds every cell, so it answers 404 unless `GSQ_INTERNAL_ROUTES=1`.

## Public export

### GET /datasets/{dataset_id}/export

Export with k-anonymity. For the full rules, see [Privacy and internal routes](privacy-and-internal-routes.md).

Query:

- `count`: name of the COUNT column in the dataset. Required.
- `k`: anonymity threshold. Default 5. Minimum 2.

Example:

```zsh
curl "http://127.0.0.1:8000/datasets/ID-L9-0123456789ab/export?count=population&k=5"
```

The response contains `k`, `count_field`, `input_level`, `rows` (each row has `gid`, `level`, `count`, `totals`, and `source_cell_count`), `suppressed_cell_count`, `suppressed_count`, and `dropped_fields`.

## Crosswalk

A crosswalk turns source data, such as data per village, into data per cell. The weights for each source must add up to 1.

### POST /crosswalks

Form:

| Field | Value |
|---|---|
| `file` | CSV with the columns `sumber_id,gid,bobot` (source id, GID, weight) |
| `name` | Crosswalk name. Must match the dataset ID pattern |
| `domain_code` | Domain code, for example `ID` |
| `level` | Level of the output cells |
| `weight_kind` | Kind of weight, for example `luas` (area) |

The crosswalk is stored in the `crosswalks` folder under the data folder. Validation rejects weights that are not positive, weights per source that do not add up to 1, GIDs outside the level, and duplicate source-GID pairs.

### GET /crosswalks/{name}

Read the manifest and the row count of the crosswalk. An unknown name returns 404.

### POST /crosswalks/{name}/apply

Apply the crosswalk to source data and store the result as a new COUNT cell dataset.

Form:

| Field | Value |
|---|---|
| `file` | CSV of source data with a `sumber_id` column and value columns |
| `dataset_id` | ID for the new cell dataset |
| `value_columns` | Comma-separated list of value columns |
| `license` | **Required.** License or terms of the source data. Without it, the request is rejected (422). |
| `share_policy` | **Required.** `internal`, `k_anon_only`, or `public`. Without it, the request is rejected (422). |

The platform rejects a source that is not in the crosswalk. It also checks that the total of each value in the cells equals the total in the source. If they differ, the dataset is not stored.

## Internal routes

These routes are active only with `GSQ_INTERNAL_ROUTES=1`.

### GET /datasets/{dataset_id}/rollup

Sum columns up to a coarser parent level.

Query:

- `level`: parent level. Must be between 0 and the dataset level.
- `sum`: column names, comma-separated.

The response contains `rows` (each row has `parent_gid`, `cells`, and a total per column) and `cells`.

Note: when `level` equals the dataset level, each cell comes back with its original value. This is why this route must not be public.

### POST /query/join

Join two or more datasets with the same level, then sum to a parent level.

JSON:

```json
{
  "dataset_ids": ["ID-L9-aaaaaaaaaaaa", "ID-L9-bbbbbbbbbbbb"],
  "level": 8,
  "sum": ["population"],
  "gid_prefixes": ["2"]
}
```

- `dataset_ids`: at least two, no duplicates.
- `level`: parent level.
- `sum`: columns to sum.
- `gid_prefixes`: optional. Limit to cells whose GID starts with one of these prefixes.

If two datasets have a column with the same name, the response names it `dataset_id.column`. The response also contains `cells_joined`.

## Compact sets

### POST /datasets/compact

Store a compact set from a CSV with exactly the columns `gid,level`.

The set must be canonical. The platform does not repair a set. A set that is not canonical is rejected with 422, for example when:

- a row is inside another row;
- all 25 children of a parent are present, when the parent should replace them;
- a row is duplicated;
- the level does not match the length of the GID.

Storing the same set twice returns 409.

### POST /query/compact-join

Internal. Sum columns in the dataset cells that a compact set covers.

JSON:

```json
{"compact_id": "ID-COMPACT-0123456789ab", "dataset_id": "ID-L9-0123456789ab", "sum": ["population"]}
```

Response: `matched_cells` and `sums`.

## Time datasets (L7)

### POST /time-datasets

Store a time-value dataset. One row per cell and per period.

Form:

| Field | Value |
|---|---|
| `file` | CSV with the columns `gid,period` and value columns |
| `level` | Cell level |
| `period_type` | `year`, `month`, `day`, or `hour` |
| `period_start` | Start of the range, in the form of `period_type` |
| `period_end` | End of the range, not included (exclusive) |
| `value_fields` | JSON array, as in `/datasets` |
| `bucket_hours` | Only for `hour`: 1, 2, 3, 4, 6, 8, or 12 |

Time rules:

- Period labels: `2026`, `2026-11`, `2026-11-19`, or `2026-11-19T04`. No time zone offset.
- All times are in UTC. Convert WIB, WITA, or WIT before storing.
- An hour label must be the start of a bucket. Example: a 4-hour bucket can start only at 00, 04, 08, 12, 16, or 20.
- Each `MEASUREMENT` value must have a `time_aggregation`: `mean`, `min`, or `max`.

### POST /query/time-join

Internal. Count left rows that have a right row in the same cell and a matching interval relation. Then sum the left columns.

JSON:

```json
{"left_id": "TIME-aaaaaaaaaaaa", "right_id": "TIME-bbbbbbbbbbbb", "relation": "contained", "sum": ["rain_mm"]}
```

`relation` can be `equal`, `contained` (left is inside right), `contains` (left contains right), or `overlap`. Default `contained`.

Response: `matched_rows`, `sums`, `relation`, `left_id`, and `right_id`.

## Map page

- `GET /map`: map page with the cell layer.
- `GET /map/pkg/{filename}`: support files for the map page.
- `GET /map/js/{filename}`: support scripts for the map page, such as `rollup.mjs`.

---

## Ringkasan Bahasa Indonesia

Halaman ini berisi semua endpoint platform: parameter, contoh, dan jawaban. Rute bertanda "internal" hanya aktif dengan `GSQ_INTERNAL_ROUTES=1`. Rute publik `/datasets/{id}/cells` dan `/cells/{gid}/profile` mengembalikan nilai per sel dan belum memeriksa `share_policy`. Export publik juga belum memeriksa `share_policy`.
