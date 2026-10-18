"""Kalender peristiwa emiten: jadwal rilis laporan keuangan & tanggal ex-dividen.

Kenapa modul ini ada: kedua tanggal itulah yang paling sering menendang stop tanpa memberi Anda
kesempatan bereaksi — harga bergerak gap saat laporan keluar, dan harga secara teori turun
sebesar dividen pada tanggal ex-dividend. Mesin skor tidak bisa melihatnya (ia hanya membaca
harga + volume), jadi ia dipasang sebagai lapisan terpisah di atas keputusan teknis.

Sumber: dua permintaan per emiten lewat yfinance (`.calendar` lalu `.info`), dicache per kode di
`data/kalender.json` — diuji nyata 18-10-2026 pada 8 emiten:
  · `Ticker.calendar` = sumber UTAMA. Isinya `Earnings Date` (daftar, kadang kosong), 
    `Ex-Dividend Date`, `Earnings Average` + `Revenue Average` (konsensus kuartal). Ini endpoint yang
    sama dengan yang dipakai cache profil `company.py`, jadi `profil` dan `kalender` membaca tanggal
    yang identik. Catatan: kamus `calendar` di DALAM `.info` kosong untuk semua ticker .JK — yang
    bekerja adalah property `Ticker.calendar`.
  · Field epoch `.info` (`earningsTimestampStart`, `exDividendDate`) = CADANGAN bila panggilan
    kalender gagal. Cadangan ini berbahaya: untuk AMRT/PTBA/KLBF kalender bilang "tidak ada jadwal"
    sementara epoch masih menyimpan tanggal tahun lalu — setiap peristiwa hasil cadangan diberi label
    `field cadangan`, tidak pernah disamarkan sebagai jadwal.

Batasan yang selalu ditulis terang di output, bukan disembunyikan:
  · tanggal BELUM diverifikasi silang ke IDX — seluruh endpoint idx.co.id menjawab 403 Cloudflare;
  · tanggal rilis = perkiraan sumber, bukan pengumuman resmi emiten;
  · "sesi" dihitung dari hari kerja Senin–Jumat; libur nasional IDX belum dikurangkan;
  · konsensus EPS dari sumber bisa salah satuan (PGAS dikirim 0,0065) → disaring, dan yang dibuang
    dikatakan dibuang.
"""

import datetime as dt
import json
import os
import time

import numpy as np
import yfinance as yf

import company as co
import idx_analyzer as eng
import portfolio as pf
import ui

HERE = os.path.dirname(os.path.abspath(__file__))
FILE = os.path.join(HERE, "data", "kalender.json")
LOT = pf.LOT
WIB = dt.timedelta(hours=7)          # epoch Yahoo dihitung UTC; bursa & investor pakai WIB

KAL_KEYS = ["earningsTimestampStart", "earningsTimestampEnd", "exDividendDate", "lastDividendDate",
            "dividendRate", "trailingAnnualDividendYield", "payoutRatio", "regularMarketPrice",
            "epsForward", "epsTrailingTwelveMonths", "longName", "currency"]

# tanggal di luar rentang ini = sampah dari sumber (Yahoo sering mengirim -1 atau 0)
_MIN_TAHUN, _MAX_TAHUN = 2020, 2100


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


def peek(codes):
    """Isi cache saja, tanpa jaringan — untuk mode yang tidak boleh melambat (brief)."""
    d = _read()["items"]
    return {c: d[c] for c in codes if c in d}


