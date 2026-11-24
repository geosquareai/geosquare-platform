# Tools

Run these tools from the terminal. All examples use `uv run`.

## Demo

The demo runs the upload steps through the API: detect, store, and check the manifest.

```zsh
uv run python -m geosquare_platform.demo cells.csv --numeric share --license "internal-license" --share-policy internal
```

For the KYL data (50,676 cells, level 12), rename the column `coverage_ratio` to `share_in_aoi`. The values do not change.

```zsh
uv run python -m geosquare_platform.demo kyl_cells_50m.csv --numeric share_in_aoi --license "internal-license" --share-policy internal
GSQ_KYL_CSV=kyl_cells_50m.csv uv run pytest -q tests/test_demo.py
```

## Convert V1 to V2

This tool turns one V1 GID CSV into one V2 dataset. It reads only the file you give it. It does not move team data.

```zsh
GSQ_PLATFORM_DATA=data/cells uv run python -m geosquare_platform.migrate_v1 \
  --input synthetic-v1.csv \
  --domain ID \
  --level 9 \
  --name ID-L9-0123456789ab \
  --license "internal-license" \
  --share-policy internal \
  --value-fields '[{"name":"population","logical_type":"numeric","semantics":"COUNT","unit":null}]'
```

Required flags: `--input`, `--domain`, `--level`, `--name`, `--license`, `--share-policy`, and `--value-fields`.

Optional flags:

- `--gid-column`: default `gid_v1`.
- `--max-unallocated`: default `1e-6`.

Rules:

- Value columns must be numeric, with `COUNT` or `TOTAL`.
- V1 GIDs at level 15 are rejected. Invalid V1 GIDs are rejected too.
- A dataset name that already exists is rejected.
- If the unallocated area is larger than the limit, the conversion stops.

The result always includes `migration_report.json` in the dataset folder. It holds the hash of the source file, the versions, the coverage gaps, and the conversion settings.

The runtime conversion uses `geosquare-v2`, including `LegacyV1Decoder`. The tests use `geosquare-grid` only to check decoder parity.

Do not run this tool on other project data without permission. Recorded examples: `geosquare-risk`, `pandaigadai`, `dukcapil`, and `bank_tanah`.

## Crosswalk builder

The crosswalk builder lives in the `geosquare-v2` repo. It builds a crosswalk from source polygons, for example village boundaries.

```zsh
cd /path/to/geosquare-v2
.venv/bin/python tools/build_polygon_crosswalk.py path/to/source.parquet \
  --id-column kode_desa \
  --value-column jumlah_penduduk \
  --domain ID \
  --level 11 \
  --weight-kind luas \
  --description "Source description. Use for internal analysis only." \
  --out-dir path/to/output
```

Output:

- `crosswalk.csv`: columns `sumber_id,gid,bobot` (source ID, GID, weight). Weights are the area in the grid CRS. The weights per source add up to 1.
- `crosswalk_manifest.json`: format metadata.
- `sources.csv`: `sumber_id` and value columns, unchanged.

Pick an ID column that is not empty and is unique. An empty ID value stops the builder.

After the build, you can upload the crosswalk to the platform with `POST /crosswalks`.

## Cell to admin reference table (L17)

This tool builds one Parquet table. It has one row for each pair of cell and admin unit. Join it to a cell table on `gid`, then `GROUP BY` any admin code.

```zsh
uv run python -m geosquare_platform.admin_reference boundaries.parquet out/admin_reference_l12.parquet \
  --regency-column kode_kota \
  --level 11 \
  --source-name "Boundary source" \
  --license "source-license"
```

Required: `SOURCE`, `OUT`, `--source-name`, and `--license`. Neither text can be empty. Add at least one of `--village-column`, `--district-column`, or `--regency-column`. Optional: `--province-column` and `--geometry-column` (default `geom`). The default level is 11 (100 m cells), per decision K14 in the team risk log. Use `--level 12` for 50 m cells.

Output columns: `gid`, `village_code`, `district_code`, `regency_code`, `province_code`, and `area_weight`.

- `area_weight` = area of the overlap between the unit and the cell, divided by the cell area.
- The sum of `area_weight` per `gid` is the share of the cell covered by admin units. It is at most 1. It is exactly 1 when the cell is fully covered, and less than 1 for cells on the edge of the covered area. Do not assume every cell sums to 1.
- Admin units must not overlap. The tool compares the total unit area with the area of their union. If they overlap, the tool stops and writes no file.
- The input must be WGS 84 lon/lat (EPSG:4326). Coordinates outside the degree range are rejected.
- If `province_code` is not given, the tool uses the first two digits of the regency code.
- The tool uses the polygon builder from `geosquare-v2`. Each unit is cut into tiles of 20 km (level 11, 200 x 200 cells), because the builder rejects more than 100,000 candidate cells.

The file metadata stores `source_name`, `license`, the source file name, the level, and the creation time. Write the license honestly. Do not use `dukcapil_*` files without written permission.

---

## Ringkasan Bahasa Indonesia

Halaman ini berisi empat alat: demo upload, konversi V1 ke V2, builder crosswalk dari `geosquare-v2`, dan tabel referensi sel ke admin (L17). Semua alat dijalankan dari terminal dengan `uv run`. Jangan jalankan alat konversi pada data proyek lain tanpa izin. Isi lisensi harus jujur, dan jangan pakai file `dukcapil_*` tanpa izin tertulis.
