# Privacy and internal routes

This page covers two things. It explains how export protects anonymity. It also lists which routes are for internal team use only.

## K-anonymity export

Export returns cells whose `count` is at least `k`. Cells below `k` merge into their parent. Export does not check `share_policy` today.

How it works (see `src/geosquare_platform/export.py`):

1. Read each cell. The COUNT column value is the anonymity number.
2. A cell with a count of at least `k` is output as is.
3. A cell with a count below `k` is merged into its parent. The merge repeats until the count reaches `k`.
4. At the coarsest level, cells that are still below `k` are not output. Their number is recorded in `suppressed_cell_count` and `suppressed_count`.
5. TOTAL columns are summed up with their parent. Other columns are dropped and listed in `dropped_fields`.
6. The platform checks that the number of output rows plus the number of suppressed rows equals the number of input rows. If this check fails, the server does not send the result.

Rules for `k`:

- Default: 5.
- Minimum: 2. A value below 2 is rejected.
- The `k` value is set per request, so it is configurable. A higher default is under review.
- Team decision: the default of 5 and the minimum of 2 are accepted. No Indonesian legal rule is available as a reference for this value.

## Internal routes

These routes return per-cell values or per-parent totals from the source data:

| Route | Returns |
|---|---|
| `GET /datasets/{id}/rollup` | Totals per parent. With a level equal to the dataset level, the original per-cell values |
| `GET /datasets/{id}/parquet` | The full Parquet file of one dataset |
| `POST /query/join` | Combined totals of several datasets |
| `POST /query/compact-join` | Totals in the cells that a compact set covers |
| `POST /query/time-join` | Totals of left rows that match right rows |

All of these routes answer 404 unless `GSQ_INTERNAL_ROUTES=1`.

Important notes:

- The gate only checks an environment variable. It is not a login, and there is no user check.
- A server that sets `GSQ_INTERNAL_ROUTES=1` must stay private. For example, run it on an internal network or behind a proxy with authentication.
- Public export still goes through k-anonymity. Internal routes do not.
- Export does not check `share_policy` today, so it is not a privacy gate for internal data.

## Public routes that are not gated

These routes return values per cell. They are public, and `GSQ_INTERNAL_ROUTES` does not affect them:

| Route | Returns | Note |
|---|---|---|
| `GET /datasets/{id}/cells` | Cell values inside a box, up to `limit` (default 1000) | Does not check `share_policy` |
| `GET /cells/{gid}/profile` | Values of one cell and its parent | Does not check `share_policy` |
| `GET /datasets/{id}/export` | Cells with count at least `k` | Public. Does not check `share_policy` |

A dataset with `share_policy` set to `internal` can still be read through these routes. Until the code checks `share_policy` on these routes, do not upload data you cannot make public to a server that others can reach.

## Risks

- With k = 2, one person in a small cell can be identified if a total column is also exported. Keep the output internal.
- Repeated queries can narrow down a cell. Limit queries, or publish fixed releases.
- Mobile data and 50 m demographic data are team assets. Do not release them through export or public routes.
- Dukcapil data has no public license yet. Do not use it in a paid product until written permission exists. Source: internal agreement, ask the owner.

---

## Ringkasan Bahasa Indonesia

Bagian ini adalah ringkasan. Versi lengkap ada di bagian Inggris di atas.

Export publik memakai k-anonymity: sel hasil punya hitungan minimal `k` (default 5, minimal 2). Nilai `k` bisa diatur per permintaan. Default yang lebih tinggi sedang ditinjau. Sel di bawah `k` digabung ke induknya. Rute internal (rollup, parquet, join) hanya aktif dengan `GSQ_INTERNAL_ROUTES=1`. Rute publik `/datasets/{id}/cells`, `/cells/{gid}/profile`, dan `/datasets/{id}/export` belum memeriksa `share_policy`. Jadi jangan unggah data yang tidak boleh dibagikan ke server yang bisa diakses orang lain.