def _one(code):
    """Dua permintaan per emiten: endpoint kalender (tanggal + konsensus) dan quote (harga, dividen).

    `Ticker.calendar` ternyata MENGISI data untuk .JK walaupun `info['calendar']` kosong — dan ia
    yang lebih bisa dipercaya: untuk AMRT/PTBA/KLBF kalender mengembalikan daftar rilis KOSONG,
    sementara `earningsTimestampStart` masih menyimpan tanggal tahun lalu. Field epoch hanya dipakai
    sebagai cadangan bila panggilan kalender gagal.
    """
    rec = {"code": code, "sym": code + ".JK", "ts": time.time(), "err": None}
    tk = yf.Ticker(rec["sym"])
    cal, info, gagal = {}, {}, []
    try:
        cal = tk.calendar or {}
    except Exception as e:  # noqa: BLE001
        gagal.append(f"calendar {type(e).__name__}")
    try:
        info = tk.info or {}
    except Exception as e:  # noqa: BLE001
        gagal.append(f"info {type(e).__name__}")
    if not cal and not info:
        rec["err"] = "sumber tidak mengembalikan data (" + ", ".join(gagal) + ")"
        return rec
    earn = co._js(cal.get("Earnings Date"))
    rec["cal_earn"] = earn[0] if isinstance(earn, list) and earn else earn
    rec["cal_exdiv"] = co._js(cal.get("Ex-Dividend Date"))
    rec["cal_eps"] = co.num(cal.get("Earnings Average"))
    rec["cal_rev"] = co.num(cal.get("Revenue Average"))
    for k in KAL_KEYS:
        rec[k] = co._js(info.get(k))
    return rec


def fetch(codes, force=False, ttl_h=24.0, progress=None):
    """Ambil kalender per kode. Cache per kode: hanya yang basi/belum ada yang diunduh."""
    d = _read()
    out, nnet = {}, 0
    for i, c in enumerate(codes, 1):
        rec = d["items"].get(c)
        segar = rec and not force and (time.time() - rec.get("ts", 0) < ttl_h * 3600)
        if not segar:
            nnet += 1
            rec = _one(c)
            d["items"][c] = rec
            _write(d)
        out[c] = rec
        if progress:
            progress(c, i, len(codes))
    return out, nnet


def age_h(rec):
    return max(0.0, (time.time() - rec.get("ts", time.time())) / 3600.0)


# ---------------------------------------------------------------- tanggal

def _date(epoch):
    """Epoch (UTC) → tanggal WIB, atau None bila kosong/di luar nalar."""
    v = co.num(epoch)
    if not v or v <= 0:
        return None
    try:
        d = (dt.datetime(1970, 1, 1) + dt.timedelta(seconds=v) + WIB).date()
    except (ValueError, OverflowError):
        return None
    return d if _MIN_TAHUN <= d.year <= _MAX_TAHUN else None


def busday(d, today=None):
    """Jumlah SESI bursa antara hari ini dan `d` (bertanda): positif = masih akan datang,
    0 = tanggal yang sama, negatif = sudah lewat.

    Sengaja menghitung pada selang (today, d] dan bukan [today, d): hari ini bisa Sabtu/Minggu,
    dan `busday_count` lama akan menyebut acara Senin sebagai "hari ini". Libur nasional IDX TIDAK
    dikurangkan — angka ini perkiraan jarak, dan tabelnya diberi catatan.
    """
    today = today or dt.date.today()
    satu = dt.timedelta(1)
    if d >= today:
        return int(np.busday_count(today + satu, d + satu))
    return -int(np.busday_count(d + satu, today + satu))


def prev_busday(d):
    """Hari bursa kerja terakhir sebelum `d` — dipakai untuk memperkirakan cum-date."""
    return np.busday_offset(d, -1, roll="backward").astype(dt.date)


def _iso(v):
    """Tanggal dari endpoint kalender (string ISO atau date) → date, atau None."""
    if not v:
        return None
    try:
        d = dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None
    return d if _MIN_TAHUN <= d.year <= _MAX_TAHUN else None


RILIS, EXDIV = "RILIS LAPORAN", "EX-DIVIDEN"


