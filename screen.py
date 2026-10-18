"""Saringan multi-kriteria atas universe: valuasi, profitabilitas, dividen, likuiditas, lot.

Kenapa ini bukan `scan`: `scan` mengurutkan saham menurut konfluensi teknis walk-forward dan hanya
itu yang bisa dijawabnya. Saringan menjawab pertanyaan berbeda — "mana yang murah secara valuasi,
cukup likuid untuk dimasuki dan dikeluarkan lagi dengan uang saya, dan satu lot-nya tidak
menghabiskan modal?" Kedua jawaban boleh berbeda, karena memang dua pertanyaan.

Satu definisi per angka (aturan yang sama dipakai seluruh aplikasi ini):
  · tabel `KRIT` adalah satu-satunya tempat yang tahu nama kolom, satuan, arah mana yang baik, dari
    field mana nilainya diambil, dan bagaimana kalimat penolakannya — jadi filter, pengurutan,
    tabel, dan panel "kenapa tidak lolos" tidak mungkin berbeda pendapat;
  · `metrik()` memakai unit TAMPIL (persen sudah persen, miliar sudah miliar) sehingga batas yang
    Anda ketik (`roe_min=12`) dibandingkan dengan angka yang Anda lihat di tabel, bukan dengan
    pecahan mentah sumber. Ini bukan detail kecil: `returnOnEquity` Yahoo datang sebagai 0,218 dan
    `dividendYield` sebagai 7,36 — dua-duanya "persen" tapi satuannya beda;
  · kolom SKOR memakai `idx_analyzer.compute_scan` yang sama dengan mode `scan` dan selalu dipanggil
    dengan universe penuh: penyesuaian pasar dihitung dari breadth seluruh ticker, jadi subset
    kandidat akan mencetak angka yang berbeda dari `scan`;
  · format rupiah diwarisi dari `company.py` supaya `screen`, `profil`, dan `kalender` mencetak
    angka yang identik untuk fakta yang sama.

Satu permintaan `.info` per emiten (bukan `company.fetch` yang ikut menarik laporan keuangan
penuh), dicache per kode di `data/screen.json`. Batasan yang selalu ditulis terang di output:
sumbernya Yahoo bukan IDX (endpoint idx.co.id menjawab 403); PER kosong berarti sumber tidak
mengirim angka — umumnya emiten rugi, dan emiten begitu TIDAK dianggap lolos; `debtToEquity` tidak
ada untuk bank karena utangnya adalah dana nasabah; nilai buku sebagian emiten datang pada skala
yang salah (PGAS: bookValue 0,117 vs harga 1.190 → priceToBook 10.170,94) sehingga pita `wajar`
di KRIT menahannya sebagai "di luar nalar" — tidak dinilai, tidak lolos, tapi tetap disebut;
harga di tabel adalah kutipan cache, bukan ±1 menit seperti `check`.
"""

import json
import os
import time

import yfinance as yf

import company as co
import idx_analyzer as eng
import portfolio as pf
import ui

HERE = os.path.dirname(os.path.abspath(__file__))
FILE = os.path.join(HERE, "data", "screen.json")
LOT = pf.LOT

SCREEN_KEYS = ["longName", "sector", "regularMarketPrice", "marketCap", "sharesOutstanding",
               "trailingPE", "forwardPE", "priceToBook", "bookValue", "returnOnEquity",
               "profitMargins", "dividendYield", "fiveYearAvgDividendYield", "payoutRatio",
               "debtToEquity", "currentRatio", "revenueGrowth", "earningsGrowth", "averageVolume",
               "averageDailyVolume3Month", "targetMeanPrice", "recommendationKey"]


# ---------------------------------------------------------------- tampilan angka

def kelipatan(v):
    v = co.num(v)
    return "n/a" if v is None else f"{v:,.2f}"


def pct(v):
    """Nilai yang SUDAH dalam persen (setelah `metrik` menormalkan satuannya)."""
    v = co.num(v)
    return "n/a" if v is None else f"{v:,.1f}%"


