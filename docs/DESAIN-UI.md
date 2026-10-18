# Konsep desain UI/UX `idx.py` — terminal, satu layar, satu pertanyaan

Bukan dokumen niat. Setiap aturan di bawah punya alamat ke kode yang sudah ada (`ui.py`,
`idx.py`) dan bisa diuji dengan perintah yang dicetak di sini. Mockup visualnya:
**`docs/mockup-beranda.html`** (angkanya nyata, diambil 18-10-2026 16:32 dari mesin ini).

---

## 1. Prinsip yang memegang

| # | Prinsip | Konsekuensi desain | Sudah ada di |
|---|---------|--------------------|--------------|
| P1 | **Satu layar = satu pertanyaan** | Layar dibuka dengan kalimat pertanyaannya, bukan dengan nama mode. `check BBCA` menjawab "apa rencana saya di BBCA"; `beranda` menjawab "hari ini saya harus ngapain". | `header(title, sub)` |
| P2 | **Empat lapis, urutan tetap** | (1) KEPUTUSAN → (2) ALASAN → (3) BUKTI ANGKA → (4) CARA BACA & BATASAN. Lapis 4 tidak pernah boleh naik ke atas; lapis 1 tidak pernah boleh hilang karena data rusak. | panel `CARA BACA`, `decision_banner` |
| P3 | **Warna = makna, bukan hiasan** | satu semantik untuk seluruh aplikasi (lihat §2). Tidak ada panel yang "bagus" tapi warna ≠ artinya. | `C_VERDICT`, `C_MOOD`, `C_GRADE` |
| P4 | **Angka tidak pernah bicara sendiri** | tiap angka menempel pada *basis*-nya: `trailing 12 bln` vs `FY`, `bar harian` vs `live`, `Rp` vs `USD`, `cache 3,4 jam`. Nomor tanpa basis = bug desain, bukan estetik. | `basis_note()`, `duit()/lab()`, umur cache |
| P5 | **Ketiadaan data harus terbaca sebagai "tidak diketahui", bukan "aman"** | `n/a`, panel `BAGIAN YANG GAGAL DIAMBIL`, bucket `rusak/lewat/jauh`, baris abu-abu di `screen`. | `company`, `kalender`, `screen` |
| P6 | **Tidak ada angka yang hilang saat layar sempit** | degradasi bertingkat: tabel → kolom dibungkus → kartu vertikal → satu kolom per baris. Melebar/menyempit tidak boleh mengubah isinya. | `ui.table()` tingkat 1-2-3, `_records()` |
| P7 | **Determinisme** | perubahan UI tidak boleh mengubah satu digit pun. Uji tetap: bandingkan token angka antar tier ikon dan antar lebar. | §7 |

---

## 2. Sistem warna & tanda (token)

Satu palet, lima makna. Yang berubah hanya *ikon*-nya, tidak pernah artinya.

| Token | rich style | Makna | Dipakai di |
|-------|-----------|-------|-----------|
| `aksi.positif` | `green` / `bold white on green` | sistem memperbolehkan entry / menambah | verdik BELI*, PLUS |
| `aksi.waspada` | `yellow` | boleh tapi kecil / tunggu konfirmasi | SIAGA, HOLD, PERHATIAN |
| `aksi.negatif` | `orange_red1` → `bold white on red` | keluar / hindari | KURANGI, JUAL, BAHAYA |
| `fakta.netral` | `cyan` | konteks, bukan suruhan | WATCHLIST, IDENTITAS, LANGKAH |
| `fakta.samar` | `grey62`/`grey58` | keterangan, dasar angka, umur | caption, `ket`, catatan berita |
| `bahasa.tujuan` | `bold magenta` | angka sumber yang belum diuji (analis, konsensus) | VALUASI MENURUT ANALIS |