def events(code, rec, today=None):
    """Daftar peristiwa satu emiten, termasuk tanggal yang baru lewat (penting: ex-div itu
    peristiwa yang justru terjadi sebelum laporan periodenya keluar).

    Tiap peristiwa membawa `via`: 'kalender' = sumber tanggal yang sama dengan panel profil,
    'epoch' = cadangan field mentah yang untuk beberapa emiten masih berisi tanggal tahun lalu
    → selalu ditandai, tidak diam-diam disajikan sebagai jadwal.
    """
    ev = []
    px = co.num(rec.get("regularMarketPrice"))
    d = _iso(rec.get("cal_earn"))
    via = "kalender"
    if not d:
        d, via = _date(rec.get("earningsTimestampStart")), "epoch"
    if d:
        ev.append({"date": d, "kind": RILIS, "code": code, "px": px, "via": via,
                   "eps": rec.get("cal_eps"), "rev": rec.get("cal_rev")})
    dx = _iso(rec.get("cal_exdiv"))
    via = "kalender"
    if not dx:
        dx, via = _date(rec.get("exDividendDate")) or _date(rec.get("lastDividendDate")), "epoch"
    if dx:
        ev.append({"date": dx, "kind": EXDIV, "code": code, "cum": prev_busday(dx),
                   "dps": co.num(rec.get("dividendRate")), "px": px, "via": via})
    return ev


# ---------------------------------------------------------------- render

def _ket(ev):
    """Keterangan sebaris: apa yang diharapkan pasar + dari mana tanggalnya."""
    basi = "field cadangan, kemungkinan tanggal lama"
    if ev["kind"] == RILIS:
        # konsensus EPS kuartal dari Yahoo kadang salah satuan (PGAS dikirim 0,0065), jadi disaring
        # pada besar angkanya: EPS kuartal yang wajar untuk .JK selalu 0,5%–25% dari harga sahamnya
        bits = ([basi] if ev["via"] == "epoch" else []) + ["perkiraan sumber, bukan pengumuman resmi"]
        eps, px = co.num(ev["eps"]), ev["px"]
        if eps is not None and px and 0.005 * px <= abs(eps) <= 0.25 * px:
            bits.append(f"konsensus EPS kuartal {co.idr2(eps)}"
                        + (" → sumber memperkirakan rugi" if eps < 0 else ""))
            if co.num(ev["rev"]):
                bits.append(f"pendapatan {co.rp(ev['rev'])}")
        elif eps is not None and px:
            bits.append(f"konsensus EPS {eps:g} terlalu kecil untuk masuk akal → diabaikan")
        return " · ".join(bits)
    bits = [basi] if ev["via"] == "epoch" else []
    if ev["dps"] and ev["px"]:
        # dividendRate Yahoo adalah tarif setahun (bisa gabungan interim + final), jadi persennya
        # = imbal hasil tahunan, bukan potongan harga pada satu tanggal ini saja
        bits.append(f"DPS {co.idr(ev['dps'])} (tarif setahun sumber) ≈ "
                    f"{ev['dps'] / ev['px'] * 100:.1f}% dari harga")
    elif ev["dps"]:
        bits.append(f"DPS {co.idr(ev['dps'])} (tarif setahun sumber)")
    if ev["cum"]:
        bits.append(f"cum-div terakhir {ev['cum']:%d-%m}")
    return " · ".join(bits) if bits else "dividen per lembar tidak tersedia"


def _jarak(b):
    if b == 0:
        return "[bold yellow]HARI INI[/]"
    if b < 0:
        return f"[grey62]H+{-b} lewat[/]"
    return f"[bold green]H-{b}[/]" if b <= 5 else f"[green]H-{b}[/]"


_HARI = ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"]


def tgl(d):
    """Nama hari bahasa Indonesia — %a memberi 'Mon/Tue' dan jadi aneh di samping teks lainnya."""
    return f"{_HARI[d.weekday()]} {d:%d-%m-%Y}"