def miliar(v):
    """Nilai dalam miliar rupiah — dicetak pendek gaya panel lain (241.94 M)."""
    v = co.num(v)
    return "n/a" if v is None else co.rp(v * 1e9)


def rupiah(v):
    v = co.num(v)
    return "n/a" if v is None else f"{v:,.0f}"


def poin(v):
    v = co.num(v)
    return "n/a" if v is None else f"{v:+.1f}"


# ---------------------------------------------------------------- kriteria
# `baik` = arah yang diuntungkan: "kecil" (PER, PBV, utang, lot) → batasnya maksimum,
#          "besar" (ROE, dividen, likuiditas, skor) → batasnya minimum.
# Argumen `per=12` memakai arah baik kriteria itu; `per_min=12` memaksa arah sebaliknya.
# `wajar` = pita nilai yang masih masuk nalar untuk emiten riil. Di luar pita, angkanya TIDAK
# dinilai dan TIDAK dianggap lolos: sumber terbukti mengirim sebagian emiten dengan skala yang
# salah — PGAS datang dengan bookValue 0,117 (harga 1.190) sehingga priceToBook 10.170,94, dan
# nilai buku semacam itu tidak bisa dibedakan dari emiten yang benar-benar mahal kalau kita
# membandingkannya tanpa checksani.

KRIT = {
    "per":    dict(lbl="PER", baik="kecil", fmt=kelipatan, satuan="kelipatan harga",
                   wajar=(0.05, 1000.0),
                   ambil=lambda m: m["per"],
                   sumber="trailingPE — kosong berarti sumber tidak mengirim angka (umumnya rugi)"),
    "pbv":    dict(lbl="PBV", baik="kecil", fmt=kelipatan, satuan="kelipatan buku",
                   wajar=(0.02, 100.0),
                   ambil=lambda m: m["pbv"],
                   sumber="priceToBook versi Yahoo; versi neraca yang bisa diaudit ada di `profil`"),
    "roe":    dict(lbl="ROE", baik="besar", fmt=pct, satuan="persen",
                   wajar=(-60.0, 200.0),
                   ambil=lambda m: m["roe"],
                   sumber="returnOnEquity datang sebagai pecahan (0,218) → dinormalkan ke 21,8%"),
    "div":    dict(lbl="DIV", baik="besar", fmt=pct, satuan="persen",
                   wajar=(0.0, 60.0),
                   ambil=lambda m: m["div"],
                   sumber="dividendYield — satuannya sudah persen, tidak dikali 100 lagi"),
    "payout": dict(lbl="PAYOUT", baik="kecil", fmt=pct, satuan="persen laba dibagikan",
                   wajar=(0.0, 300.0), ambil=lambda m: m["payout"],
                   sumber="payoutRatio (pecahan → persen)"),
    "dte":    dict(lbl="D/E", baik="kecil", fmt=pct, satuan="persen terhadap ekuitas",
                   wajar=(0.0, 5000.0),
                   ambil=lambda m: m["dte"],
                   sumber="debtToEquity; kosong untuk bank — utangnya dana nasabah"),
    "kap":    dict(lbl="KAP", baik="besar", fmt=miliar, satuan="miliar Rp",
                   ambil=lambda m: m["kap"], sumber="marketCap"),
    "likuid": dict(lbl="LIKUID/HARI", baik="besar", fmt=miliar, satuan="miliar Rp per hari",
                   ambil=lambda m: m["likuid"],
                   sumber="rata-rata volume × harga cache — proksi seberapa besar posisi bisa keluar"),
    "lot":    dict(lbl="1 LOT", baik="kecil", fmt=rupiah, satuan="rupiah",
                   ambil=lambda m: m["lot"], sumber="harga cache × 100 lembar"),
    "tumbuh": dict(lbl="REV ↑", baik="besar", fmt=pct, satuan="persen YoY",
                   wajar=(-95.0, 1000.0),
                   ambil=lambda m: m["tumbuh"], sumber="revenueGrowth (pecahan → persen)"),
    "laba":   dict(lbl="LABA ↑", baik="besar", fmt=pct, satuan="persen YoY",
                   wajar=(-300.0, 1000.0),
                   ambil=lambda m: m["laba"],
                   sumber="earningsGrowth — sering melonjak dari basis kecil, baca laporannya dulu"),
    "target": dict(lbl="UPSIDE", baik="besar", fmt=pct, satuan="persen ke target rerata analis",
                   wajar=(-90.0, 500.0),
                   ambil=lambda m: m["target"], sumber="targetMeanPrice dibanding harga cache"),
    "skor":   dict(lbl="SKOR", baik="besar", fmt=poin, satuan="poin konfluensi",
                   ambil=lambda m: m["skor"],
                   sumber="skor AKHIR `compute_scan` — angka yang sama dengan mode `scan` pada "
                          "saat yang sama (penyesuaian pasar punya cache sendiri ±30 menit)"),
}