**Tanda wajib (bukan warna-saja, supaya terminal 16 warna & buta warna tetap kebaca):**
`BAHAYA` / `PERHATIAN` / `PLUS` / `INFO` selalu tercetak sebagai **kata + ikon**, tidak pernah
warna saja (`_penanda()` sudah begitu — pertahankan). Tambahannya: setiap baris merah
menyebut *nomor* yang membuatnya merah ("harga 2.800, stop 2.937").

---

## 3. Tipografi & ritme layar

- **Judul panel**: kata benda pendek, KAPITAL, tanpa titik. Ikon dari `title_icon()` —
  jangan tambah kata sifat ("PENTING!").
- **Baris metrik**: `LABEL ··· nilai  [grey62 keterangan]` (titik tiga rata, sudah jadi
  bahasa aplikasi ini). Satu baris satu angka pokok; angka kedua masuk keterangan.
- **Lebar aman**: 100 kolom adalah desain target; 118+ boleh dua kolom; ≤ 80 wajib satu kolom;
  ≤ 40 hanya kartu vertikal. `avail()` yang memutuskan, bukan hardcode.
- **Jeda**: satu baris kosong antar panel, tidak lebih. Panel ≠ dinding — panel adalah
  jawaban atas satu sub-pertanyaan.
- **Urusan panjang**: berita, ringkasan bisnis, `cara` → boleh dipotong, tapi **potong dengan
  elipsis dan sebut sisa** (`… (2 judul tak ditampilkan)`), jangan pernah sunting diam-diam.

---

## 4. Peta layar → pertanyaan

| Layar | Pertanyaan pengguna | Lapis 1 yang harus muncul duluan |
|-------|---------------------|-----------------------------------|
| `beranda` **(baru)** | "hari ini saya ngapain?" | daftar aksi posisi + gerbang pasar |
| `check <KODE>` | "apa rencana di saham ini?" | banner verdik + stop/target/lot |
| `minggu <KODE>` | "apa layak ditahan 5 hari?" | satu pilihan + faktornya |
| `scan` | "siapa yang paling kuat sekarang?" | tabel peringkat (bukan kondisi pasar) |
| `screen` | "siapa yang layak masuk radar saya?" | batas aktif + jumlah yang lolos |
| `pos` | "apa kata sistem atas posisi saya?" | PEMBERITAHUAN KELUAR + anggaran risiko |
| `kalender` | "tanggal apa yang bisa menendang stop saya?" | H-3 ke bawah |
| `profil <KODE>` | "bisnis apa ini, dan angkanya sehat?" | PENANDA (bukan identitas) |
| `backtest` / `peka` | "metodenya jujur tidak?" | verdict: kontrol drawdown, bukan alpha |

Aturan: layar mana pun yang membuka layar lain menyebut perintahnya di panel `LANGKAH
BERIKUT` — navigasi lewat teks, bukan lewat ingatan pengguna.

---

## 5. Konsep utama: **BERANDA** (satu layar keputusan)

Hari kerja user ini ±90 detik di terminal. Yang belum ada: satu layar yang menjawab
"ngapain sekarang" tanpa menyuruh membuka 4 mode.

