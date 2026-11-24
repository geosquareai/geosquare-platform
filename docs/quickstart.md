# Quick start

This takes about 5 minutes. You run the server, upload a sample CSV, read the catalog, read cells from the API, and open the map.

Each command below was run against this repository. The output shown is copied from that run. Your IDs and timestamps will differ.

Requirements: Python 3.11 or newer, and [uv](https://docs.astral.sh/uv/).

## 1. Install

From the repository root:

```zsh
uv sync
```

## 2. Start the server

```zsh
GSQ_INTERNAL_ROUTES=1 uv run uvicorn geosquare_platform.app:app --port 8000
```

The `GSQ_INTERNAL_ROUTES=1` flag is needed for the map. Use it on your own machine only. Never run a public server with this flag. See [Privacy and internal routes](privacy-and-internal-routes.md).

Leave this terminal open. Open a second terminal for the next steps.

## 3. Check that the server is up

```zsh
curl http://127.0.0.1:8000/health
```

Output:

```json
{"status":"ok"}
```

## 4. Check the sample CSV

The sample file has 256 rows, one per cell. The first column is `gid`.

```zsh
curl -F "file=@notebooks/sample/sample_cells.csv" http://127.0.0.1:8000/import/analyse
```

Output (shortened):

```json
{"ok":true,"rows":256,"key_column":"gid","key_kind":"gid","level":12,"status":"certain", ...}
```

This step stores nothing. It reports that `gid` is the GID column and the level is 12.

## 5. Upload the sample

```zsh
curl -F "file=@notebooks/sample/sample_cells.csv" \
  -F 'value_fields=[{"name":"people","logical_type":"numeric","semantics":"COUNT","unit":null},{"name":"income_index","logical_type":"numeric","semantics":"MEASUREMENT","unit":"index"}]' \
  -F "license=Sample data in this repository, for testing only" \
  -F "share_policy=internal" \
  -F "title=Quickstart sample" \
  http://127.0.0.1:8000/datasets
```

Output:

```json
{"dataset_id":"ID-L12-cfd6318882eb","level":12,"rows":256,"unique_cells":256,"value_fields":["people","income_index"],"dropped_columns":[],"valid":true}
```

Copy the `dataset_id`. The next steps use `ID-L12-cfd6318882eb`. Replace it with your own ID.

If you upload the same file with the same settings again, the server answers 409. The dataset already exists. To start over, stop the server and delete the `data/cells` folder.

## 6. Read the catalog

```zsh
curl http://127.0.0.1:8000/catalog
```

Output (shortened):

```json
[{"dataset_id":"ID-L12-cfd6318882eb","domain_code":"ID","level":12,"dataset_type":"cell_values","row_count":256, ...,"governance":{"license":"Sample data in this repository, for testing only","share_policy":"internal", ...},"extent":{"bbox_wgs84":[106.76466320316271,-6.227709590360788,106.77197529858725,-6.220392182247332], ...}, ...}]
```

The catalog reads manifests only. It does not open the Parquet files.

## 7. Read cells inside a box

Use a box that is inside the `extent` from step 6. The format is `min_lon,min_lat,max_lon,max_lat`.

```zsh
curl "http://127.0.0.1:8000/datasets/ID-L12-cfd6318882eb/cells?bbox=106.7640,-6.2280,106.7660,-6.2250&limit=3"
```

Output:

```json
{"cells":[{"gid":"H2X545M5X4E2","people":44.0,"income_index":7.1},{"gid":"H2X545M5X4E4","people":0.0,"income_index":1.4},{"gid":"H2X545M5X4L2","people":177.0,"income_index":9.9}]}
```

Note: this route returns values per cell. It does not check `share_policy`. Use it only with data you can show.

## 8. Roll up to a parent level

This route is internal. It works because of the flag from step 2.

```zsh
curl "http://127.0.0.1:8000/datasets/ID-L12-cfd6318882eb/rollup?level=10&sum=people"
```

Output (shortened):

```json
{"rows":[{"parent_gid":"H2X545M5X4","cells":16,"people":1467.0},{"parent_gid":"H2X545M5X5","cells":80,"people":8725.0}, ...]}
```

## 9. Open the map

The map needs the WASM package. Copy it from a local engine build first. See [Development](development.md#browser-map-wasm).

```zsh
cp -R ../geosquare-engine/bindings/wasm/pkg web/map/pkg
```

Then open the map in your browser:

```text
http://127.0.0.1:8000/map
```

The page reads the Parquet file in the browser, then draws the cells. The map also loads maplibre and hyparquet from CDNs, so you need internet access.

## 10. Stop the server

In the first terminal, press `Ctrl+C`.

---

## Ringkasan Bahasa Indonesia

Panduan ini menjalankan server, mengunggah CSV contoh, membaca katalog, membaca sel per kotak, dan membuka peta. Jalankan server dengan `GSQ_INTERNAL_ROUTES=1` hanya di mesin sendiri, karena peta butuh rute internal. Jika unggahan diulang dengan isi dan pengaturan yang sama, server menjawab 409. Untuk mulai dari awal, hentikan server lalu hapus folder `data/cells`.