def cmd_kalender(cfg, tickers=None, all_universe=False, window=None, force=False):
    held = pf._load()
    dasar = [co.code_of(t) for t in (tickers or (
        (cfg["watchlist"] or eng.UNIVERSE) if all_universe else
        (cfg["watchlist"] or eng.UNIVERSE)[:cfg["top"]]))]
    # posisi terbuka selalu ikut diperiksa walau tidak ada di watchlist: kalender yang paling
    # penting justru untuk saham yang sedang Anda pegang
    codes = list(dict.fromkeys(dasar + [co.code_of(t) for t in held]))
    win = window or int(cfg.get("kal_days", 30))
    ttl = float(cfg.get("kal_ttl_hours", 24.0))
    today = dt.date.today()

    with ui.wait(f"mengambil kalender {len(codes)} emiten (Yahoo kalender+quote, cache {ttl:g} jam)…"):
        recs, nnet = fetch(codes, force=force, ttl_h=ttl,
                           progress=lambda c, i, n: ui.stage(f"kalender {c} ({i}/{n})")
                           if (i == n or i % 5 == 0) else None)

    rows, rusak, lewat, jauh = [], [], [], []
    for c in codes:
        rec = recs.get(c) or {}
        ev = events(c, rec, today)
        if rec.get("err"):
            rusak.append((c, rec["err"]))
            continue
        if not ev:
            kosong = not co.num(rec.get("regularMarketPrice"))
            rusak.append((c, "tidak ada satu pun tanggal di sumber"
                             + (" — harga pun kosong, symbol mungkin tidak dikenali" if kosong else "")))
            continue
        didepan = [e for e in ev if -win <= busday(e["date"], today) <= win]
        for e in didepan:
            rows.append((e, busday(e["date"], today), c))
        if not didepan:
            # jangan diam-diam membuang: emiten tanpa jadwal di depan harus muncul sebagai
            # "tidak diketahui", bukan sebagai baris kosong yang terlihat aman
            near = min(ev, key=lambda e: abs(busday(e["date"], today)))
            g = busday(near["date"], today)
            (jauh if g > win else lewat).append((c, near, g))
    rows.sort(key=lambda x: x[1])

    nrilis = sum(1 for e, b, c in rows if e["kind"] == RILIS and b >= 0)
    nex = sum(1 for e, b, c in rows if e["kind"] == EXDIV and b >= 0)
    ui.header("KALENDER PERISTIWA EMITEN",
              f"jendela {win} hari bursa ke depan (+{win} ke belakang) · {len(codes)} emiten · "
              f"{nnet} diunduh, {len(codes) - nnet} dari cache")

    if not rows:
        ui.info_panel("HASIL", [
            "[yellow]Tidak ada satu pun tanggal dalam jendela ini.[/] Sumbernya memang belum "
            "memiliki jadwal untuk emiten yang diperiksa — bukan berarti tidak ada peristiwa."], "yellow")
    else:
        trs = []
        for e, b, c in rows:
            pos = held.get(pf.normalize(c))
            if pos:
                isk = f"[bold cyan]DIPEGANG {pos['lots']} lot[/]"
                if e["kind"] == EXDIV and e["dps"]:
                    isk += f" [dim]≈ dividen {co.rp(e['dps'] * pos['lots'] * LOT)}[/]"
            else:
                isk = "[grey62]—[/]"
            col = "grey62" if b < 0 else ("yellow" if b <= 5 else "white")
            trs.append((f"[{col}]{tgl(e['date'])}[/]", _jarak(b), f"[bold]{c}[/]",
                        e["kind"], _ket(e), isk))
        ui.table(["TANGGAL", "H", "KODE", "PERISTIWA", "KETERANGAN", "POSISI ANDA"], trs)
        ui.info_panel("RINGKASAN", [
            f"· [bold]{nrilis}[/] perkiraan tanggal rilis laporan dan [bold]{nex}[/] tanggal "
            f"ex-dividend jatuh dalam {win} hari bursa ke depan.",
            f"· [bold]{len(rows) - nrilis - nex}[/] baris berlabel `lewat` = tanggal sumber yang "
            "sudah berlalu; dipertahankan supaya Anda tahu field itu tidak diperbarui sumber, "
            "bukan karena peristiwanya tidak pernah ada.",
        ], "cyan")

    punya = [(e, b, c) for e, b, c in rows if b >= 0 and pf.normalize(c) in held]
    if punya:
        baris = []
        for e, b, c in punya:
            pos = held[pf.normalize(c)]
            nilai = pos["lots"] * LOT * (e["px"] or pos["avg"])
            jarak = "hari ini" if b == 0 else f"{b} sesi lagi"
            if e["kind"] == EXDIV:
                isi = (f"ex-dividend {e['date']:%d-%m} — {jarak}; perkiraan dividen "
                       f"{co.rp((e['dps'] or 0) * pos['lots'] * LOT)} sebelum PPh final 10%. "
                       "Setelah tanggal ini harga kehilangan sebesar itu, jadi stop/target perlu "
                       "dibaca ulang.")
            else:
                isi = (f"rilis laporan {e['date']:%d-%m} ({jarak}) — nilai position {co.rp(nilai)} "
                       "bergerak pada satu berita, bukan pada candle biasa. Kalau tidak siap menahan "
                       "lompatannya, kecilkan sebelum tanggalnya.")
            baris.append(f"· [bold cyan]{c}[/] {pos['lots']} lot — {isi}")
        ui.info_panel("JADWAL MENYERET POSISI ANDA", baris + [
            "Jarak dihitung dalam sesi bursa, bukan hari kalender; libur nasional belum dikurangkan."],
            "yellow")

    lines = [
        "· [bold]Tanggal rilis laporan[/] diambil dari endpoint kalender Yahoo (`Ticker.calendar`) — "
        "sumber yang sama dengan penanda merah di mode `profil`, jadi keduanya tidak mungkin berbeda "
        "angka. Kamus `calendar` di dalam `.info` kosong untuk semua ticker .JK. Bila kalender tidak "
        "tahu, mesin jatuh ke field epoch `earningsTimestampStart` dan barisnya dilabeli `field "
        "cadangan`: untuk AMRT/PTBA/KLBF field itu masih menyimpan tanggal tahun lalu.",
        "· [bold]Konsensus EPS kuartal[/] dipakai hanya kalau wajar (0,5%–25% dari harga saham); "
        "yang di luar itu dibuang dan dikatakan dibuang — sumbernya kadang salah satuan.",
        "· [bold]Ex-dividen[/]: harga secara teori turun sebesar dividen pada tanggal ini. Menahan sampai "
        "cum-date berarti dividen yang Anda terima digantikan penurunan harga — plus PPh final "
        "10% atas dividen, jadi ini bukan celah gratis.",
        "· [bold]H-[/] dihitung dalam sesi kerja Senin–Jumat. Libur nasional belum dikurangkan, "
        "jadi jarak sebenarnya bisa 1–2 hari lebih jauh.",
        "· [bold]Sumber tanggal belum diverifikasi silang ke IDX[/] — seluruh endpoint idx.co.id "
        "menjawab 403. Ini lapisan peringatan, bukan dasar eksekusi.",
    ]
    if rusak or lewat or jauh:
        peg = lambda c: " [bold cyan]*(dipegang)[/]" if pf.normalize(c) in held else ""  # noqa: E731
        baris = []
        for c, e, g in sorted(jauh, key=lambda x: x[2]):
            baris.append(f"· [bold]{c}[/]{peg(c)} — {e['kind'].lower()} {tgl(e['date'])} "
                         f"({g} sesi lagi) masih di luar jendela {win}; perlebar dengan "
                         f"[bold]--days {g}[/]")
        if lewat:
            baris.append(f"· [bold]Hanya punya tanggal yang sudah lewat[/] {len(lewat)} emiten — "
                         "rilis periode ini belum tercatat di sumber, jadi jadwalnya perlakukan "
                         "tidak diketahui: " + ", ".join(
                             f"{c}{peg(c)} {e['date']:%d-%m}" for c, e, g in
                             sorted(lewat, key=lambda x: -x[2])))
        for c, why in rusak:
            baris.append(f"· [bold]{c}[/]{peg(c)} — {why}")
        ui.info_panel("EMITEN YANG TIDAK PUNYA JADWAL DEPAN", baris + [
            "[grey62]* = sedang Anda pegang. Tidak munculnya tanggal di tabel BUKAN berarti tidak ada "
            "peristiwa — berarti sumber tidak punya tanggal dalam jendela ini.[/]"], "yellow")
    ui.info_panel("CARA BACA & BATASAN", lines, "magenta")

    ui.info_panel("LANGKAH BERIKUT", [
        "· [bold]idx.py kalender BBCA TLKM[/] — hanya emiten tertentu.",
        "· [bold]idx.py kalender --semua[/] — seluruh universe; dua permintaan per emiten (±80), "
        "sekalinya lalu karena cache 24 jam.",
        "· [bold]idx.py kalender --days 60[/] memperlebar jendela; [bold]--refresh[/] memaksa unduh ulang.",
        "· Peristiwa terdekat juga muncul di panel fundamental mode `check` dan `profil`."], "cyan")