```
┌ idx · BERANDA — 18-10-2026 16:32 · bar harian 18-10 · live 16:14 (±1 mnt) · cache ok ┐
│                                                                                        │
│  ▌SEKARANG (2 aksi, 0 entry)                                                           │
│  (!) PTBA  KELUAR   harga 2.800 ≤ stop 2.937 · skor −26.3      −258.000 (−13.3%)      │
│  (!) KLBF  KELUAR   harga 675  ≤ stop 712  · skor −71.3        −42.500 (−11.2%)       │
│  ( ) ENTRY BARU    GERBANG TERTUTUP — IHSG 5.185 < EMA200 · mood SANGAT BERISIKO      │
│                                                                                        │
│  ▌MOTOR KEPUTUSAN          IHSG ▓▓▓▓▓▓▓▓▓▓ 5.185 −19.5%/20h · HV 45.7%                │
│  penyesuaian pasar −22.1   breadth LQ45 >EMA50 0% >EMA200 2% · berita 248 ±0.46        │
│  penalti sektor −2.8       risiko terkunci 0 dari jatah 90.000 · slot 3/5              │
│                                                                                        │
│  ▌RADAR 5 TERATAS          1 MDKA  +66.6  ▓▓▓▓▓▓▓▓▓▓░ SIAGA      2.430                │
│                            2 AKRA  +64.7  ▓▓▓▓▓▓▓▓▓▓░  SIAGA      1.250               │
│                            3 BBNI  +62.2  ▓▓▓▓▓▓▓▓▓▓░  SIAGA      2.970               │
│                            … 8 dari 40 dinilai · yang tidak lolos saringan → `screen`  │
│                                                                                        │
│  ▌MENUNGGU 30 SESI         H-1 Sen 19-10 PGAS rilis laporan (perkiraan sumber)         │
│                            H-2 Sel 20-10 BBCA rilis laporan · konsensus EPS 123        │
│                            tidak diketahui: PTBA*, KLBF* (sumber belum isi periode ini)│
│                                                                                        │
│  ▌CARA BACA                skor mentah = walk-forward · akhir = + pasar + sektor;      │
│                            angka BERANDA = angka `scan`/`pos` pada saat yang sama,     │
│                            bukan angka baru; tanggal = estimasi Yahoo, IDX 403.        │
└────────────────────────────────────────────────────────────────────────────────────────┘
  [b]eranda  [c]ek KODE  [s]can  [k]alender  [p]os  [t]opup posisi  [q]uit
```

Pilihan desain yang disengaja di layar ini:

1. **Aksi dulu, konteks kedua.** Empat baris teratas sudah cukup untuk menutup laptop.
2. **Gerbang pasar ditampilkan sebagai baris, bukan sebagai cerita.** Kalau entry tidak
   mungkin, itu tertulis `GERBANG TERTUTUP` beserta penyebabnya — bukan daftar saham yang
   memancing orang beli di pasar yang salah.
3. **Gauge skor/▓▓▓ hanya ornamen arah.** Angka aslinya selalu ada di sebelahnya; gauge
   tidak pernah satu-satunya sumber nilai (P7: gauge tidak boleh membawa digit).
4. **`MENUNGGU` memakai pita 30 sesi** (sama dengan `kalender`) dan menyebut yang *tidak
   diketahui* eksplisit — tidak ada kolom yang kosong terasa aman.
5. **Tidak ada angka baru.** Semua baris dipetik dari fungsi yang sudah ada. Ini syarat
   masuk: `beranda` memanggil `eng.snapshot/verdict`, `pf.ringkasan`, `kal.peek`, `co.peek`
   (cache-only) — tidak mengundi ulang, tidak menghitung ulang skor.

---

## 6. Komponen baru di `ui.py` (primitif, bukan tema)

Empat primitif kecil menutupi kebutuhan §5 tanpa menyentuh mesin hitung. Semuanya harus
menyerah ke mode teks polos (`HAS_RICH = False`) dan ke tier ikon.

```python
def kv(label, nilai, ket="", level=None):
    """Satu baris metrik: 'LABEL ··· [bold]nilai[/]  [grey62 ket][/]' — bentuk yang sudah
    dipakai panel RASIO; dijadikan fungsi supaya titiknya tidak lagi diketik manual."""

def meter(frac, w=10, level="cyan"):
    """▓▓▓▓░░░░░░ untuk besaran 0..1 (breadth, penggunaan jatah risiko, umur cache).
    Hanya ornamen: pemanggil tetap mencetak angka aslinya di sel sebelah."""

def scorebar(v, lo=-100.0, hi=100.0, w=12):
    """─▓▓▓▓●▓▓░░ untuk skor konfluensi; posisi titik = v. Teks angka tidak ikut digambar,
    supaya mode ikon tidak pernah mengubah digit."""

def zone(title, lines, lebar_min=110, kiri=None, kanan=None):
    """Dua kolom kalau ≥ lebar_min, satu kolom kalau tidak. Gagal muat = tumpuk, jangan
    potong. Ini naiknya aturan yang sudah ada di ui.table() ke tingkat panel."""

def aksi_baris(badge, kode, sebab, angka_kanan):
    """Satu baris zona 'SEKARANG': badge (!) / ( ) + kode + SEBAB yang menyebut angka + P&L."""
```