# Batas siap pakai; `batas` memakai satuan KRIT, `kenapa` dicetak di kepala jadi preset bukan
# kotak hitam. `terjangkau` menambah batas lot dari modal config saat dibaca (lihat batas_preset).
PRESET = {
    "value": {"batas": {"per": 12, "pbv": 2.5, "roe": 12},
              "kenapa": "murah secara valuasi, tapi tetap menghasilkan di atas 12%"},
    "deviden": {"batas": {"div": 5, "payout": 80, "per": 20},
                "kenapa": "imbal hasil sumber ≥ 5% dan laba masih cukup untuk membiayainya"},
    "tumbuh": {"batas": {"tumbuh": 10, "laba": 10, "per": 35},
               "kenapa": "revenue dan laba tumbuh dua digit, valuasi boleh premium"},
    "sehat": {"batas": {"roe": 15, "dte": 100, "kap": 30000},
              "kenapa": "ROE tinggi, utang terhadap ekuitas terjaga, kapitalisasi besar"},
    "terjangkau": {"batas": {"likuid": 1},
                   "kenapa": "satu lot tidak melebihi porsi maksimum satu posisi, dan masih ada "
                             "likuiditas untuk keluar"},
}

URUT = ["likuid", "skor", "per", "pbv", "roe", "div", "kap", "lot", "target", "kode"]
# Harga tidak dibariskan sendiri: 1 LOT = harga × 100, jadi satu kolom cukup dan tabelnya muat di
# terminal 100–120 kolom tanpa diurai jadi kartu per emiten.
KOLOM = ["KODE", "1 LOT", "PER", "PBV", "ROE", "DIV", "KAP", "LIKUID", "SKOR", "VERDIK"]
LEGENDA = ("* = sudah dipegang · ! = nilai sumber di luar pita wajar · likuid = rata-rata nilai "
           "transaksi/hari (miliar Rp)")


# ---------------------------------------------------------------- cache

def _read():
    if os.path.exists(FILE):
        try:
            with open(FILE, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("items"), dict):
                return d
        except (OSError, ValueError):
            pass
    return {"items": {}}


def _write(d):
    os.makedirs(os.path.dirname(FILE), exist_ok=True)
    try:
        with open(FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1, ensure_ascii=False)
    except OSError:
        pass


def _one(code):
    rec = {"code": code, "sym": code + ".JK", "ts": time.time(), "err": None}
    try:
        info = yf.Ticker(rec["sym"]).info or {}
    except Exception as e:  # noqa: BLE001
        rec["err"] = f"{type(e).__name__}: {str(e)[:70]}"
        return rec
    for k in SCREEN_KEYS:
        rec[k] = co._js(info.get(k))
    if not co.num(rec.get("regularMarketPrice")):
        rec["err"] = "sumber tidak mengirim harga (symbol tidak dikenal atau datanya kosong)"
    return rec


def fetch(codes, force=False, ttl_h=24.0):
    """Cache per kode: hanya yang basi/belum ada yang menyentuh jaringan."""
    d = _read()
    out, nnet = {}, 0
    for c in codes:
        rec = d["items"].get(c)
        if not rec or force or (time.time() - rec.get("ts", 0) >= ttl_h * 3600):
            nnet += 1
            rec = _one(c)
            d["items"][c] = rec
            _write(d)
        out[c] = rec
    return out, nnet


