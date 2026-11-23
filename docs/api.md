# API

Semua contoh memakai `http://127.0.0.1:8000`. Upload memakai `multipart/form-data`. Query memakai JSON atau query string sesuai tabel.

Kode error yang sering muncul:

| Kode | Arti |
|---|---|
| 201 | Dibuat |
| 404 | Tidak ada. Untuk rute internal, 404 berarti rute tidak aktif |
| 409 | Dataset dengan isi yang sama sudah ada |
| 422 | Input tidak valid. Pesan menjelaskan kenapa |

## Ringkasan

| Metode | Path | Akses | Fungsi |
|---|---|---|---|
| GET | `/health` | publik | Cek server hidup |
| POST | `/import/analyse` | publik | Cek CSV, tanpa menyimpan |
| POST | `/datasets` | publik | Simpan CSV sebagai dataset sel |
| GET | `/datasets/{id}` | publik | Baca lagi dataset dan cek manifest |
| GET | `/datasets/{id}/export` | publik | Export dengan k-anonymity |
| GET | `/datasets/{id}/cells` | publik | Sel dalam kotak WGS 84 |
| GET | `/catalog` | publik | Daftar dataset dari manifest |
| GET | `/cells/{gid}/profile` | publik | Nilai satu sel dan induknya |
| POST | `/crosswalks` | publik | Simpan crosswalk |
| GET | `/crosswalks/{name}` | publik | Baca crosswalk |
| POST | `/crosswalks/{name}/apply` | publik | Terapkan crosswalk ke data sumber |
| GET | `/datasets/{id}/rollup` | internal | Jumlah per induk |
| POST | `/query/join` | internal | Join beberapa dataset lalu jumlahkan |
| POST | `/datasets/compact` | publik | Simpan compact set |
| POST | `/query/compact-join` | internal | Jumlah nilai di sel yang tercakup compact set |
| POST | `/time-datasets` | publik | Simpan dataset nilai waktu |
| POST | `/query/time-join` | internal | Join dua dataset nilai waktu lewat interval |
| GET | `/map` | publik | Halaman peta |

Rute "internal" menjawab 404 kecuali `GSQ_INTERNAL_ROUTES=1`.

## Dataset sel

### POST /import/analyse

Cek CSV dan laporkan kolom GID dan level. Tidak menyimpan apa pun.

```zsh
curl -F "file=@cells.csv" "http://127.0.0.1:8000/import/analyse"
```

Query: `confirm_digit_gid` (bool, default `false`). Pakai `true` jika semua GID berupa angka. Angka bisa juga kode biasa, jadi platform meminta konfirmasi.

### POST /datasets

Cek CSV, lalu simpan sebagai dataset sel dengan manifest.

Form:

| Field | Isi |
|---|---|
| `file` | CSV dengan kolom GID dan kolom nilai |
| `value_fields` | JSON array, misalnya `[{"name":"share","logical_type":"numeric","semantics":"MEASUREMENT","unit":"ratio"}]` |

Setiap item di `value_fields` punya:

- `name`: nama kolom di CSV. Nama yang dicadangkan kontrak tidak boleh dipakai, misalnya `coverage_ratio`.
- `logical_type`: `numeric` atau `categorical`.
- `semantics`: `COUNT`, `TOTAL`, `MEASUREMENT`, atau `CATEGORICAL`.
- `unit`: teks atau `null`.

Query: `confirm_digit_gid`.

Jawaban 201 berisi `dataset_id`, `level`, `rows`, `unique_cells`, `value_fields`, `dropped_columns`, dan `valid`.

### GET /datasets/{dataset_id}

Baca dataset lagi dan cek manifest-nya. Jawaban berisi manifest lengkap. Jika cek gagal, jawabannya 422.

### GET /catalog

Daftar metadata semua dataset. Hanya membaca manifest, tidak membuka file Parquet.

### GET /cells/{gid}/profile

Nilai untuk satu sel, induknya, dan dataset yang punya level lebih halus dari sel itu. Hanya dataset domain ID yang ikut. Dataset lain dilewati dan alasannya dicatat.

GID tidak valid menghasilkan 422.

### GET /datasets/{dataset_id}/cells

Sel yang titik pusatnya berada dalam kotak WGS 84.

Query:

- `bbox`: `min_lon,min_lat,max_lon,max_lat`. Wajib.
- `limit`: jumlah maksimum sel, default 1000, minimal 1.

## Export publik

### GET /datasets/{dataset_id}/export

Export dengan k-anonymity. Detail aturan ada di [Privasi dan rute internal](privacy-and-internal-routes.md).

Query:

- `count`: nama kolom COUNT di dataset. Wajib.
- `k`: ambang anonimitas. Default 5. Minimal 2.

Contoh:

```zsh
curl "http://127.0.0.1:8000/datasets/ID-L9-0123456789ab/export?count=population&k=5"
```

Jawaban berisi `k`, `count_field`, `input_level`, `rows` (setiap baris punya `gid`, `level`, `count`, `totals`, dan `source_cell_count`), `suppressed_cell_count`, `suppressed_count`, dan `dropped_fields`.

## Crosswalk

Crosswalk mengubah data sumber, misalnya per kelurahan, menjadi data per sel. Bobot per sumber harus berjumlah 1.

### POST /crosswalks

Form:

| Field | Isi |
|---|---|
| `file` | CSV dengan kolom `sumber_id,gid,bobot` |
| `name` | Nama crosswalk. Harus sesuai pola ID dataset |
| `domain_code` | Kode domain, misalnya `ID` |
| `level` | Level sel hasil |
| `weight_kind` | Jenis bobot, misalnya `luas` |

Crosswalk disimpan di folder `crosswalks` di bawah folder data. Validasi menolak bobot tidak positif, jumlah bobot per sumber yang tidak 1, GID di luar level, dan pasangan sumber-GID ganda.

### GET /crosswalks/{name}

Baca manifest dan jumlah baris crosswalk. Nama yang tidak ada menghasilkan 404.

### POST /crosswalks/{name}/apply

Terapkan crosswalk ke data sumber dan simpan hasilnya sebagai dataset COUNT baru.

Form:

| Field | Isi |
|---|---|
| `file` | CSV data sumber dengan kolom `sumber_id` dan kolom nilai |
| `dataset_id` | ID untuk dataset sel baru |
| `value_columns` | Daftar kolom nilai, dipisah koma |

Platform menolak sumber yang tidak ada di crosswalk. Platform juga memeriksa bahwa jumlah nilai di sel sama dengan jumlah nilai sumber. Jika tidak sama, dataset tidak disimpan.

## Rute internal

Rute ini hanya aktif dengan `GSQ_INTERNAL_ROUTES=1`.

### GET /datasets/{dataset_id}/rollup

Jumlahkan kolom ke level induk yang lebih kasar.

Query:

- `level`: level induk. Harus antara 0 dan level dataset.
- `sum`: nama kolom, dipisah koma.

Jawaban berisi `rows` (setiap baris punya `parent_gid`, `cells`, dan jumlah per kolom) dan `cells`.

Catatan: jika `level` sama dengan level dataset, setiap sel kembali dengan nilai aslinya. Ini alasan rute ini tidak boleh publik.

### POST /query/join

Gabungkan dua atau lebih dataset dengan level yang sama, lalu jumlahkan ke induk.

JSON:

```json
{
  "dataset_ids": ["ID-L9-aaaaaaaaaaaa", "ID-L9-bbbbbbbbbbbb"],
  "level": 8,
  "sum": ["population"],
  "gid_prefixes": ["2"]
}
```

- `dataset_ids`: minimal dua, tidak boleh sama.
- `level`: level induk.
- `sum`: kolom yang dijumlahkan.
- `gid_prefixes`: opsional. Batasi ke sel yang GID-nya diawali prefix ini.

Jika dua dataset punya kolom yang sama, nama di jawaban memakai `dataset_id.kolom`. Jawaban juga berisi `cells_joined`.

## Compact set

### POST /datasets/compact

Simpan compact set dari CSV dengan kolom tepat `gid,level`.

Set harus kanonik. Platform tidak memperbaiki set. Set yang tidak kanonik ditolak dengan 422, misalnya:

- baris ada di dalam baris lain;
- semua 25 anak sebuah induk ada, padahal seharusnya diganti induknya;
- baris duplikat;
- level tidak sama dengan panjang GID.

Set yang sama dua kali menghasilkan 409.

### POST /query/compact-join

Internal. Jumlahkan kolom di sel dataset yang tercakup compact set.

JSON:

```json
{"compact_id": "ID-COMPACT-0123456789ab", "dataset_id": "ID-L9-0123456789ab", "sum": ["population"]}
```

Jawaban: `matched_cells` dan `sums`.

## Dataset waktu (L7)

### POST /time-datasets

Simpan dataset nilai waktu. Satu baris per sel dan per periode.

Form:

| Field | Isi |
|---|---|
| `file` | CSV dengan kolom `gid,period` dan kolom nilai |
| `level` | Level sel |
| `period_type` | `year`, `month`, `day`, atau `hour` |
| `period_start` | Awal rentang, ikut `period_type` |
| `period_end` | Akhir rentang, tidak ikut (eksklusif) |
| `value_fields` | JSON array seperti di `/datasets` |
| `bucket_hours` | Hanya untuk `hour`: 1, 2, 3, 4, 6, 8, atau 12 |

Aturan waktu:

- Label periode: `2026`, `2026-11`, `2026-11-19`, atau `2026-11-19T04`. Tidak ada offset zona waktu.
- Semua waktu dalam UTC. Konversi WIB, WITA, atau WIT sebelum menyimpan.
- Label jam harus berupa awal bucket. Contoh: bucket 4 jam hanya boleh mulai di 00, 04, 08, 12, 16, atau 20.
- Setiap nilai `MEASUREMENT` harus punya `time_aggregation`: `mean`, `min`, atau `max`.

### POST /query/time-join

Internal. Hitung baris kiri yang punya baris kanan di sel yang sama dan relasi interval yang cocok. Lalu jumlahkan kolom kiri.

JSON:

```json
{"left_id": "TIME-aaaaaaaaaaaa", "right_id": "TIME-bbbbbbbbbbbb", "relation": "contained", "sum": ["rain_mm"]}
```

`relation` bisa `equal`, `contained` (kiri ada di dalam kanan), `contains` (kiri memuat kanan), atau `overlap`. Default `contained`.

Jawaban: `matched_rows`, `sums`, `relation`, `left_id`, dan `right_id`.

## Halaman peta

- `GET /map`: halaman peta dengan lapisan sel.
- `GET /map/pkg/{filename}`: file pendukung halaman peta.
