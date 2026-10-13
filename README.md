# IDX Decision Desk

Perangkat analisis teknikal & pendukung keputusan untuk saham **IDX (Bursa Efek Indonesia)**, berbasis Python + sumber data publik **Yahoo Finance**. Satu pintu (`idx.py`) untuk seluruh mode: penilaian sinyal bergaya *walk-forward*, pemindaian LQ45, panel kondisi pasar & geopolitik, engine keputusan dengan ukuran posisi, pencatat portofolio, laporan, hingga **validasi kesegaran data (bar harian vs kutipan live)**.

> **Bukan nasihat keuangan.** Sistem ini alat bantu disiplin & kontrol risiko, **bukan mesin pencari alpha**. Rekam jejak backtest menunjukkan keunggulannya ada pada *manajemen risiko* (potong kerugian), bukan pada memprediksi arah harga. Semua angka berasal dari feed publik gratis yang bisa tertinggal/revisi.

---

## Daftar isi
- [Struktur modul](#struktur-modul)
- [Prasyarat & instalasi](#prasyarat--instalasi)
- [Instal di Termux (Android)](#instal-di-termux-android)
- [Mulai cepat](#mulai-cepat)
- [Semua mode](#semua-mode)
- [Konfigurasi (`config.json`)](#konfigurasi-configjson)
- [Sumber data & validasi](#sumber-data--validasi)
- [Cara baca hasil](#cara-baca-hasil)
- [Metodologi singkat](#metodologi-singkat)
- [Batasan jujur](#batasan-jujur)
- [Pemecahan masalah](#pemecahan-masalah)

---

## Struktur modul

| File | Tanggung jawab |
|------|----------------|
| `idx.py` | Entrypoint tunggal + **menu interaktif**; routing semua mode, override CLI, pelaporan. |
| `idx_analyzer.py` | Engine inti: unduh bar harian, indikator, skor konfluensi *walk-forward*, `snapshot()`, `decide()`. Menolak simbol non-Jakarta (validasi zona waktu +7). |
| `live.py` | Kutipan **near-realtime** (±1 mnt), label sesi, kalender bursa, cek kesegaran, **cross-validation** bar harian vs harga live, probe sumber (`cekdata`). |
| `market_state.py` | Panel kondisi pasar & geopolitik (IHSG vs EMA200, volatilitas, tekanan), cache TTL 30 mnt. |
| `portfolio.py` | Status posisi terbuka: P&L, jarak stop/target, aturan keluar, ukuran lot. |
| `stocknews.py` | Headline Google News RSS per emiten + penanda **umur berita** (segar/basi). |
| `store.py` | Persistensi: `config.json`, `data/positions.json`, `data/trades.jsonl`, `history.csv`, `reports/`. Semua path relatif ke file ini, `encoding="utf-8"`. |
| `ui.py` | Terminal UI `rich` berwarna (panel/tabel + spinner). Fallback polos kalau `rich` tak ada / output di-pipe. |
| `requirements.txt` | Daftar dependensi untuk mesin biasa (`pip install -r`). |
| `setup-termux.sh` | Installer sekali-jalan untuk Termux/Android. |

---

## Prasyarat & instalasi

**Python 3.10+** dan paket: `numpy`, `pandas`, `yfinance`, `rich` (opsional tapi dianjurkan untuk tampilan berwarna).

### PC / server (Windows, macOS, Linux)
```bash
pip install -r requirements.txt
```
Jalankan dari folder project:
```bash
python idx.py            # menu interaktif
python idx.py check BBCA
```

> Di Windows, satu baris `sys.stdout.reconfigure(encoding="utf-8")` sudah dibungkus `try/except`; output aman walau terminal cp1252.

---

## Instal di Termux (Android)

**Jangan** `pip install numpy pandas` langsung di Termux — keduanya harus dikompilasi untuk ARM dan hampir selalu gagal. `setup-termux.sh` memasang numpy & pandas dari **paket bawaan Termux** yang sudah terkompilasi, sisanya (`yfinance`, `rich`) lewat pip.

### 1. Pindahkan project dari PC ke HP
Letakkan seluruh folder (`*.py`, `config.json`, `setup-termux.sh`, `requirements.txt`, dan `data/` bila ingin portofolio lama ikut) ke HP. Pilih salah satu:

- **Lewat folder Download + storage**
  ```sh
  termux-setup-storage
  mkdir -p ~/idx && cp -r /sdcard/Download/idx-analyzer/* ~/idx/ && cd ~/idx
  ```
- **Lewat `rclone`** (Google Drive/Dropbox): `pkg install rclone` → `rclone copy remote:idx .`
- **Lewat HTTP lokal** (PC & HP satu WiFi): di PC jalankan `python -m http.server 8000` dari folder project, lalu di HP unduh tiap file dari `http://<ip-pc>:8000/...`.

### 2. Instal & uji
```sh
cd ~/idx
sh setup-termux.sh
python idx.py cekdata          # tes pipa data: internet + SSL + endpoint hidup?
python idx.py minggu AMRT
python idx.py brief
```

Kalau `cekdata` menampilkan harga + jam, seluruh mode lain praktis ikut jalan (kode-nya sama, hanya beda data).

---

## Mulai cepat

```bash
python idx.py                          # menu interaktif (pilih nomor)
python idx.py brief                    # dashboard keputusan lengkap
python idx.py minggu                   # "dengan modal ini, saham apa layak beli 1 minggu?"
python idx.py check BBCA               # keputusan 1 saham + rencana entry/stop/target + lot
python idx.py pos add BBCA --lot 5 --harga 9000 --cat "buy on dip"
python idx.py pos                      # status posisi terbuka + P&L live
python idx.py cekdata --semua          # validasi seluruh watchlist
```

Override sekali jalan (tidak disimpan): `--modal 250000000 --risiko 0.5 --entry 25 --ts 3 --top 10 --rebal 42`.

---

## Semua mode

| Perintah | Fungsi |
|----------|--------|
| `python idx.py` / `menu` | Menu interaktif: pilih nomor, perintah dijalankan. |
| `brief [--refresh]` | Dashboard keputusan: panel pasar → scanner → portofolio → rencana aksi. |
| `check <T>` | Keputusan 1 saham: skor, rencana entry/stop/target, ukuran lot, validasi data live. |
| `minggu [<T>] [--kartu]` | Ranking horison 5 hari + rekam jejak + 1 rencana + berita. `<T>`/`--kartu` → tampilan kartu (BERITA → HASIL → PENDUKUNG → KEPUTUSAN). |
| `scan [--top N]` / `analyze` / `score` | Pemindaian universe LQ40 dengan skor walk-forward. |
| `market` | Panel kondisi pasar & geopolitik. |
| `backtest <T>` | Statistik akurasi sinyal pada horizon terpilih. |
| `pos [add/cut <T>]` | Status & pencatatan posisi terbuka (P&L, stop, skor, risiko tersisa). |
| `cfg [show/set K V]` | Watchlist, modal, risiko, ambang. |
| `hist [<T>] [--days N]` | Riwayat skor, delta, transaksi & rekap P&L terealisasi. |
| `cekdata [<T> ...] [--semua]` | Asal-usul angka: sumber hidup/matinya, umur data, kecocokan bar harian vs kutipan live. |

Mode bawaan engine `idx_analyzer` (`decide`, `week`) juga tersedia sebagai pilihan CLI.

---

## Konfigurasi (`config.json`)

| Kunci | Default | Arti |
|-------|---------|------|
| `watchlist` | `null` | `null` = pakai universe LQ45 bawaan; isi daftar untuk membatasi. |
| `capital` | 3000000 | Modal (Rp) untuk menghitung ukuran lot. |
| `risk_pct` | 1.0 | Risiko maksimal per posisi (% dari modal). |
| `max_total_risk_pct` | 3.0 | Batas risiko agregat semua posisi. |
| `max_positions` | 5 | Jumlah posisi terbuka maksimum. |
| `entry` | 20.0 | Ambang skor minimum untuk layak masuk. |
| `flat` | 0.0 | Ambang skor untuk keluar / tidak punya edge. |
| `ts_mult` | 2.5 | Pengali ATR untuk trailing stop. |
| `gate` | true | Aktifkan gerbang "hanya beli bila IHSG > EMA200". |
| `top` | 15 | Banyak baris teratas di scanner. |
| `rebal` | 63 | Jarak bar rekalibrasi bobot walk-forward. |
| `week_horizon` | 5 | Horison mode mingguan (hari bursa). |
| `week_risk_pct` | 5.0 | Risiko per ide pada mode minggu. |
| `week_max_pos_pct` | 0.9 | Porsi modal maksimum di mode minggu. |
| `week_news_days` | 14 | Jendela umur berita yang dianggap relevan. |

Ubah lewat CLI: `python idx.py cfg set capital 5000000`.

---

## Sumber data & validasi

**Yang terbukti jalan:** Yahoo `v8/finance/chart` — `interval=1m&range=1d` (kutipan berjalan, ±1 mnt lag, membawa kalender sesi 09:00–16:15 WIB) dan `interval=1d` (bar harian). IHSG `^JKSE` dipakai sebagai kalender bursa.

**Yang mati / buntu:** `api.idx.co.id`, `www.idx.co.id/primary/*`, MobileWeb IDX (403 Cloudflare), Yahoo v7 quote (401 crumb), Google Finance (JS-rendered). Tick data sejati butuh feed IDX/OVOP berbayar.

**Jebakan simbol:** `BBCA` polos di Yahoo = ETF Amerika. Engine memaksa `.JK` dan **menolak** data berzona waktu ≠ +7, jadi tidak ada salah sekuritas senyap.

Sinkronisasi live: bar hari ini diselaraskan ke harga terakhir sebelum menghitung stop/target/lot di jalur keputusan (`check`, `minggu`, `brief`, `pos`). **Pemindaian 40 saham sengaja tidak menyentuh live** (biaya per-ticker) — hal ini selalu diungkapkan di UI.

---

## Cara baca hasil

- **STATUS DATA** (baris kepala): harga live, jam, "N mnt lalu", label sesi (PASAR BUKA / PASCAPERDAGANGAN / SESI SELESAI), dan sumber. Ini membuktikan angka berasal dari feed yang hidup.
- **VALIDASI DATA** (panel): daftar `Bahaya` / `Perhatian` / `ok`.
  - **Bahaya** = candle hari ini masih berjalan (angka bisa berubah), gap tanggal, OHLC tidak cocok dengan live > toleransi, lonjakan >25%, volume 0.
  - **Perhatian** = berita basi (>72 jam), feed cacat (bar hilang pada banyak saham sekaligus = masalah sumber, bukan bursa libur).
  - **ok** = bar harian konsisten dengan kutipan live; data boleh dipercaya.
- **SKOR** = konfluensi indikator bergaya walk-forward, terkalibrasi ke horison. `entry ≥ ambang` → layak; `≤ flat` → keluar.
- **GATE / SIAGA**: kalau IHSG < EMA200, gerbang menahan beli; status **SIAGA** memotong ukuran lot jadi sepertiga.
- **Trailing stop** = 2.5×ATR dari puncak sejak entry — aturan keluar berbasis risiko, bukan tebakan arah.
- **Rekam jejak (edge vs base rate)** = seberapa sering sinyal serupa berakhir untung dibanding peluang acak. Angka tipis = keunggulan kecil.
- **Umur berita** (kolom `umur`): "baru (<1 jam)" … "N.N hari lalu". Berita hanya **penanda perhatian**, bukan prediktor.

---

## Metodologi singkat

Skor konfluensi dihitung **walk-forward**: bobot rekalibrasi tiap `rebal` bar memakai hanya data masa lalu, sehingga tidak ada *look-ahead*. Horison (mis. 20 hari utk engine, 5 hari utk mode minggu) menandai jendela evaluasi akurasi. Ukuran posisi memakai risiko-per-posisi (% modal ÷ jarak stop). Biaya bulat bolak-balik diasumsikan **0,4%** (beli ~0,15% + jual ~0,25%). Gerbang IHSG>EMA200 memfilter rezim.

---

## Batasan jujur

1. **Kontrol risiko, bukan alpha.** Backtest tidak menunjukkan keunggulan prediksi arah; nilai sistem ada pada memotong kerugian & disiplin ukuran posisi.
2. **±1 menit delayed, bukan tick.** Sumber publik gratis; sesi berlanjut pasca 15:20 dengan lelang terus-menerus — harga "live" tetap approximate.
3. **Berita = penanda attention**, hasil pembacaan kata kunci RSS, bukan sinyal sebab-akibat.
4. **Feed publik bisa bolong/revisi** — panel VALIDASI DATA ada justru karena itu.
5. **Universe tetap LQ45**; saham di luar daftar tidak dinilai sampai ditambahkan ke watchlist.
6. **Termux tidak diverifikasi dari mesin ini** — portabilitas diaudit (0 impor Windows-only, 0 path absolut, semua `open()` UTF-8, shebang LF), tapi instal & akses jaringan hanya bisa dibuktikan di HP.

---

## Pemecahan masalah

| Gejala | Penyebab umum | Solusi |
|--------|---------------|--------|
| `simbol tidak menghasilkan data bursa Jakarta` | Nama bentrok dengan sekuritas luar | Pakai ticker IDX yang benar; engine sudah menolak otomatis. |
| `CERTIFICATE_VERIFY_FAILED` (Termux) | CA belum ada | `pkg install ca-certificates` lalu restart Termux. |
| Tabel terpotong / layout berantakan | Terminal HP sempit | `export IDX_UI_WIDTH=50` atau putar landscape. |
| `ModuleNotFoundError: rich` | `rich` belum terpasang | `pip install rich` (kalau tidak, UI jatuh ke mode polos, tetap jalan). |
| `numpy/pandas` gagal di Termux | Lewat pip, bukan pkg | `pkg install python-numpy python-pandas` (lihat `setup-termux.sh`). |
| Angka berbeda dari sekuritas | Bar harian belum diselaraskan live | Jalankan `check`/`minggu`/`pos` (jalur keputusan sync live), bukan `scan`. |