# ---------------------------------------------------------------- metrik & saringan

def persen(x_):
    """Pecahan sumber → persen. None tetap None (bukan 0)."""
    v = co.num(x_)
    return None if v is None else v * 100


def metrik(rec, skor=None):
    """Semua angka satu emiten, sudah dalam satuan yang dipakai tabel dan batas."""
    px = co.num(rec.get("regularMarketPrice"))
    cap = co.num(rec.get("marketCap"))
    vol = co.num(rec.get("averageVolume")) or co.num(rec.get("averageDailyVolume3Month"))
    tgt = co.num(rec.get("targetMeanPrice"))
    return {
        "kode": rec.get("code"), "nama": rec.get("longName") or "", "harga": px,
        "lot": px * LOT if px else None,
        "per": co.num(rec.get("trailingPE")), "pbv": co.num(rec.get("priceToBook")),
        "roe": persen(rec.get("returnOnEquity")), "div": co.num(rec.get("dividendYield")),
        "payout": persen(rec.get("payoutRatio")), "dte": co.num(rec.get("debtToEquity")),
        "kap": cap / 1e9 if cap else None, "likuid": vol * px / 1e9 if vol and px else None,
        "tumbuh": persen(rec.get("revenueGrowth")), "laba": persen(rec.get("earningsGrowth")),
        "target": (tgt / px - 1) * 100 if tgt and px else None, "skor": skor,
    }


def batas_preset(nama, cfg):
    """Batas preset sebagai dict {kritis: (jenis, nilai)}; `terjangkau` mengambil lot dari modal."""
    b = {k: (jenis(k), float(v)) for k, v in PRESET[nama]["batas"].items()}
    if nama == "terjangkau":
        b["lot"] = ("maks", cfg["capital"] * cfg["max_pos_pct"] / 100.0)
    return b


def jenis(k):
    """Jenis batas default untuk satu kriteria: yang baik itu kecil → batasnya maksimum."""
    return "maks" if KRIT[k]["baik"] == "kecil" else "min"


def syarat_teks(batas):
    """Perintah saringan dalam satu baris — dipakai di kepala dan di panel penolakan."""
    return " · ".join(f"{KRIT[k]['lbl']} {'≤' if a == 'maks' else '≥'} {KRIT[k]['fmt'](v)}"
                      for k, (a, v) in urutkan(batas))


def urutkan(d):
    """Urutan kriteria tetap (tidak ikut urutan ketik user) supaya output dapat direplikasi."""
    return sorted(d.items(), key=lambda kv: list(KRIT).index(kv[0]))


def parse_args(rest, cfg):
    """`screen preset=value per_maks=12 likuid=5 urut=roe BBCA TLKM` → batas + ticker + preset.

    Batas lewat pasangan K=V, bukan satu bendera argparse per kriteria: belasan ambang baru di
    `--help` akan menutupi daftar mode, dan `per_maks=12` tetap terbaca seperti baris di tabel.
    """
    batas, tickers, preset, urut = {}, [], None, "likuid"
    for tok in rest or []:
        if "=" not in tok:
            tickers.append(co.code_of(tok))
            continue
        k, _, v = tok.partition("=")
        k, v = k.lower().strip(), v.strip()
        if k == "preset":
            if v not in PRESET:
                raise SystemExit("`preset` yang ada: " + ", ".join(sorted(PRESET)) + f" — bukan {v}.")
            preset = v
            for pk, pv in batas_preset(v, cfg).items():
                batas.setdefault(pk, pv)
            continue
        if k == "urut":
            if v not in URUT:
                raise SystemExit("`urut` yang ada: " + ", ".join(URUT) + f" — bukan {v}.")
            urut = v
            continue
        paksa = None
        for suf, a in (("_maks", "maks"), ("_min", "min")):
            if k.endswith(suf):
                k, paksa = k[: -len(suf)], a
        if k not in KRIT:
            raise SystemExit("kriteria yang dikenal: " + ", ".join(sorted(KRIT))
                             + " · plus preset= dan urut= · bukan " + k + ".")
        try:
            batas[k] = (paksa or jenis(k), float(v))
        except ValueError:
            raise SystemExit(f"batas {k} harus angka, bukan '{v}'.")
    return batas, tickers, preset, urut


