# IDX Decision Desk

Perangkat analisis teknikal & pendukung keputusan untuk saham **IDX (Bursa Efek Indonesia)**, berbasis Python + sumber data publik **Yahoo Finance**. Satu pintu (`idx.py`) untuk seluruh mode: penilaian sinyal bergaya *walk-forward*, pemindaian LQ45, panel kondisi pasar & geopolitik, engine keputusan dengan ukuran posisi, pencatat portofolio, laporan, **profil fundamental emiten** (`profil`), **kalender peristiwa emiten** (`kalender`: jadwal rilis laporan keuangan & tanggal ex-dividen), hingga **validasi kesegaran data (bar harian vs kutipan live)**.

> **Bukan nasihat keuangan.** Sistem ini alat bantu disiplin & kontrol risiko, **bukan mesin pencari alpha**. Rekam jejak backtest menunjukkan keunggulannya ada pada *manajemen risiko* (potong kerugian), bukan pada memprediksi arah harga. Semua angka berasal dari feed publik gratis yang bisa tertinggal/revisi.

---

## Daftar isi
- [Struktur modul](#struktur-modul)
- [Prasyarat & instalasi](#prasyarat--instalasi)
- [Instal di Termux (Android)](#instal-di-termux-android)
- [Mulai cepat](#mulai-cepat)
- [Semua mode](#semua-mode)
- [Konfigurasi (`config.json`)](#konfigurasi-configjson)
- [Tampilan adaptif (layar sempit / Termux)](#tampilan-adaptif-layar-sempit--termux)
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
| `stocknews.py` | Headline Google News RSS per emiten + penanda **umur berita** (segar/basi), plus kueri bertema (`by_query`). |
| `company.py` | Profil fundamental 1 emiten (mode `profil`): laporan keuangan tahunan, rasio, dividen/split, jejak aksi korporasi dari arus kas, pemegang saham, tema berita proyek/rencana, dan **penanda** (merah/kuning/hijau). Cache JSON per ticker di `data/profil/` + TTL. **Tidak pernah ikut menghitung skor.** |
| `kalender.py` | **Kalender peristiwa emiten** (mode `kalender`): tanggal rilis laporan keuangan berikutnya + tanggal ex-dividen, jarak dalam **hari bursa**, konsensus EPS/revenue kuartalan, DPS & cum-date. Dua permintaan per emiten (`Ticker.calendar` lalu `.info`), cache per kode di `data/kalender.json` + TTL. Setiap tanggal diberi label **asalnya** (`kalender sumber` vs `field cadangan`). Peristiwa juga muncul di `check`, `profil`, dan panel `brief`. **Tidak pernah ikut menghitung skor** — hanya lapisan peringatan. |
| `store.py` | Persistensi: `config.json`, `data/positions.json`, `data/trades.jsonl`, `data/profil/` (cache profil), `data/kalender.json` (cache peristiwa), `history.csv`, `reports/`. Semua path relatif ke file ini, `encoding="utf-8"`. |
| `ui.py` | Terminal UI `rich` berwarna (panel/tabel + spinner) dengan **tata letak adaptif**, **ikon judul & verdik** (tiga tier: `IDX_ICONS=emoji|symbol|ascii|none`, legend) dan **indikator proses** (`wait` beranimasi, `stage` untuk langkah yang mencetak hasilnya sendiri, `progress` untuk pemindaian massal). Fallback teks polos kalau `rich` tak ada / output di-pipe. |
| `requirements.txt` | Daftar dependensi untuk mesin biasa (`pip install -r`). |
| `setup-termux.sh` | Installer sekali-jalan untuk Termux/Android. |
| `buka-terminal.cmd` | Peluncur Windows (cmd/PowerShell): buka `idx.py` di **Windows Terminal** supaya ikon emoji + warna penuh tergambar. |

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

**Tampilan tidak perlu disetel.** Lebar terminal Termux dibaca otomatis; tabel yang tidak muat langsung berubah jadi daftar vertikal (rekaman) sehingga angkanya tetap utuh — lihat [Tampilan adaptif](#tampilan-adaptif-layar-sempit--termux). Putar HP ke landscape kalau ingin tetap melihat bentuk tabel.

---

## Mulai cepat

```bash
python idx.py                          # menu interaktif (pilih nomor)
python idx.py brief                    # dashboard keputusan lengkap
python idx.py minggu                   # "dengan modal ini, saham apa layak beli 1 minggu?"
python idx.py check BBCA               # keputusan 1 saham + rencana entry/stop/target + lot
python idx.py profil BBCA              # fundamental: keuangan, rasio, dividen, kepemilikan, proyek
python idx.py kalender                 # jadwal rilis laporan & ex-dividen watchlist + posisi yang dipegang
python idx.py kalender BBCA TLKM --days 60   # emiten tertentu, jendela diperlebar
python idx.py pos add BBCA --lot 5 --harga 9000 --cat "buy on dip"
python idx.py pos                      # status posisi terbuka + P&L live
python idx.py backtest BBCA            # aturan yang sama atas sejarah: daftar transaksi + biaya dua sisi
python idx.py peka BBCA                # grid 45 kombinasi ambang × stop × kalibrasi + vonis overfit
python idx.py backtest --semua         # portofolio gabungan: satu modal, rebutan slot, cap likuiditas
python idx.py cekdata --semua          # validasi seluruh watchlist
```

Override sekali jalan (tidak disimpan): `--modal 250000000 --risiko 0.5 --entry 25 --ts 3 --top 10 --rebal 42 --slip 0.3`.

---

## Semua mode

| Perintah | Fungsi |
|----------|--------|
| `python idx.py` / `menu` | Menu interaktif: pilih nomor (1–9, `0`, `b`, `p`, `s`, `f`, `k`) atau ketik perintah lengkap. `k` = kalender peristiwa. |
| `brief [--refresh]` | Dashboard keputusan: panel pasar → scanner → portofolio → rencana aksi + panel `JADWAL YANG MENYERET POSISI INI` (murni dari cache kalender, tanpa permintaan jaringan tambahan). |
| `check <T>` | Keputusan 1 saham: skor, rencana entry/stop/target, ukuran lot, validasi data live, konteks fundamental dari cache, + panel `PERISTIWA TERDEKAT` (rilis laporan/ex-dividen). |
| `profil <T> [--refresh]` | **Profil fundamental 1 emiten**: identitas & usaha → tabel keuangan tahunan → rasio & kesehatan → **penanda** → valuasi analis → dividen & split → aksi korporasi → pemegang saham → proyek & rencana (berita bertema) → **peristiwa terdekat** (rilis laporan/ex-dividen dari cache kalender) → cara baca. `--refresh` memaksa tarik ulang; cache di `data/profil/<KODE>.json`. |
| `kalender [<T> ...] [--semua] [--days N] [--refresh]` | **Kalender peristiwa emiten**: perkiraan tanggal rilis laporan keuangan + tanggal ex-dividen, jarak dalam hari bursa (`H-3`), konsensus EPS/revenue kuartal, DPS & cum-date. Default = watchlist sebatas `top`; **posisi yang dipegang selalu ikut diperiksa**. `--semua` = seluruh universe (±80 permintaan, sekali jalan karena cache 24 jam). Panel tambahan: `JADWAL MENYERET POSISI ANDA`, `EMITEN YANG TIDAK PUNYA JADWAL DEPAN`. |
| `minggu [<T>] [--kartu]` | Ranking horison 5 hari + rekam jejak + 1 rencana + berita. `<T>`/`--kartu` → tampilan kartu (BERITA → HASIL → PENDUKUNG → KEPUTUSAN). |
| `scan [--top N]` / `analyze` / `score` | Pemindaian universe LQ40 dengan skor walk-forward. |
| `market` | Panel kondisi pasar & geopolitik. |
| `backtest <T>` | Simulasi walk-forward 1 saham: aturan masuk/keluar yang sama dengan mode `check`, plus **daftar transaksi per posisi** dan **biaya dua sisi**. |
| `backtest --semua [<T> ...]` | **Backtest portofolio**: beberapa saham rebutan satu modal — slot terbatas, risiko per posisi, cap porsi & likuiditas (ADV). Tanpa daftar ticker = pakai watchlist (sebatas `top`). |
| `peka <T>` | **Uji kepekaan otomatis**: grid ambang skor × trailing stop × jarak kalibrasi (45 kombinasi) + vonis overfit. |
| `pos [add/cut <T>]` | Status & pencatatan posisi terbuka (P&L, stop, skor, risiko tersisa). |
| `cfg [show/set K V]` | Watchlist, modal, risiko, ambang. |
| `hist [<T>] [--days N]` | Riwayat skor, delta, transaksi & rekap P&L terealisasi. |
| `cekdata [<T> ...] [--semua]` | Asal-usul angka: sumber hidup/matinya, umur data, kecocokan bar harian vs kutipan live. |
| `ikon` | Uji tampil di terminal sendiri: legenda, contoh verdik, contoh tabel & panel — untuk memastikan ikon benar-benar tergambar. |

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
| `fee_buy_pct` | 0.15 | Fee beli (% dari nilai transaksi, belum termasuk slippage). |
| `fee_sell_pct` | 0.25 | Fee jual + PPN jual (% dari nilai transaksi). |
| `slip_pct` | 0.05 | Slippage tambahan **per sisi** — dipakai di `backtest`, `backtest --semua`, `peka`; ubah sekali jalan dengan `--slip 0.3`. |
| `max_pos_pct` | 20.0 | Porsi ekuitas maksimum untuk satu posisi (cap ukuran di `backtest --semua`). |
| `max_volume_pct` | 1.0 | Bagian maksimum dari nilai transaksi harian (ADV 20 hari) yang boleh dibeli — cap likuiditas. |
| `profile_ttl_hours` | 12.0 | Umur maksimum cache profil fundamental (`data/profil/`) sebelum `profil` menarik ulang. Lewati sekali jalan dengan `--refresh`. |
| `profile_news_days` | 180 | Jendela hari untuk berita bertema di panel PROYEK & RENCANA. |
| `profile_news_limit` | 4 | Jumlah headline maksimum per tema — naikkan kalau `profile_news_days` diperlebar. |
| `kal_ttl_hours` | 24.0 | Umur maksimum cache peristiwa per emiten (`data/kalender.json`). `check`/`profil` menarik ulang bila lebih tua dari ini; `--refresh` memaksa. |
| `kal_days` | 30 | Jendela **hari bursa** yang ditampilkan kalender — ke depan dan ke belakang. Ubah sekali jalan dengan `kalender --days 60`. |

> `--days` dipakai dua mode dengan arti berbeda: di `hist` = berapa hari riwayat skor, di `kalender` = lebar jendela hari bursa.

Ubah lewat CLI: `python idx.py cfg set capital 5000000`.

---

## Tampilan adaptif (layar sempit / Termux)

Lebar terminal diambil dari `Console` rich — deteksi ukuran terminal asli (Termux ikut), dan `IDX_UI_WIDTH` untuk memaksa. `ui.table()` lalu memilih salah satu dari tiga tata letak sehingga **tidak ada informasi yang terpotong di rasio layar apa pun**:

| Kondisi | Tata letak | Contoh nyata |
|---------|-----------|--------------|
| semua kolom muat satu baris | tabel normal | scan/`check` di desktop (≥104 kolom) |
| hanya kolom teks yang kepanjangan | tabel dengan kolom teks dibungkus (`max_width` + `overflow="fold"`); angka tetap utuh | scan 8 kolom di 80–96 kolom |
| tetap tidak muat | **rekaman**: tiap baris jadi blok `> <kode>` + pasangan `label nilai` yang dipacking per baris, lipat kalau perlu | ranking `minggu` 11 kolom & `pos` 10 kolom di HP portrait (≤72 kolom) |

Kontrol manual:

- `IDX_UI_WIDTH=<n>` — paksa lebar (kalau auto-detect meleset, atau untuk output yang di-pipe/direkam; default pipe 104).
- `IDX_UI_TABLE=1` — paksa mode tabel. Hati-hati: kalau memang tidak muat, rich akan memotong sel dengan `…`.

Sudah disapu 24–120 kolom untuk tiga bentuk tabel terberat (scan 8 kolom, berita 6 kolom dengan judul panjang, posisi 10 kolom): 0 baris meluber, 0 nilai hilang. Mode polos tanpa `rich` ikut adaptif (daftar vertikal saat terminal sempit).

### Ikon & kerapihan baca

Setiap judul panel membawa ikon sesuai isinya, dan setiap verdik membawa ikon arah, jadi hasil bisa dipindai mata sebelum dibaca:

| Ikon | Arti |
|------|------|
| 🚀 BELI KUAT · 🟢 BELI · 🚨 SIAGA · 👀 WATCHLIST · 🟡 PEGANG/HOLD · ⏳ TUNGGU · 🟠 KURANGI · 🔴 JUAL · ⛔ JUAL KUAT / HINDARI | arah verdik (naik → turun) |
| 📡 | status data live (harga siapa, jam berapa, setua apa) |
| 🛡️ | validasi/eviden — angka sudah dicocokkan dengan live |
| 🌐 kondisi pasar · 📰 berita · ⚖️ keputusan · 📅 rencana/mingguan · 💰 ukuran/portofolio · ⚠️ anggaran risiko · 📖 cara baca/legenda · 🧭 menu · ⚙️ konfigurasi · 🧪 backtest/uji · 🔎 hasil scan · 🎯 level & target | pengelompokan blok |

Padanan tier `symbol` (konsol Windows lama, semua satu kolom): `▲/▼` sinyal metode · `↑↑` BELI KUAT · `↑` BELI · `!` SIAGA · `◇` WATCHLIST · `—` HOLD/PEGANG · `…` TUNGGU · `↓` KURANGI · `▽` JUAL · `↓↓` JUAL KUAT · `√` validasi · `(•)` status data · `▌` analisa/scanner · `¤` ukuran/portofolio · `▪` berita · `○` kondisi pasar · `≈` keputusan · `□` rencana · `△` risiko · `≡` cara baca · `#` konfigurasi · `∴` backtest · `►` penanda rekaman.

Baris `LEGENDA ...` ikut dicetak di panel MENU supaya tidak perlu menebak makna simbol.

Kontrol:

- `IDX_ICONS=emoji` (default di Termux/Windows Terminal/VS Code) · `symbol` · `ascii` · `none`.
- Konsol Windows lama (cmd/PowerShell biasa) tidak sanggup menggambar emoji, jadi ada **tiga tier** yang dipilih otomatis:
  1. **emoji** — terminal modern (juga dipakai saat `WT_SESSION`/VS Code terdeteksi).
  2. **symbol** — konsol lama: simbol BMP satu kolom (`↑` BELI, `↓↓` JUAL KUAT, `√` validasi, `(•)` status data, `▮` analisa). Sebelum mode ini dipilih, program mencoba mengubah codepage output ke **UTF-8** (`SetConsoleOutputCP(65001)`) — tanpa itu `▲` muncul sebagai `^` dan `–` sebagai karakter rusak.
  3. **ascii** — kalau UTF-8 pun ditolak, atau output di-pipe / `rich` tidak terpasang (padding dihitung per karakter): `+` BELI, `--` JUAL, `V` validasi, `(~)` status data.
- Mode teks polos (tanpa `rich`) selalu pakai ASCII.
- **Mau emoji penuh di Windows?** Konsol PowerShell/cmd lama (conhost) secara fisik tidak bisa menggambar emoji — tidak ada pengaturan yang mengubah itu. Buka jendelanya lewat **Windows Terminal**:

  ```
  buka-terminal.cmd            :: menu interaktif, ikon emoji penuh
  buka-terminal.cmd brief      :: dashboard harian di Windows Terminal
  ```

  Script-nya mencoba `wt` dulu; kalau alias `wt` mati di Windows kamu, ia membuka Terminal lewat `shell:AppsFolder`. Alternatif manual: Settings → Apps → Advanced app settings → App execution aliases → nyalakan **Terminal**, lalu jalankan `wt python idx.py`.
- Glyph yang punya dua bentuk (🛡 ⚖ ⚠ 📅 ⚙) ditulis dengan **U+FE0F** (emoji presentation selector) supaya terminal dan `rich` sepakat lebar 2 kolom — tanpa itu border panel bisa miring.
- Lebar baris diukur dengan **kolom terminal** (`rich.cells.cell_len`), bukan jumlah karakter; kalau pakai `len()`, tiap ikon membuat baris bocor 1 kolom lalu dibungkus di tengah blok.
- Ikon tidak pernah mengubah angka: yang berubah hanya penanda judul, penanda baris rekaman (`▸ KODE`) dan kolom verdik.
- Di mode rekaman, label kolom disejajarkan (`lot 5 · avg 760`), kolom kosong tidak dicetak lagi sebagai label menggantung, header tabel diseragamkan KAPITAL.

Mau memastikan ikon benar-benar tergambar di perangkat sendiri?

```bash
python idx.py ikon        # legenda + contoh verdik + contoh tabel & panel pada lebar terminal saat ini
```

`python idx.py ikon` mencetak mode yang terdeteksi beserta saran kalau ikon belum versi penuh. Kalau simbol masih kotak ▯/ karakter rusak di konsol Windows: buka **Windows Terminal** (dari Store, jalankan `wt` lalu `python idx.py`) untuk emoji penuh, atau naik-turunkan tier manual sampai ketemu yang bersih: `set IDX_ICONS=symbol` / `set IDX_ICONS=ascii` (PowerShell: `$env:IDX_ICONS="ascii"`). Di Termux, simbol kotak biasanya berarti font tidak memuat emoji → Preferences → Styles → Font.

### Indikator proses

Setiap langkah lambat menampilkan baris berputar `| ambil data… 3s` lalu ditutup `ok  ambil data… (3.4s)` — **termasuk langkah yang selesai dalam 0,1 s** (dulu langkah cepat sengaja dibisukan, itu penyebab "kadang tampil kadang tidak"). Aturannya:

- Bingkai pertama ditulis sinkron, bukan menunggu thread, jadi tidak ada langkah yang lolos tanpa tampilan.
- `wait` bersarang hanya menggambar yang terluar → tidak ada dua spinner saling menghapus baris.
- Langkah yang mencetak panelnya sendiri (`analyze`, `score`, `backtest`) memakai `ui.stage()` (satu baris `… sedang mengerjakan`) karena spinner animatif akan menimpa panel tersebut.
- Pemindaian 40 saham (`scan`, `minggu` ranking) memakai bar progres `[█░] n/40 ≈Ns lagi` — kini juga aktif di mode ranking, dulu dimatikan.
- **Kalau output di-pipe/direkam** (`idx.py brief > log.txt`, `| tee`, `| less`) spinner sengaja tidak dicetak sama sekali; itu by design agar log bersih. Di Termux jalankan langsung di shell (atau `script -c` untuk merekam sambil tetap animasi).

---

## Sumber data & validasi

**Yang terbukti jalan:** Yahoo `v8/finance/chart` — `interval=1m&range=1d` (kutipan berjalan, ±1 mnt lag, membawa kalender sesi 09:00–16:15 WIB) dan `interval=1d` (bar harian). IHSG `^JKSE` dipakai sebagai kalender bursa.

**Yang mati / buntu:** `api.idx.co.id`, `www.idx.co.id/primary/*`, MobileWeb IDX (403 Cloudflare), Yahoo v7 quote (401 crumb), Google Finance (JS-rendered). Tick data sejati butuh feed IDX/OVOP berbayar.

**Jebakan simbol:** `BBCA` polos di Yahoo = ETF Amerika. Engine memaksa `.JK` dan **menolak** data berzona waktu ≠ +7, jadi tidak ada salah sekuritas senyap.

**Fundamental (`profil`):** laporan keuangan, rasio, dividen/split, pemegang saham, rekomendasi analis & target harga semuanya dari **Yahoo Finance** (`yfinance`: `info`, `income_stmt`, `balance_sheet`, `cashflow`, `dividends`, `splits`, `major_holders`, `institutional_holders`, `recommendations`, `calendar`, `analyst_price_targets`). Cakupannya terbukti luas untuk `.JK` — diuji dari LQ45 (BBCA, TLKM, AMRT) sampai saham kecil (BEEF). Yang **tidak** tersedia di jalur gratis: struktur kepemilikan resmi + beneficial owner (endpoint IDX 403), data ESG/sustainability (kosong), berita per emiten dari Yahoo (kosong → diganti Google News RSS), dan tanggal rilis earnings terformat (`earnings_dates` butuh `lxml`; yang dipakai sekarang field `calendar`). Beta Yahoo untuk `.JK` kacau (BBCA −0,017) jadi tidak pernah ditampilkan.

**Kalender peristiwa (`kalender`):** sumber utamanya property **`Ticker.calendar`** dari yfinance — `Earnings Date` (daftar), `Ex-Dividend Date`, `Earnings Average` + `Revenue Average` (konsensus **kuartal**), ditambah `.info` untuk harga, `dividendRate`, dan field epoch cadangan. Diverifikasi nyata 18-10-2026 pada 8 emiten: kamus **`calendar` di DALAM `.info` kosong untuk semua ticker `.JK`** — yang mengisi hanya property `Ticker.calendar`. Field epoch (`earningsTimestampStart`, `exDividendDate`) **bisa basi**: untuk AMRT/PTBA/KLBF kalender mengembalikan daftar rilis kosong sementara epoch masih menyimpan tanggal tahun 2025 — karena itu cadangan dipakai hanya bila panggilan kalender gagal, dan barisnya selalu dilabeli `field cadangan, kemungkinan tanggal lama`. `dividendRate` adalah **tarif setahun** (kadang gabungan interim + final), jadi persen yang dicetak = imbal hasil tahunan, bukan potongan pada satu tanggal. Konsensus EPS kuartal kadang salah satuan (PGAS dikirim `0,0065`; MDKA `-0,00298`) → disaring pada 0,5%–25% dari harga saham dan **yang dibuang dikatakan dibuang**. `WGNI.JK` menjawab 404. Tanggal **belum diverifikasi silang ke IDX** (semua endpoint idx.co.id 403 Cloudflare).

**Basis skor vs basis harga (penting!):** skor walk-forward **selalu** dihitung dari bar harian resmi — `scan`, `check`, `minggu`, `pos`, dan `brief` membaca angka yang sama persis. Kutipan live (`check`, `minggu`, `brief`, `pos`) hanya dipakai sebagai **harga untuk stop/target/ukuran lot**; kalau harga live beda lebih dari satu tick dari bar terakhir, panel EVIDEN mencetak baris `Basis angka` yang menyebutkan keduanya. Dulu kutipan live ikut menimpa bar terakhir sebelum indikator dihitung, sehingga tabel ranking dan keputusan bisa menunjuk dua skor berbeda untuk saham yang sama (selisih sampai ±30 poin dari perbedaan harga 0,5%) — sekarang tidak bisa lagi. **Pemindaian seluruh universe sengaja tidak menyentuh live** (biaya satu request per ticker).

---

## Cara baca hasil

- **STATUS DATA** (baris kepala): harga live, jam, "N mnt lalu", label sesi (PASAR BUKA / PASCAPERDAGANGAN / SESI SELESAI), dan sumber. Ini membuktikan angka berasal dari feed yang hidup.
- **VALIDASI DATA** (panel): daftar `Bahaya` / `Perhatian` / `ok`.
  - **Bahaya** = candle hari ini masih berjalan (angka bisa berubah), gap tanggal, OHLC tidak cocok dengan live > toleransi, lonjakan >25%, volume 0.
  - **Perhatian** = berita basi (>72 jam), feed cacat (bar hilang pada banyak saham sekaligus = masalah sumber, bukan bursa libur).
  - **ok** = bar harian konsisten dengan kutipan live; data boleh dipercaya.
- **SKOR** = konfluensi indikator bergaya walk-forward, terkalibrasi ke horison. `entry ≥ ambang` → layak; `≤ flat` → keluar. Ambang `entry`/`flat`/`ts_mult` yang berlaku sekarang dicetak **di semua panel teks saran** (`check` → RENCANA & CARA BACA, `pos` → catatan keluar, `brief` → panel konfigurasi), termasuk saat di-override sekali jalan (`check BBCA --entry 30 --flat -15`) — tidak ada lagi baris yang menempel angka −20/2,5×ATR sementara config menyatakan lain. Kalau aturan keluar sudah terpenuhi di skor kini, panel RENCANA mengatakannya dengan `SUDAH TERPENUHI`, bukan menyuruh menunggu ambang yang lebih dalam.
- **GATE / SIAGA**: kalau IHSG < EMA200, gerbang menahan beli; status **SIAGA** memotong ukuran lot jadi sepertiga.
- **Trailing stop** = 2.5×ATR dari puncak sejak entry — aturan keluar berbasis risiko, bukan tebakan arah.
- **Rekam jejak (edge vs base rate)** = seberapa sering sinyal serupa berakhir untung dibanding peluang acak. Angka tipis = keunggulan kecil.
- **Umur berita** (kolom `umur`): "baru (<1 jam)" … "N.N hari lalu". Berita hanya **penanda perhatian**, bukan prediktor.
- **`Basis angka`** (baris terakhir panel EVIDEN): menjelaskan bar mana yang menghasilkan skor dan harga mana yang dipakai untuk stop/target/lot. Kalau keduanya beda, keduanya dicetak — tidak ada angka yang diam-diam menimpa angka lain.
- **`backtest` — kolom ALASAN KELUAR**: kalau hampir semua exit karena *trailing stop*, yang bekerja adalah disiplin keluar, bukan kemampuan memilih arah. `P&L BERSIH` sudah dipotong fee beli, fee jual, dan slippage. Baris bertanda `*` masih terbuka sampai bar terakhir.
- **`profil` — panel PENANDA**: baris berlabel `BAHAYA` / `PERHATIAN` / `PLUS` / `INFO` adalah aturan sederhana yang bisa dibaca dalam satu detik: rilis laporan ≤10 hari bursa, laba turun 2 tahun beruntun, revenue mengecil, arus kas bebas negatif di mayoritas tahun, utang bersih >2×/ >4× EBITDA, payout ratio >100%, lembar beredar bertambah >5% (dilusi), ROE, fokus kepemilikan, dan sisa upside menurut target analis. Penanda **tidak mengubah skor** — fungsinya memberi alasan untuk memperkecil posisi atau menunggu, bukan menambah keyakinan arah.
- **`profil` — KEUANGAN vs RASIO**: tabel KEUANGAN adalah angka **setahun buku** (per Desember), sementara baris margin/tumbuh di panel RASIO dihitung dari **trailing 12 bulan**. Keduanya bisa beda (dan itu normal) — jangan membandingkan angka di antara dua panel itu seperti membandingkan apel dengan apel. Untuk bank, rasio arus kas & D/E sengaja dicetak "tidak berlaku" (utang bank = dana nasabah).
- **`profil` — PEMEGANG SAHAM & PROYEK**: yang ada hanya persentase agregat + nama yang kebetulan muncul di judul berita, ditandai **belum terverifikasi**. Google News hanya dibaca **judulnya** dan difilter agar benar-benar menyebut kode/singkatan emiten; artikel yang tidak cocok dibuang, jadi "tidak ada headline" bisa berarti tidak ada berita, bukan tidak ada data.
- **`profil` — dua angka sumber yang memang TIDAK rekonsiliasi** (sengaja ditampilkan berdua, bukan disamarkan): (1) panel RASIO mencetak **PBV versi Yahoo** dan **PBV dari tabel keuangan** (`ekuitas ÷ lembar beredar`) — kalau selisihnya >5% muncul baris `PERHATIAN`, dan yang bisa Anda audit ulang hanyalah versi neraca (TLKM: 1,58 vs 1,44). (2) persen **insider + persen float bisa menjumlah di atas 100%** (Yahoo memakai dua estimator berbeda; TLKM 55,4% + 47,1% = 102,6%) — panel PEMEGANG sekarang memperingatkannya dan memberi angka cadangan `100% − insider`.
- **KONTEKS FUNDAMENTAL** di `check` dan kartu `minggu`: dibaca dari **cache saja** (tanpa network, jadi tidak memperlambat). Kalau belum pernah `idx.py profil <KODE>` dijalankan, panelnya bilang begitu — itu bukan error. Cache >24 jam diberi peringatan umur. Setiap angka diberi label dasarnya — `PER/ROE/div (12 bln)` adalah trailing, `laba FY2025` adalah setahun buku — karena keduanya bisa berbeda jauh untuk saham yang sama (TLKM: ROE 12-bulan 17,7% vs laba buku FY2025 −21,8%).
- **`kalender` — kolom `H`**: `H-3` = 3 **hari bursa** lagi, `HARI INI` = tanggalnya hari ini, `H+12 lewat` = tanggal sumber yang sudah berlalu dan sengaja tetap ditampilkan (menunjukkan field itu tidak diperbarui sumber, bukan bahwa peristiwanya fiktif). Perhitungan sesi hanya Senin–Jumat — **libur nasional IDX belum dikurangkan**, jadi jarak sebenarnya bisa 1–2 hari lebih panjang.
- **`kalender` — kolom `KETERANGAN`**: tiap baris menyebut **dari mana tanggalnya**. Tanpa label = `Ticker.calendar` (sumber yang sama dengan penanda merah di `profil`, jadi kedua surface tidak mungkin berbeda angka). Berlabel `field cadangan, kemungkinan tanggal lama` = jatuh ke epoch mentah `.info` yang untuk sebagian emiten masih tahun lalu.
- **`kalender` — panel `JADWAL MENYERET POSISI ANDA`**: hanya emiten yang benar-benar Anda pegang, dengan nilai position dan konsekuensinya — rilis laporan = satu berita menggerakkan seluruh nilai (persiapan: kecilkan posisi sebelum tanggalnya); ex-dividen = harga kehilangan sebesar DPS setelah tanggal itu, jadi stop/target harus dibaca ulang, dan dividen kena **PPh final 10%** (menahan sampai cum-date bukan celah gratis).
- **`kalender` — panel `EMITEN YANG TIDAK PUNYA JADWAL DEPAN`**: tiga kelompok yang dibedakan terang — *hanya punya tanggal lewat* (jadwal periode ini belum tercatat sumber), *masih di luar jendela* (disarankan `--days N`), dan *rusak/tidak mengembalikan data* (404 atau sumber kosong). **Tidak munculnya tanggal ≠ tidak ada peristiwa.**
- **Peristiwa di `check`, `profil`, `brief`**: panel `PERISTIWA TERDEKAT` / `JADWAL YANG MENYERET POSISI INI` dibaca **dari cache kalender** (`brief` murni cache, tanpa jaringan; `check`/`profil` menarik ulang bila cache lebih tua dari `kal_ttl_hours`). Kalau isinya "belum ada di cache kalender", itu berarti belum pernah di-fetch — bukan berarti tidak ada jadwal.
- **`peka` — KESIMPULAN GRID**: yang dicari bukan sel terbaik, tapi **seluruh grid searah**. `STABIL` = ≥80% kombinasi punya drawdown lebih dangkal dari beli-dan-tepak; `RENTAN / OVERFIT` = hasil bagus hanya tinggal di beberapa sel. Kalau median return masih negatif, "STABIL" berarti konsisten *lebih kecil rugi*, bukan konsisten untung.
- **`backtest --semua` — panel SAAT SINYAL DITOLAK**: sinyal yang layak tapi tidak bisa dieksekusi tidak dibuang, dihitung per penyebab (slot penuh / batas porsi ekuitas / cap likuiditas ADV / uang tunai tak cukup 1 lot). Dominan "tak cukup uang tunai" = kendala modal, bukan sinyal buruk. Kolom `MODAL DIPAKAI` hanya berisi posisi yang benar-benar dieksekusi.

---

## Metodologi singkat

Skor konfluensi dihitung **walk-forward**: bobot rekalibrasi tiap `rebal` bar memakai hanya data masa lalu, sehingga tidak ada *look-ahead*. Horison (mis. 20 hari utk engine, 5 hari utk mode minggu) menandai jendela evaluasi akurasi. Ukuran posisi memakai risiko-per-posisi (% modal ÷ jarak stop). Gerbang IHSG>EMA200 memfilter rezim.

**Biaya** dihitung **per sisi**, bukan rata-rata bulat: beli `fee_buy_pct` + jual `fee_sell_pct` (broker + PPN jual) + `slip_pct` di kedua sisi — default 0,20% masuk dan 0,30% keluar (≈0,50% per putaran). Eksekusi tetap diidealkan: harga penutupan, H+1 dari sinyal, tanpa gap intraday, tanpa auto-rejection, tanpa T+2 yang menahan uang. `backtest --semua` menjalankan scheduler dua tahap: daftar transaksi per saham dari skornya masing-masing, lalu dieksekusi kronologis dengan merebutkan satu modal — urutan antrean mengikuti urutan daftar ticker, jadi hasilnya sedikit bergantung pada urutan itu.

---

## Batasan jujur

1. **Kontrol risiko, bukan alpha.** Backtest tidak menunjukkan keunggulan prediksi arah; nilai sistem ada pada memotong kerugian & disiplin ukuran posisi.
2. **±1 menit delayed, bukan tick.** Sumber publik gratis; sesi berlanjut pasca 15:20 dengan lelang terus-menerus — harga "live" tetap approximate.
3. **Berita = penanda attention**, hasil pembacaan kata kunci RSS, bukan sinyal sebab-akibat.
4. **Feed publik bisa bolong/revisi** — panel VALIDASI DATA ada justru karena itu.
5. **Universe tetap LQ45**; saham di luar daftar tidak dinilai sampai ditambahkan ke watchlist.
6. **Backtest portofolio tidak mensimulasikan antrean eksekusi**: satu modal direbutkan secara kronologis, tapi pembeliannya di harga penutupan dan penolakan dicatat apa adanya. "Ditolak karena uang tunai tidak cukup" pada modal kecil adalah konsekuensi nyata dari lot 100 lembar — bukan bug.
7. **Uji peka memakai satu rentang sejarah** yang sama untuk 45 kombinasi: grid mengukur *kepekaan parameter*, bukan *out-of-sample* sungguhan. Tambahkan data (atau ubah `--rebal`) lalu bandingkan.
8. **Termux tidak diverifikasi dari mesin ini** — portabilitas diaudit (0 impor Windows-only, 0 path absolut, semua `open()` UTF-8, shebang LF), tapi instal & akses jaringan hanya bisa dibuktikan di HP.
9. **Fundamental adalah konteks, bukan sinyal.** Mode `profil` (dan panel KONTEKS FUNDAMENTAL) sengaja **tidak** menyentuh skor walk-forward: rasio keuangan belum diuji out-of-sample di sini, jadi memasukkannya akan membuat klaim "keyakinan = hitung-hitungan" jadi bohong. Angka datang dari Yahoo, bisa terlambat satu periode atau direvisi, dan nama pemegang saham hanya terbaca dari judul berita. Sebagian field Yahoo juga **tidak konsisten satu sama lain** (PBV versi `info` ≠ PBV dari neraca; insider% + float% > 100%) — modul ini menampilkannya berdampingan dengan baris `PERHATIAN` alih-alih memilih satu diam-diam.
10. **Kalender adalah lapisan peringatan, bukan dasar eksekusi.** `kalender` tidak pernah masuk skor: tanggal peristiwa tidak diuji out-of-sample di sini. Tanggal rilis dari Yahoo adalah **perkiraan sumber** (bukan pengumuman resmi emiten), belum diverifikasi silang ke IDX karena seluruh endpoint idx.co.id menjawab 403, dan jaraknya dihitung dalam hari kerja tanpa mengurangi libur nasional. Untuk emiten yang sumbernya tidak punya tanggal, yang Anda dapat adalah pernyataan "tidak diketahui" — bukan tabel kosong yang terasa aman.

---

## Pemecahan masalah

| Gejala | Penyebab umum | Solusi |
|--------|---------------|--------|
| `simbol tidak menghasilkan data bursa Jakarta` | Nama bentrok dengan sekuritas luar | Pakai ticker IDX yang benar; engine sudah menolak otomatis. |
| `CERTIFICATE_VERIFY_FAILED` (Termux) | CA belum ada | `pkg install ca-certificates` lalu restart Termux. |
| Layout terlihat aneh / angka kurang | Auto-detect lebar terminal meleset | `export IDX_UI_WIDTH=<lebar asli>` (cek dengan `tput cols`). Selain itu sudah otomatis pindah ke tata letak rekaman. |
| `ModuleNotFoundError: rich` | `rich` belum terpasang | `pip install rich` (kalau tidak, UI jatuh ke mode polos, tetap jalan). |
| `numpy/pandas` gagal di Termux | Lewat pip, bukan pkg | `pkg install python-numpy python-pandas` (lihat `setup-termux.sh`). |
| Tidak ada baris loading sama sekali | Output di-pipe/direkam (bukan TTY) — spinner memang dimatikan agar log bersih | Jalankan langsung di shell Termux, tanpa `>` atau `| tee`. |
| Ikon jadi kotak `□`/`?` | Font terminal tidak memuat emoji (Windows lama, Termux font default) | Naikkan/turunkan tier manual sampai bersih: `IDX_ICONS=symbol` (simbol BMP) → `ascii` → `none`. Di Windows, buka **Windows Terminal** (`wt`) untuk emoji penuh; di Termux pasang font emoji (Preferences → Styles → Font). `python idx.py ikon` mencetak mode yang terdeteksi + sarannya. |
| Ikon jadi `%  @  (!)  (~)` **dan** tampilan tanpa warna | `rich` tidak terpasang di interpreter yang dipakai — program jatuh ke mode teks polos | `python -m pip install rich` (atau `bash setup-termux.sh`). Setelah itu ikon ikut naik ke tier emoji/simbol. Angka tidak pernah berubah oleh mode ikon apa pun. |
| Angka berbeda dari sekuritas | Skor dihitung dari bar harian (bisa separuh jalan saat sesi), ukuran posisi dari harga live | Baca baris `Basis angka` di panel EVIDEN; jalankan ulang setelah penutupan untuk angka final. |
| `profil` menampilkan banyak `n/a` + panel **BAGIAN YANG GAGAL DIAMBIL** | Yahoo tidak mengembalikan blok itu untuk emiten tersebut (bank sering tidak punya baris arus kas bebas; emiten kecil kadang tanpa `major_holders`) | Bukan bug — modul tidak menambal dengan angka karangan. Coba `idx.py profil <KODE> --refresh`; kalau tetap kosong, berarti sumbernya memang tidak punya. |
| Panel PROYEK & RENCANA kosong | Tidak ada headline yang **menyebut kode/singkatan emiten** pada tema itu; judul dari emiten lain dibuang lewat penyaring alias | Perlebar jendela: `idx.py cfg set profile_news_days 365` (naikkan `profile_news_limit` jika perlu). |
| Baris kalender berlabel `field cadangan, kemungkinan tanggal lama` | Endpoint `Ticker.calendar` tidak mengembalikan apa-apa untuk emiten itu, jadi mesin jatuh ke epoch `.info` — yang untuk AMRT/PTBA/KLBF masih menyimpan tanggal tahun lalu | Perlakukan tanggalnya sebagai **tidak diketahui**, bukan sebagai jadwal. Cek pengumuman resmi emiten; jangan `--refresh` berharap angka berubah bila sumbernya memang kosong. |
| Tanggal rilis/ex-div di kalender beda dengan pengumuman resmi | Semua tanggal berasal dari Yahoo dan **belum diverifikasi silang ke IDX** (endpoint idx.co.id 403 Cloudflare); tanggal rilis adalah perkiraan sumber | Gunakan sebagai pengingat untuk mengecek, bukan sebagai dasar order. Cocokkan dengan Keterbukaan Informasi IDX sebelum mengambil keputusan di sekitar tanggalnya. |
| Jarak `H-3` terasa meleset | Hitungan sesi hanya Senin–Jumat; **libur nasional IDX belum dikurangkan** | Tambah 1–2 hari longgar. Untuk peristiwa penting, cek kalender libur di situs BEI. |
| Emiten dipegang tidak muncul di `brief` panel jadwal | `brief` hanya membaca cache dan tidak menyentuh jaringan; emiten itu belum pernah di-fetch | Jalankan `idx.py kalender` (atau `kalender --semua`) sekali — cache `data/kalender.json` lalu dipakai semua mode. |
| `kalender --semua` lama di jalan pertama | ±40 emiten × **dua permintaan** (kalender + quote) | Sekali jalan saja; 24 jam berikutnya (`kal_ttl_hours`) murni dari cache. Paksa ulang dengan `--refresh`. |