def brief_events(tickers, cfg, window=None):
    """Ringkasan peristiwa untuk posisi terbuka, KHUSUS dari cache — `brief` tidak boleh menambah
    permintaan jaringan yang tidak terduga. Emiten yang belum tercacah dikatakan eksplisit."""
    win = window or int(cfg.get("kal_days", 30))
    today = dt.date.today()
    recs = peek([co.code_of(t) for t in tickers])
    out, belum, bersih = [], [], []
    for t in tickers:
        c = co.code_of(t)
        rec = recs.get(c)
        if rec is None:
            belum.append(c)
            continue
        ada = False
        for e in events(c, rec, today):
            b = busday(e["date"], today)
            if 0 <= b <= win:
                what = "rilis laporan" if e["kind"] == RILIS else "ex-dividen"
                out.append(f"· [bold]{c}[/] {what} {tgl(e['date'])} — "
                           + ("[bold yellow]hari ini[/]" if b == 0 else f"{b} sesi lagi"))
                ada = True
        if not ada:
            bersih.append(c)
    if bersih:
        # "tidak ada jadwal" harus dikatakan, bukan diam — kalau tidak, panel kosong terbaca
        # sebagai "belum diperiksa"
        out.append(f"· [green]Tidak ada jadwal dalam {win} sesi[/] untuk {', '.join(bersih)} — "
                   "sumber memang tidak punya tanggalnya, bukan belum diperiksa")
    if belum:
        out.append(f"· {', '.join(belum)}: [yellow]belum ada di cache kalender[/] — jalankan "
                   f"[bold]idx.py kalender[/] sekali agar tanggalnya ikut dipantau di brief")
    return out