def masuk_nalar(k, v):
    """False bila nilai kriteria k di luar pita `wajar` — angkanya ada, tapi tidak bisa dipercaya."""
    band = KRIT[k].get("wajar")
    return band is None or v is None or band[0] <= v <= band[1]


def uji(m, batas):
    """(lolos, gagal, tanpa_angka, di_luar_nalar) untuk satu emiten.

    Semua kriteria diuji, tidak berhenti di yang pertama, supaya panel penolakan bisa menyebut
    seluruh alasan. Emiten yang angkanya tidak ada di sumber TIDAK dianggap lolos: yang tidak bisa
    diperiksa tidak bisa lolos. Yang angkanya ada tapi mustahil (PBV 10.000×) juga tidak lolos —
    membandingkannya dengan batas sama saja memberi nilai pada sampah.
    """
    gagal, tanpa, aneh = [], [], []
    for k in [x for x in KRIT if x in batas]:
        arah, v = batas[k]
        got = KRIT[k]["ambil"](m)
        if got is None:
            tanpa.append(k)
        elif not masuk_nalar(k, got):
            aneh.append((k, got))
        elif (got > v) if arah == "maks" else (got < v):
            gagal.append((k, got))
    return (not gagal and not tanpa and not aneh), gagal, tanpa, aneh


# ---------------------------------------------------------------- render

def tampil(k, m):
    """Angka satu kriteria persis seperti yang dibaca di tabel; yang di luar pita diberi tanda !."""
    v = KRIT[k]["ambil"](m)
    s = KRIT[k]["fmt"](v)
    return s + "[red]![/]" if v is not None and not masuk_nalar(k, v) else s


def _baris(m, held, gate, entry):
    kode = f"[bold]{m['kode']}[/]" + ("[cyan]*[/]" if m["kode"] in held else "")
    sk = "[grey62]—[/]" if m["skor"] is None else f"[{'green' if m['skor'] >= 0 else 'red'}]" \
                                                   f"{m['skor']:+.1f}[/]"
    # verdik memakai fungsi & kosakata yang sama dengan `check`/`scan`, termasuk ambang entry
    # config dan gate IHSG>EMA200 yang berlaku — bukan tabel arah terpisah untuk angka yang sama
    vd = "[grey62]—[/]" if m["skor"] is None else ui.verdict_markup(
        eng.verdict(m["skor"], gate, entry))
    return (kode, tampil("lot", m), tampil("per", m), tampil("pbv", m),
            tampil("roe", m), tampil("div", m), tampil("kap", m), tampil("likuid", m), sk, vd)


