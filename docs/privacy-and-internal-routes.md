# Privasi dan rute internal

Dokumen ini menjelaskan dua hal: cara export menjaga anonimitas, dan rute mana yang hanya boleh dipakai di dalam tim.

## Export k-anonymity

Export hanya menerbitkan hitungan yang sudah digabung. Setiap sel hasil punya nilai `count` minimal `k`.

Cara kerja (lihat `src/geosquare_platform/export.py`):

1. Ambil setiap sel. Hitungan kolom COUNT adalah angka anonimitas.
2. Sel dengan hitungan minimal `k` langsung keluar.
3. Sel dengan hitungan di bawah `k` digabung ke induknya. Penggabungan diulang sampai hitungan mencapai `k`.
4. Di level paling kasar, sisa sel yang masih di bawah `k` tidak dikeluarkan. Jumlahnya dicatat di `suppressed_cell_count` dan `suppressed_count`.
5. Kolom TOTAL dijumlahkan ikut induknya. Kolom lain dibuang dan dicatat di `dropped_fields`.
6. Platform memeriksa bahwa jumlah keluaran plus jumlah yang ditekan sama dengan jumlah masukan. Jika pemeriksaan ini gagal, server tidak mengirim hasil export.

Aturan `k`:

- Default 5.
- Minimal 2. Nilai di bawah 2 ditolak.
- Keputusan tim: setting default 5 dan minimal 2 diterima. Tidak ada aturan hukum Indonesia yang bisa dijadikan rujukan untuk nilai ini.

## Rute internal

Rute ini mengembalikan nilai per sel atau jumlah per induk dari data sumber:

| Rute | Mengembalikan |
|---|---|
| `GET /datasets/{id}/rollup` | Jumlah per induk. Dengan level sama dengan dataset, nilai per sel asli |
| `POST /query/join` | Jumlah gabungan beberapa dataset |
| `POST /query/compact-join` | Jumlah di sel yang tercakup compact set |
| `POST /query/time-join` | Jumlah baris kiri yang cocok dengan baris kanan |

Semua rute ini menjawab 404 kecuali `GSQ_INTERNAL_ROUTES=1`.

Catatan penting:

- Gerbang ini hanya memeriksa variabel lingkungan. Ini bukan login, dan tidak ada pemeriksaan pengguna.
- Server yang memasang `GSQ_INTERNAL_ROUTES=1` harus tetap privat, misalnya di jaringan internal atau di belakang proxy dengan autentikasi.
- Export publik tetap lewat k-anonymity. Rute internal tidak.

## Risiko

- Dengan k = 2, satu orang di sel kecil bisa membuka nilai orang lain jika kolom total juga keluar. Jaga agar keluaran tetap internal.
- Query berulang bisa mempersempit sel. Batasi query atau terbitkan rilis tetap.
- Data mobile dan data demografi 50 m adalah aset tim. Jangan keluarkan data itu lewat export atau rute publik.
- Data Dukcapil belum punya lisensi publik. Jangan pakai di produk berbayar sebelum ada izin tertulis.