def event_note(ticker, cfg, window=None):
    """Baris singkat untuk mode lain (check/profil): peristiwa terdekat satu emiten, dari cache saja
    kecuali memang belum ada — supaya tidak ada mode yang diam-diam menampilkan tanggal basi."""
    code = co.code_of(ticker)
    win = window or int(cfg.get("kal_days", 30))
    today = dt.date.today()
    rec = peek([code]).get(code)
    if rec is None or (age_h(rec) > float(cfg.get("kal_ttl_hours", 24.0))):
        rec = _one(code)
        d = _read()
        d["items"][code] = rec
        _write(d)
    if rec.get("err"):
        return [f"[grey62]kalender peristiwa: {rec['err']}[/]"]
    ev = events(code, rec, today)
    dekat = [e for e in ev if -1 <= busday(e["date"], today) <= win]
    if not dekat:
        if not ev:
            return [f"[grey62]kalender peristiwa: sumber tidak punya tanggal rilis maupun ex-div "
                    f"untuk {code} — perlakukan jadwalnya sebagai tidak diketahui[/]"]
        near = min(ev, key=lambda e: abs(busday(e["date"], today)))
        g = busday(near["date"], today)
        return [f"[grey62]kalender peristiwa: tidak ada jadwal dalam {win} sesi ke depan; tanggal "
                f"terdekat sumber {tgl(near['date'])} ({near['kind'].lower()}, "
                f"{f'{g} sesi lagi' if g >= 0 else f'{abs(g)} sesi lalu'}[/]"]
    out = []
    for e in sorted(dekat, key=lambda e: busday(e["date"], today)):
        b = busday(e["date"], today)
        label = "HARI INI" if b == 0 else (f"{b} sesi lagi" if b > 0 else f"{-b} sesi lalu")
        gaya = "bold yellow" if 0 <= b <= 5 else "cyan"
        out.append(f"[{gaya}]{e['kind']}[/] {tgl(e['date'])} — {label} · {_ket(e)}")
    out.append("[grey62]tanggal dari Yahoo, belum terverifikasi silang ke IDX (endpoint 403); "
               "jarak dihitung dalam sesi kerja, libur nasional belum dikurangkan[/]")
    return out