def cmd_screen(cfg, rest=None, force=False, with_score=True):
    batas, tickers, preset, urut = parse_args(rest, cfg)
    if "skor" in batas and not with_score:
        raise SystemExit("Batas `skor` butuh skor walk-forward — jalankan tanpa --tanpa-skor.")
    uni = tickers or [co.code_of(t) for t in (cfg["watchlist"] or eng.UNIVERSE)]
    held = {co.code_of(t) for t in pf._load()}
    ttl = float(cfg.get("screen_ttl_hours", 24.0))
    top = int(cfg.get("top", 15))

    with ui.wait(f"mengambil metrik {len(uni)} emiten (Yahoo .info, cache {ttl:g} jam)…"):
        recs, nnet = fetch(uni, force=force, ttl_h=ttl)

    skors = {}
    if with_score:
        # Universe penuh, bukan hanya kandidat: penyesuaian pasar & sektor dihitung dari breadth
        # seluruh ticker, jadi subset akan mencetak skor yang beda dari mode `scan`.
        n = len(cfg["watchlist"] or eng.UNIVERSE)
        with ui.wait(f"menghitung skor walk-forward {n} saham (sumber sama dengan `scan`)…"):
            rows, _ = eng.compute_scan(cfg["watchlist"] or eng.UNIVERSE, rebal=cfg["rebal"])
        skors = {co.code_of(r["ticker"]): r for r in rows}

    met, tanpa_data = [], []
    for c in uni:
        rec = recs.get(c) or {}
        if rec.get("err"):
            tanpa_data.append((c, rec["err"]))
            continue
        s = skors.get(c)
        met.append(metrik(rec, float(s["final"]) if s else None))

    lolos, tolak = [], []
    for m in met:
        ok, gagal, tanpa, aneh = uji(m, batas)
        if ok:
            lolos.append(m)
        else:
            tolak.append((m, gagal, tanpa, aneh))

    def urut_key(m):
        if urut == "kode":
            return (m["kode"],)
        v = m[urut]
        return (1, 0.0) if v is None else (0, -v if KRIT[urut]["baik"] == "besar" else v)
    lolos.sort(key=urut_key)

    ui.header(f"SARINGAN SAHAM — {len(lolos)} LOLOS DARI {len(met)} DINILAI",
              (f"preset {preset} — {PRESET[preset]['kenapa']} · " if preset else "")
              + (syarat_teks(batas) or "tanpa batas")
              + f" · urut {urut} · {nnet} diunduh, {len(uni) - nnet} dari cache")

    if not met:
        ui.info_panel("HASIL", [
            "[yellow]Tidak ada metrik sama sekali[/] — semua symbol gagal di sumber. Lihat panel "
            "EMITEN TANPA DATA di bawah."], "red")
    elif lolos:
        ui.table(KOLOM, [_baris(m, held, bool((skors.get(m["kode"]) or {}).get("gate", True)),
                                 cfg["entry"]) for m in lolos[:top]], caption=LEGENDA)
        arah_urut = "kode A-Z" if urut == "kode" else (
            f"{'terkecil' if KRIT[urut]['baik'] == 'kecil' else 'terbesar'} lebih dulu")
        extra = "" if with_score else " Kolom SKOR/VERDIK kosong karena --tanpa-skor."
        if len(lolos) > top:
            ui.info_panel("BATAS TAMPIL", [
                f"· Dicetak {top} teratas dari [bold]{len(lolos)}[/] yang lolos — sisanya tidak "
                f"dibuang, hanya belum ditampilkan. Perlebar: [bold]--top {len(lolos)}[/]",
                f"· Urutan mengikuti arah baik kriteria: [bold]{KRIT[urut]['lbl']}[/] {arah_urut}"
                + (", emiten tanpa angka di kriteria itu ditaruh terakhir." if urut != "kode" else "."),
                extra.strip()], "yellow")
    else:
        ui.info_panel("HASIL", [
            "[yellow]Tidak ada emiten yang lolos semua batas.[/] Saringannya terlalu sempit, atau "
            "sumber tidak punya angka untuk kriteria itu — panel penolakan di bawah membedakan "
            "keduanya, dan [bold]idx.py scan[/] tetap memberi peringkat teknis tanpa syarat "
            "fundamental."], "yellow")

    if tolak:
        baris, kelompok, buta, mustahil = [], {}, {}, {}
        for m, gagal, tanpa, aneh in tolak:
            for k, got in gagal:
                kelompok.setdefault(k, []).append((got, f"{m['kode']} {KRIT[k]['fmt'](got)}"))
            for k in tanpa:
                buta.setdefault(k, []).append(m["kode"])
            for k, got in aneh:
                mustahil.setdefault(k, []).append((got, f"{m['kode']} {KRIT[k]['fmt'](got)}"))
        for k in sorted(kelompok, key=lambda x: -len(kelompok[x])):
            arah, v = batas[k]
            baris.append(f"· [bold]{KRIT[k]['lbl']} {'≤' if arah == 'maks' else '≥'} "
                         f"{KRIT[k]['fmt'](v)}[/] — "
                         + ", ".join(t for _, t in sorted(kelompok[k], key=lambda x: x[0])))
        for k in sorted(mustahil, key=lambda x: -len(mustahil[x])):
            baris.append(f"· [red]{KRIT[k]['lbl']} di luar nalar[/] — "
                         + ", ".join(t for _, t in sorted(mustahil[k], key=lambda x: x[0]))
                         + f" (pita wajar {KRIT[k]['fmt'](KRIT[k]['wajar'][0])}–"
                         + f"{KRIT[k]['fmt'](KRIT[k]['wajar'][1])})")
        for k in sorted(buta, key=lambda x: -len(buta[x])):
            baris.append(f"· [grey62]{KRIT[k]['lbl']} tidak dikirim sumber[/] — "
                         + ", ".join(sorted(buta[k])))
        ui.info_panel(f"KENAPA {len(tolak)} EMITEN TIDAK LOLOS", baris + [
            "Satu emiten bisa muncul di beberapa baris: semua kriteria yang tidak dipenuhi "
            "disebut, bukan hanya yang pertama. Baris abu-abu berarti angkanya memang tidak ada di "
            "sumber — itu bukan nol dan bukan jelek, itu belum diketahui. Baris merah berarti "
            "angkanya ada tapi tidak masuk akal — untuk PBV biasanya karena laporan emiten itu "
            "bermata uang USD sementara harganya rupiah (dijelaskan `idx.py profil <KODE>`); keduanya "
            "sama-sama tidak pernah dihitung lolos."], "red")

    umur = max((time.time() - (recs.get(c) or {}).get("ts", time.time())) / 3600 for c in uni)
    # Kriteria yang dipakai menyaring tapi tidak tampil di tabel tetap dijelaskan dasar angkanya,
    # supaya batas yang ditolak bisa dilacak ke field sumbernya.
    tak_tampil = [k for k, _ in urutkan(batas) if k not in
                  ("per", "pbv", "roe", "div", "kap", "likuid", "lot", "skor")]
    ui.info_panel("CARA BACA & BATASAN", [
        "· Satuan: PER/PBV = kelipatan · ROE/DIV/payout/D-E/REV/LABA/UPSIDE = persen · KAP dan "
        "LIKUID = miliar Rp · 1 LOT = harga × 100 lembar. Batas yang Anda ketik dibandingkan "
        "dengan angka yang tampil di tabel, tidak dengan pecahan mentah sumber.",
        "· Dasar kolom: PER = trailingPE · PBV = priceToBook · ROE = returnOnEquity · "
        "DIV = dividendYield · KAP = marketCap · LIKUID = averageVolume × harga · "
        "1 LOT = harga × 100 lembar · SKOR = skor akhir `compute_scan` (mode `scan`).",
    ] + [f"· Dasar [bold]{KRIT[k]['lbl']}[/]: {KRIT[k]['sumber']}." for k in tak_tampil] + [
        "· Likuiditas = rata-rata volume × harga cache, bukan nilai transaksi resmi IDX. Pakai "
        "sebagai batas kasar: posisi yang nilainya mendekati LIKUID/HARI sulit ditutup dalam "
        "satu sesi.",
        "· [bold]PER kosong[/] umumnya berarti emiten rugi — alasan untuk memeriksa, bukan angka "
        "untuk dibandingkan. Emiten tanpa angka tidak pernah dihitung lolos, dan disebut terpisah "
        "di panel penolakan.",
        "· [red]![/] di sebelah angka = nilainya di luar pita wajar sumber, biasanya karena laporan "
        "emiten tersebut datang dengan skala/mata uang yang tidak sebanding dengan harga rupiah "
        "(kasus nyata: PGAS bookValue 0,117 vs harga 1.190 → PBV 10.170,94, dan `profil` ikut "
        "menurunkan EPS 0 darinya). Angka begitu tidak dinilai dan tidak lolos — buka neracanya "
        "lewat [bold]profil[/] sebelum dipakai.",
        "· `debtToEquity` kosong untuk bank (utangnya dana nasabah) dan margin bank tidak "
        "sebanding dengan manufaktur; ROE masih bisa dibandingkan, margin antar sektor jangan. "
        "ROE di atas 100% umumnya berarti ekuitasnya sudah diratakan dividen (UNVR 133%), bukan "
        "manajemen yang luar biasa — cek neracanya.",
        f"· Harga, kapitalisasi, dan target adalah kutipan cache berumur ±{umur:.0f} jam, bukan "
        "±1 menit seperti di `check`. Nilai 1 LOT untuk keputusan ukuran posisi diambil dari "
        "[bold]idx.py check <KODE>[/], bukan dari tabel ini.",
        "· Semua angka dari Yahoo untuk emiten .JK dan belum diverifikasi silang ke IDX "
        "(seluruh endpoint idx.co.id menjawab 403). Cakupan analis .JK sebagian besar lembaga "
        "global, jadi UPSIDE bisa tidak memasukkan aksi korporasi lokal.",
        ("· Skor di sini = skor AKHIR mode `scan` (konfluensi walk-forward), dihitung pada universe "
         "penuh supaya angkanya identik — pada saat yang sama. Penyesuaian pasar & berita punya "
         "cache sendiri (±30 mnt), jadi dua jalan yang berjeda lebih dari itu bisa berbeda ±0,x "
         "pada kolom SKOR; mentah teknisnya tidak bergerak." if with_score else
         "· Skor TIDAK dihitung (--tanpa-skor): hanya metrik fundamental yang dinilai, jadi tidak "
         "ada yang bisa dibandingkan dengan `scan` pada jalan ini."),
        "· Saringan tidak mengubah skor dan bukan rekomendasi beli: ia hanya memperkecil daftar "
        "yang layak dibuka satu per satu.",
    ], "magenta")

    ui.info_panel("CONTOH LAIN", [
        "· Preset: [bold]idx.py screen preset=value[/] · [bold]preset=deviden[/] · "
        f"[bold]preset=tumbuh[/] · [bold]preset=sehat[/] · [bold]preset=terjangkau[/] (lot ≤ "
        f"{cfg['capital'] * cfg['max_pos_pct'] / 100:,.0f} untuk modal {cfg['capital']:,.0f} + "
        "likuiditas ≥ 1 M/hari).",
        "· Batas sendiri: [bold]idx.py screen per_maks=15 pbv_maks=3 roe_min=12 likuid_min=5[/] — "
        "arah default mengikuti mana yang baik, jadi `per=15` sama dengan `per_maks=15`, dan "
        "`div=4` sama dengan `div_min=4`.",
        "· [bold]--tanpa-skor[/] untuk saringan fundamental saja (satu permintaan per emiten, tanpa "
        "mengunduh bar harian 40 saham).",
        "· Hanya emiten tertentu: [bold]idx.py screen BBCA TLKM AMRT div_min=4[/] · urutkan "
        "[bold]urut=roe[/] · paksa unduh ulang [bold]--refresh[/].",
        "· Kriteria yang ada (satuan, mana yang dianggap baik): " + ", ".join(
            f"{k} — {KRIT[k]['satuan']}, "
            + ("lebih kecil bagus" if KRIT[k]["baik"] == "kecil" else "lebih besar bagus")
            for k in KRIT) + ".",
    ], "cyan")

    if tanpa_data:
        ui.info_panel("EMITEN TANPA DATA", [
            f"· [bold]{c}[/] — {why}" for c, why in tanpa_data] + [
            "Tidak ikut dinilai — bukan nilai nol, bukan pula tertolak oleh saringan."], "red")

    ui.info_panel("LANGKAH BERIKUT", [
        "· [bold]idx.py check <KODE>[/] — rencana entry/stop/target + ukuran lot dari kutipan live.",
        "· [bold]idx.py profil <KODE>[/] — laporan keuangan & rasio yang bisa diaudit ulang.",
        "· [bold]idx.py kalender <KODE>[/] — tanggal rilis laporan & ex-dividen yang menyergap posisi.",
        "· [bold]idx.py scan[/] — peringkat konfluensi murni; kolom SKOR di sini angka yang sama "
        "dengan tabel itu (dihitung dari fungsi dan universe yang sama, pada saat yang sama)."], "cyan")