Penanda waktu di baris pertama header (`bar harian 18-10 · live 16:14 · cache ok`) dipakai
di semua layar sebagai satu fungsi, supaya umur data tidak lagi ditulis dengan tiga gaya
berbeda di tiga mode.

---

## 7. Cara menguji setiap perubahan UI (obligatori)

```bash
# 1. digit tidak boleh berubah oleh tier ikon
for t in emoji symbol ascii none; do
  IDX_ICONS=$t IDX_UI_WIDTH=100 python idx.py beranda | grep -v 'hari lalu' \
    | grep -oE '[0-9][0-9.,]*' | md5sum; done        # harus 4 hash sama

# 2. lebar tidak boleh menghilangkan isi
for w in 40 60 80 100 118 140; do IDX_UI_WIDTH=$w python idx.py beranda | wc -l; done

# 3. tanpa rich tetap terbaca
python -c "import ui; ui.HAS_RICH=False" # lalu jalankan; fallback teks polos harus berisi
```

Kecuali tiga hal di atas hijau, perubahan UI dianggap belum selesai.

---

## 8. Interaksi

- **Menu keymap satu baris di kaki layar** (`[b]eranda [c]ek [s]can [k]alender [p]os [q]uit`)
  — sekarang ada di menu utama; usulan: tampilkan di kaki *setiap* layar, supaya pengguna
  tidak perlu kembali ke menu untuk tahu ke mana selanjutnya.
- **Setiap panel menyebut perintah penutupnya**, bukan tombol. "Detail: idx.py profil PGAS".
- **Kecepatan = urutan, bukan filter.** Jangan menyembunyikan panel yang tidak menarik;
  taruh di bawah dan beri judul yang membosankan dengan sengaja (`CARA BACA & BATASAN`).
- **Muat lama**: selalu `ui.wait()` + baris penyelesaian `ok/gagal (3.1s)`; jangan pernah
  membiarkan layar diam 10 detik tanpa pernyataan sedang apa.
- **Kegagalan**: satu kalimat + apa yang hilang + perintah ulangnya (`--refresh`). Tidak
  ada stack trace, tidak ada angka karangan.

---

## 9. Tahap implementasi

| Tahap | Isi | Risiko | Ukuran kerja |
|-------|-----|--------|--------------|
| P0 | `kv()`, `meter()`, `scorebar()` di `ui.py`; panel STATUS header satu fungsi | rendah — murni render | kecil |
| P1 | Layar `beranda` (mode baru, cache-only + satu `compute_scan`) + keymap di kaki | sedang — harus terbukti angka = `scan` pada saat sama | sedang |
| P2 | Zone dua kolom pada `brief`/`check` saat ≥110 kolom | sedang — sentuh banyak render | sedang |
| P3 | Palet semantic (ganti gaya literal di pemanggil dengan token §2) | rendah-tapi-luas | kecil per file, banyak file |

**Anti-goal (jangan dikerjakan):** grafik candlestick di terminal, spinner permanen,
progress bar bergaya loading web, warna latar penuh (kontras & Termux), font/ukuran custom,
atau apa pun yang membuat satu angka muncul dari dua jalur hitung.

---

## 10. Yang tetap tidak berubah

Skor tetap murni teknis; fundamental, kalender, dan saringan tetap lapisan peringatan;
satu angka satu fungsi; `profil` melabeli mata uang laporan, tidak mengonversi. Desain UI
boleh mengubah *urutan* dan *rupa*, tidak boleh mengubah *isi angka* atau *arti verdiktif*
satu baris pun. Mockup ini dianggap sah hanya kalau setiap angkanya bisa dilacak ke sebuah
perintah yang sudah ada.
