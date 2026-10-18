"""Profil perusahaan & fundamental emiten IDX — konteks di luar pergerakan harga.

Dasar keputusan alat ini tetap skor teknikal walk-forward (harga + volume). Modul ini sengaja
TIDAK menyentuh skor itu: ia menambahkan fakta yang tidak terlihat di candlestick — laporan
keuangan, rasio, dividen, aksi korporasi, susunan kepemilikan, dan berita bertema
proyek/rencana — plus penanda risiko yang bisa dibaca dalam sepuluh detik.

Sumber yang benar-benar gratis dan sudah diuji nyata (18-10-2026):
  · Yahoo quoteSummary + timeseries (lewat yfinance) untuk ticker .JK: laporan keuangan 4-5
    periode, rasio kunci, histori dividen/split, kalender rilis, rekomendasi & target analis.
  · Google News RSS untuk hal yang tidak punya field terstruktur (proyek, capex, right issue,
    pengendali). Ini judul berita, BUKAN data resmi.
Yang TIDAK bisa diambil (sudah diuji, bukan asumsi):
  · Struktur kepemilikan resmi + beneficial owner: hanya ada di IDX, dan seluruh endpoint
    idx.co.id menjawab 403 Cloudflare. Nama pemegang jadi cuma terbaca dari judul berita.
  · Backlog/paparan publik: PDF per emiten, tidak generik.
  · Beta Yahoo untuk .JK kacau (BBCA −0,017) — tidak pernah ditampilkan.
  · Sebagian emiten (energi/logam/BUMN besar: PGAS, ITMG, MDKA, ADRO, MEDC, AMMN) mengirim LAPORAN
    KEUANGAN dalam USD sementara harganya rupiah. Yahoo menormalkan harga, EPS dan PER ke rupiah
    bursa, tapi mengirim laporan keuangan, bookValue, totalCash/Debt dan ebitda dalam mata uang
    laporan (field financialCurrency). Jadi rasio yang memakai buku/lembar — priceToBook — jadi
    ribuan kali terlalu besar dan kolom uang di tabel bukan rupiah. Ditangani `skala()`: labelnya
    yang diikuti, angkanya tidak pernah dikonversi.
"""

import datetime as dt
import json
import os
import re
import time

import numpy as np
import pandas as pd
import yfinance as yf

import stocknews
import ui

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "profil")

INFO_KEYS = ["longName", "sector", "industry", "website", "longBusinessSummary", "employees",
             "marketCap", "sharesOutstanding", "floatShares", "heldPercentInsiders",
             "heldPercentInstitutions", "totalRevenue", "revenueGrowth", "earningsGrowth",
             "grossMargins", "operatingMargins", "profitMargins", "returnOnEquity",
             "returnOnAssets", "debtToEquity", "currentRatio", "quickRatio", "totalCash",
             "totalDebt", "freeCashflow", "operatingCashflow", "ebitda", "ebitdaMargins",
             "trailingPE", "forwardPE", "pegRatio", "priceToBook", "bookValue",
             "dividendYield", "trailingAnnualDividendYield", "dividendRate", "payoutRatio",
             "fiveYearAvgDividendYield", "targetMeanPrice", "targetMedianPrice",
             "targetHighPrice", "targetLowPrice", "numberOfAnalystOpinions", "recommendationKey",
             "recommendationMean", "epsTrailingTwelveMonths", "regularMarketPrice",
             "previousClose", "lastSplitFactor", "lastSplitDate", "lastDividendDate",
             "financialCurrency", "currency"]

INC_ROWS = ["Total Revenue", "Operating Income", "Net Income", "Diluted EPS", "Gross Profit"]
BS_ROWS = ["Stockholders Equity", "Total Debt", "Net Debt", "Cash And Cash Equivalents",
           "Ordinary Shares Number", "Total Assets"]
CF_ROWS = ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities",
           "Cash Flowsfromusedin Operating Activities Direct", "Capital Expenditure",
           "Free Cash Flow", "Dividends Paid", "Issuance Of Capital Stock",
           "Repurchase Of Capital Stock"]

# rasio arus kas/likuiditas tidak bermakna untuk bank: "utang" bank adalah dana nasabahnya
SEKTOR_BANK = ("Financial Services",)

# tema berita yang dikejar untuk menjawab "sedang mengerjakan apa perusahaan ini"
TEMAS = [
    ("Proyek & belanja modal", ' "{c}" ("belanja modal" OR capex OR pabrik OR kapasitas OR proyek)'),
    ("Aksi korporasi", ' "{c}" ("right issue" OR "private placement" OR buyback OR akuisisi OR merger OR divestasi)'),
    ("Dividen", ' "{c}" dividen'),
    ("Kepemilikan & pengendali", ' "{c}" ("pemegang saham" OR pengendali OR "saham treasury" OR direksi)'),
    ("Rencana & target bisnis", ' "{c}" (target OR prospek OR outlook OR strategi OR rencana)'),
]


# ---------------------------------------------------------------- format

def code_of(ticker):
    return str(ticker).upper().replace(".JK", "").strip()


def num(x):
    """Float bersih: None/NaN/inf → None."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(v) else v


def rp(v, dec=2):
    """Rupiah dipendekkan, gaya angka sama dengan panel lain (titik desimal): 146.74 T."""
    v = num(v)
    if v is None:
        return "n/a"
    a = abs(v)
    if a >= 1e12:
        return f"{v / 1e12:,.{dec}f} T"
    if a >= 1e9:
        return f"{v / 1e9:,.{dec}f} M"
    if a >= 1e6:
        return f"{v / 1e6:,.{dec}f} jt"
    return f"{v:,.0f}"


def idr(v):
    """Rupiah penuh tanpa pemendekan — untuk harga/EPS per lembar."""
    v = num(v)
    return "n/a" if v is None else f"{v:,.0f}"


def idr2(v):
    """Rupiah per lembar dengan desimal hanya bila angkanya kecil (EPS kuartal emiten kecil).

    Dipakai `profil` dan `kalender` bersama-sama: dua surface tidak boleh menampilkan "123" dan
    "122,67" untuk konsensus yang sama.
    """
    v = num(v)
    return "n/a" if v is None else (f"{v:,.2f}" if abs(v) < 10 else f"{v:,.0f}")


def pc(v, dec=1, signed=True):
    """Persen dari pecahan (0,218 → +21.8%)."""
    v = num(v)
    if v is None:
        return "n/a"
    return f"{v * 100:{'+' if signed else ''}.{dec}f}%"


def pcu(v, dec=1):
    """Persen untuk field yang SATUAN-nya sudah persen (dividendYield, fiveYearAvg…, debtToEquity)."""
    v = num(v)
    return "n/a" if v is None else f"{v:.{dec}f}%"


def tanda(v, dec=1):
    """Persen berwarna: + hijau / − merah (pecahan 0..1)."""
    v = num(v)
    if v is None:
        return "[grey62]n/a[/]"
    return f"[{'green' if v >= 0 else 'red'}]{v * 100:+.{dec}f}%[/]"


def _js(x):
    """Bersihkan nilai agar bisa ditulis ke JSON (tanpa NaN, tanpa objek tanggal)."""
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return None if not np.isfinite(x) else float(x)
    if isinstance(x, (dt.date, dt.datetime, pd.Timestamp)):
        return str(pd.Timestamp(x).date())
    if isinstance(x, dict):
        return {str(k): _js(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_js(v) for v in x]
    if isinstance(x, (pd.Series, pd.DataFrame)):
        return _js(x.to_dict())
    return x


def _frame(dfx, rows):
    """DataFrame laporan → {tanggal_iso: {nama_baris: nilai}}; terbaru dulu, kolom kosong dibuang."""
    out = {}
    if dfx is None or not hasattr(dfx, "columns") or dfx.empty:
        return out
    for col in dfx.columns:
        d = {}
        for r in rows:
            if r not in dfx.index:
                continue
            v = dfx.loc[r, col]
            d[r] = num(v.iloc[0] if isinstance(v, pd.Series) else v)
        if any(x is not None for x in d.values()):
            out[str(pd.Timestamp(col).date())] = d
    return dict(sorted(out.items(), reverse=True))


def _same_year(d, key):
    """Cari kolom dengan tahun yang sama (tanggal pelaporan tiap laporan bisa beda sehari)."""
    if key in d:
        return d[key]
    for k, v in d.items():
        if k[:4] == key[:4]:
            return v
    return {}


# ---------------------------------------------------------------- ambil data

def fetch(ticker, force=False, ttl_h=12.0):
    """Ambil profil lengkap satu emiten, simpan ke disk. Cache default 12 jam."""
    code = code_of(ticker)
    path = os.path.join(DIR, code + ".json")
    if not force and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                c = json.load(fh)
            if time.time() - c.get("ts", 0) < ttl_h * 3600:
                c["cached"] = True
                c.setdefault("file", path)
                return c
        except (OSError, ValueError):
            pass

    p = {"code": code, "sym": code + ".JK", "ts": time.time(), "cached": False, "err": {}, "file": path}
    tk = yf.Ticker(p["sym"])

    def grab(label, fn, default):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            p["err"][label] = f"{type(e).__name__}: {str(e)[:70]}"
            return default

    info = grab("info", lambda: tk.info, {}) or {}
    p["info"] = {k: _js(info.get(k)) for k in INFO_KEYS}
    p["income"] = grab("income", lambda: _frame(tk.income_stmt, INC_ROWS), {})
    p["balance"] = grab("balance", lambda: _frame(tk.balance_sheet, BS_ROWS), {})
    p["cash"] = grab("cash", lambda: _frame(tk.cashflow, CF_ROWS), {})
    p["dividends"] = grab("dividends", lambda: {str(pd.Timestamp(k).date()): num(v)
                                                for k, v in tk.dividends.items()}, {})
    p["splits"] = grab("splits", lambda: {str(pd.Timestamp(k).date()): num(v)
                                          for k, v in tk.splits.items()}, {})
    p["holders"] = grab("holders", lambda: _js(tk.major_holders.to_dict().get("Value", {})), {})
    p["inst_holders"] = grab("inst_holders", lambda: _js(tk.institutional_holders.to_dict("records")), [])
    p["recs"] = grab("recs", lambda: _js(tk.recommendations.to_dict("records")), [])
    cal = grab("calendar", lambda: _js(tk.calendar), {})
    p["calendar"] = cal if isinstance(cal, dict) else {}

    os.makedirs(DIR, exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(p, fh, indent=1, ensure_ascii=False)
    except OSError:
        p["err"]["cache"] = "gagal menulis cache profil"
    return p


def cache_age(p):
    """Umur data dalam jam (0-an = baru diambil pada run ini)."""
    return max(0.0, (time.time() - p.get("ts", time.time())) / 3600.0)


# ---------------------------------------------------------------- hitungan

def _pick(d, *names):
    """Nilai baris pertama yang ada — nama baris Yahoo tidak konsisten antar emiten
    ('Operating Cash Flow' vs 'Cash Flowsfromusedin Operating Activities Direct')."""
    for n in names:
        if n in d and num(d.get(n)) is not None:
            return num(d[n])
    return None


def _years(inc, bs, cf):
    """Satu baris per tahun buku, digabung dari tiga laporan (tidak bergantung urutan kolom)."""
    keys = list(inc) or list(cf) or list(bs)
    out = []
    for k in keys:
        i, b, c = inc.get(k, {}), _same_year(bs, k), _same_year(cf, k)
        out.append({"y": k, "rev": num(i.get("Total Revenue")), "oi": num(i.get("Operating Income")),
                    "ni": num(i.get("Net Income")), "eps": num(i.get("Diluted EPS")),
                    "ocf": _pick(c, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities",
                                 "Cash Flowsfromusedin Operating Activities Direct"),
                    "capex": _pick(c, "Capital Expenditure"),
                    "fcf": _pick(c, "Free Cash Flow"), "divpay": _pick(c, "Dividends Paid"),
                    "terbit": _pick(c, "Issuance Of Capital Stock"),
                    "buyback": _pick(c, "Repurchase Of Capital Stock"),
                    "eq": num(b.get("Stockholders Equity")), "netdebt": num(b.get("Net Debt")),
                    "shares": num(b.get("Ordinary Shares Number"))})
    return out


def _series(rows, key):
    return [r.get(key) for r in rows]


def _yoy(vals):
    """Pertumbuhan terhadap periode berikutnya (daftar diurut terbaru dulu)."""
    out = []
    for i in range(len(vals)):
        a, b = vals[i], vals[i + 1] if i + 1 < len(vals) else None
        out.append(a / b - 1 if (a and b) else None)
    return out


def _skala(info, ni):
    """Apakah angka laporan keuangan datang dalam mata uang lain dari harga rupiah.

    Yahoo menormalkan harga, EPS dan PER ke mata uang bursa (IDR), tapi mengirim laporan
    keuangan plus `bookValue`/`totalCash`/`ebitda` dalam `financialCurrency` emiten — untuk
    banyak nama energi/logam itu USD. Akibatnya `priceToBook` = harga rupiah dibagi buku dolar
    (PGAS 10.170,9 'kalipatan') dan tabel keuangan bukan rupiah. Angka TIDAK dikonversi:
    faktor turunan mencampur trailing-12-bulan dengan setahun buku, jadi yang boleh berubah
    hanyalah labelnya. Cache lama tidak menyimpan financialCurrency → jalur turunan.
    """
    fc = str(info.get("financialCurrency") or "").strip().upper()
    if fc:
        return {"uji": True, "pasti": True, "bed": fc != "IDR", "mata": None if fc == "IDR" else fc,
                "faktor": None, "cara": f"sumber mengirim financialCurrency = {fc}"}
    cap, per, p2b = num(info.get("marketCap")), num(info.get("trailingPE")), num(info.get("priceToBook"))
    fakt = (cap / per) / ni if (cap and per and per > 0 and ni and ni > 0) else None
    ganjil_f = fakt is not None and not 0.25 <= fakt <= 4.0
    ganjil_p = p2b is not None and not 0.02 <= p2b <= 100.0
    if ganjil_f or (fakt is None and ganjil_p):
        return {"uji": True, "pasti": False, "bed": True, "mata": None, "faktor": fakt,
                "cara": ((f"marketCap ÷ PER ÷ laba FY = {fakt:,.1f}×" if fakt is not None else
                          "laba/PER tak cukup untuk diuji")
                         + f" dan PBV sumber {f'{p2b:,.2f}' if p2b is not None else 'n/a'} "
                         "— di luar pita wajar rupiah; field financialCurrency tidak dikirim sumber")}
    if fakt is None and p2b is None:
        return {"uji": False, "pasti": False, "bed": False, "mata": None, "faktor": None,
                "cara": "tidak bisa diuji (sumber tak mengirim PER maupun PBV)"}
    return {"uji": True, "pasti": False, "bed": False, "mata": None, "faktor": fakt,
            "cara": (f"marketCap ÷ PER ÷ laba FY = {fakt:,.2f}×" if fakt
                     else f"PBV sumber {p2b:,.2f}") + " — masuk akal sebagai rupiah"}


def facts(p):
    """Semua angka turunan yang dipakai panel & penanda — dihitung sekali, dibaca di mana-mana."""
    info = p.get("info") or {}
    rows = _years(p.get("income") or {}, p.get("balance") or {}, p.get("cash") or {})
    sek = str(info.get("sector") or "")
    px = num(info.get("regularMarketPrice")) or num(info.get("previousClose"))

    rev, ni = _series(rows, "rev"), _series(rows, "ni")
    fcf = _series(rows, "fcf")
    newest = rows[0] if rows else {}
    oldest_shares = next((r["shares"] for r in reversed(rows) if r.get("shares")), None)

    dv = p.get("dividends") or {}
    batas = str(dt.date.today() - dt.timedelta(days=365))
    last12 = sum(v for k, v in dv.items() if num(v) and k >= batas)

    return {
        "rows": rows, "years": [r["y"] for r in rows], "px": px, "sector": sek,
        "bank": sek in SEKTOR_BANK,
        "rev": rev, "ni": ni, "eps": _series(rows, "eps"),
        "skala": _skala(info, next((v for v in ni if v is not None), None)),
        "rev_yoy": _yoy(rev), "ni_yoy": _yoy(ni),
        "margin": [None if not (r and n) else n / r for r, n in zip(rev, ni)],
        "capex": _series(rows, "capex"), "ocf": _series(rows, "ocf"), "fcf": fcf,
        "equity": num(newest.get("eq")), "netdebt": num(newest.get("netdebt")),
        "shares": num(info.get("sharesOutstanding")) or num(newest.get("shares")),
        "shares_bs_ago": oldest_shares,
        "float": num(info.get("floatShares")),
        "book_val": num(info.get("bookValue")),
        "ebitda": num(info.get("ebitda")),
        "nd_ebitda": None,
        "dilusi": None,
        "div_last12_dps": last12 or None,
        "div_yield": (last12 / px * 100) if px and last12 else None,
        "div_years": _div_per_year(dv),
        "insiders": num(info.get("heldPercentInsiders")),
        "institusi": num(info.get("heldPercentInstitutions")),
        "n_institusi": num((p.get("holders") or {}).get("institutionsCount")),
        "rec_row": _rec0(p.get("recs") or []),
        "earn_date": _first(p.get("calendar", {}).get("Earnings Date")),
        "exdiv": _first([p.get("calendar", {}).get("Ex-Dividend Date")]),
        "est_eps": num(p.get("calendar", {}).get("Earnings Average")),
        "est_rev": num(p.get("calendar", {}).get("Revenue Average")),
        "fcf_minus": sum(1 for v in fcf if v is not None and v < 0),
        "laba_turun": _run_down(ni),
        "err": p.get("err") or {},
    }


def lab(f):
    """Label mata uang untuk angka yang asalnya dari laporan keuangan — 'Rp' hanya bila memang rupiah.

    '≠' dipakai kalau sumber tidak mengirim field mata uang tapi pemeriksaan turunannya bilang
    angkanya bukan rupiah; begitu labelnya ≠, penjelas ada di penanda & caption tabel.
    """
    s = f["skala"]
    return "Rp" if not s["bed"] else (s["mata"] or "≠")


def duit(f, v, dec=2):
    """Uang dari laporan: label basis ikut pemeriksaan skala, angkanya tidak pernah dikonversi."""
    return "n/a" if v is None else f"{lab(f)} {rp(v, dec)}"


def lembar(f, v):
    """Uang per lembar yang asalnya dari laporan (EPS, nilai buku/lembar) — desimal ikut basis."""
    return "n/a" if v is None else f"{lab(f)} {idr2(v)}"


def _div_per_year(dv):
    out = {}
    for k, v in (dv or {}).items():
        if num(v):
            out[k[:4]] = out.get(k[:4], 0.0) + float(v)
    return dict(sorted(out.items(), reverse=True)[:5])


def _rec0(recs):
    for r in recs or []:
        if str(r.get("period")) in ("0m", "1m"):
            return r
    return {}


def _first(x):
    if isinstance(x, list):
        return x[0] if x else None
    return x


def _run_down(ni):
    """Berapa tahun beruntun laba terakhir menurun (0 = tidak)."""
    n = 0
    for i in range(min(len(ni) - 1, 3)):
        a, b = ni[i], ni[i + 1]
        if a and b and a < b:
            n += 1
        else:
            break
    return n


def derived(f):
    """Dua angka yang butuh daftar lengkap: leverage & dilusi lembar."""
    if f["netdebt"] is not None and f["ebitda"]:
        f["nd_ebitda"] = f["netdebt"] / f["ebitda"]
    baru = next((r["shares"] for r in f["rows"] if r.get("shares")), None)
    if baru and f["shares_bs_ago"]:
        f["dilusi"] = baru / f["shares_bs_ago"] - 1


# ---------------------------------------------------------------- penanda

def flags(p, f, cfg=None, temas=None):
    """Daftar (level, teks) — level merah/kuning/hijau/biru. Setiap baris menyebut angkanya.

    Ini penyaring perhatian, bukan kesimpulan: tidak ada satu pun baris di bawah yang punya
    bukti historis seperti skor teknikal.
    """
    derived(f)
    info = p.get("info") or {}
    out = []

    sk = f["skala"]
    if sk["bed"]:
        mata = sk["mata"] or "bukan rupiah (mata uangnya tidak dikirim sumber)"
        out.append(("yellow",
                    f"DUA MATA UANG DI HALAMAN INI: laporan keuangan {mata} sementara harga rupiah — "
                    "tabel KEUANGAN, nilai buku/lembar, kas/utang & EBITDA ikut mata uang laporan, "
                    "jadi tidak bisa dibandingkan langsung dengan harga; PER, ROE dan margin tetap "
                    f"sebanding karena EPS dinormalkan ke rupiah. Pemeriksaan: {sk['cara']}."))
    elif not sk["uji"]:
        out.append(("cyan", f"skala laporan tidak teruji ({sk['cara']}) — kalau EPS di tabel jauh "
                            "lebih kecil dari harga per lembar, angka itu bukan rupiah"))

    if f["earn_date"]:
        try:
            # satu definisi "jarak hari bursa" untuk seluruh aplikasi: kalender.py yang punya,
            # supaya penanda di profil tidak pernah berbeda angka dengan mode `kalender`
            import kalender as kal
            d = dt.date.fromisoformat(str(f["earn_date"])[:10])
            hari = kal.busday(d)
            if 0 <= hari <= 10:
                out.append(("red" if hari <= 3 else "yellow",
                            f"rilis laporan {d:%d-%m-%Y} ("
                            + ("hari ini" if hari == 0 else f"{hari} sesi lagi")
                            + ") — stop Anda bisa tertendang oleh satu gap pembukaan"))
            elif -10 < hari < 0:
                out.append(("cyan", f"laporan baru dirilis {-hari} sesi lalu — pastikan angka panel "
                                    "KEUANGAN sudah versi rilis itu (Yahoo bisa terlambat merevisi)"))
        except (TypeError, ValueError):
            pass

    if f["laba_turun"] >= 2:
        out.append(("red", f"laba bersih turun {f['laba_turun']} tahun beruntun, terakhir "
                           f"{duit(f, f['ni'][0])} ({pc(f['ni_yoy'][0])}) — ini tren bisnis, bukan tren harga"))
    elif f["laba_turun"] == 1:
        out.append(("yellow", f"laba bersih {f['years'][0][:4]} turun {pc(f['ni_yoy'][0])} menjadi "
                              f"{duit(f, f['ni'][0])}"))

    ry = f["rev_yoy"][0]
    if ry is not None and ry < 0:
        out.append(("yellow", f"pendapatan {f['years'][0][:4]} menyusut {pc(ry)} — volume atau harga jual melemah"))

    if f["bank"]:
        out.append(("cyan", "sektor bank: D/E, current ratio, arus kas bebas tidak bermakna (utang bank "
                            "= dana nasabah) — panel rasio menampilkannya sebagai tidak berlaku"))
    else:
        if f["fcf_minus"] >= 2:
            out.append(("red", f"arus kas bebas negatif {f['fcf_minus']} dari {len([x for x in f['fcf'] if x is not None])} "
                               "tahun — investasi dibiayai utang/kas, bukan hasil usaha"))
        nd = f["nd_ebitda"]
        if nd is not None and nd > 3:
            out.append(("red", f"utang bersih {nd:.1f}× EBITDA — leverage tinggi, sensitif suku bunga"))
        elif nd is not None and nd > 2:
            out.append(("yellow", f"utang bersih {nd:.1f}× EBITDA — masih wajar tapi tidak tipis"))
        elif nd is not None and nd < 0:
            out.append(("green", f"kas melebihi utang (net debt {duit(f, nd)}) — ada bantalan saat pasar "
                                 "dikoreksi"))

    pr = num(info.get("payoutRatio"))
    if pr is not None and pr > 1:
        out.append(("yellow", f"payout ratio {pr * 100:.0f}% — dividen setahun lebih besar dari laba; "
                              "dibayar dari kas/utang atau tahun labanya memang jelek"))

    if f["dilusi"] is not None and f["dilusi"] > 0.05:
        out.append(("yellow", f"lembar beredar bertambah {pc(f['dilusi'])} dalam "
                              f"{len(f['years'])} tahun (jejak right issue/ESOP) — return per lembar "
                              "terdilusi meski harga naik"))

    roe = num(info.get("returnOnEquity"))
    if roe is not None and roe > 0.15:
        out.append(("green", f"ROE {pc(roe)} — ekuitas yang dipakai perusahaan menghasilkan di atas "
                             "biasanya cost of equity"))
    elif roe is not None and roe < 0.05:
        out.append(("yellow", f"ROE {pc(roe)} — imbal hasil ekuitas tipis"))

    flt = (f["float"] / f["shares"]) if f["shares"] and f["float"] else None
    if f["insiders"] is not None and f["insiders"] > 0.5:
        txt = (f"{f['insiders'] * 100:.1f}% lembar di tangan orang dalam → yang bukan insider tinggal "
               f"{(1 - f['insiders']) * 100:.1f}% — arah harga bisa ditentukan sedikit pemain")
        if flt and f["insiders"] + flt > 1.0:
            txt += (f" (float sumber {flt * 100:.0f}% datang dari estimator lain; insider+float = "
                    f"{(f['insiders'] + flt) * 100:.1f}% > 100% → jangan dijumlahkan)")
        out.append(("cyan", txt))
    if flt is not None and flt < 0.15:
        out.append(("yellow", f"float {flt * 100:.0f}% dari lembar beredar — likuiditas sempit, keluar "
                              "posisi besar ikut menekan harga sendiri"))

    tgt, n = num(info.get("targetMeanPrice")), num(info.get("numberOfAnalystOpinions"))
    if tgt and f["px"]:
        up = tgt / f["px"] - 1
        col = "green" if up > 0.2 else "yellow" if up < -0.1 else "cyan"
        out.append((col, f"rerata target analis {idr(tgt)} = {tanda(up)} dari harga {idr(f['px'])} "
                        f"({int(n or 0)} analis) — rata-rata target bias optimis"))

    r = f["rec_row"] or {}
    jual = int(num(r.get("sell")) or 0) + int(num(r.get("strongSell")) or 0)
    beli = int(num(r.get("buy")) or 0) + int(num(r.get("strongBuy")) or 0)
    if jual and jual >= beli:
        out.append(("yellow", f"analis: {jual} jual vs {beli} beli — jarang terjadi pada emiten yang "
                              "sedang disukai pasar"))

    seen = set()
    for t in temas or []:
        for x in t["items"]:
            if x["tags"] == ["error"] or x["title"] in seen:
                continue
            tl = x["title"].lower()
            if "right issue" in tl or "private placement" in tl:
                seen.add(x["title"])
                out.append(("yellow", f"berita aksi korporasi: {x['title'][:58]} — hak memesan efek "
                                      "baru menambah lembar; tidak ikut tebus = porsi turun"))
                break
            if "buyback" in tl:
                seen.add(x["title"])
                out.append(("green", f"berita buyback: {x['title'][:58]} — manajemen menilai harga "
                                     "masih di bawah nilainya"))
                break
    return out[:10]


# ---------------------------------------------------------------- berita bertema

def aliases(code, longname=""):
    """Kode + singkatan dari nama resmi: 'PT Bank Central Asia Tbk' → {BBCA, BCA}.

    Google News membalik kueri OR secara longgar, jadi banyak headline yang sama sekali tidak
    menyebut emitennya. Tanpa penyaring ini panel PROYEK & RENCANA akan berisi berita emiten lain.
    """
    out = {code}
    w = [x for x in re.findall(r"[A-Za-z]+", str(longname or ""))
         if x.upper() not in ("PT", "TBK", "PERSERO", "Tbk".upper(), "DAN", "THE", "INDONESIA")]
    ak = "".join(x[0].upper() for x in w)
    if len(ak) >= 3:
        out.add(ak)
    return out


def topics(ticker, days=180, limit=4, force=False, longname=""):
    """Headline per tema: proyek, aksi korporasi, dividen, pengendali, rencana — yang menyebut emiten."""
    code = code_of(ticker)
    al = aliases(code, longname)
    out = []
    for nama, q in TEMAS:
        items = stocknews.by_query(q.replace(' "{c}"', f' "{code}"').strip(), days=days,
                                   limit=limit, force=force)
        gaga = [x for x in items if x["tags"] == ["error"]]
        items = [x for x in items if any(a.lower() in x["title"].lower() for a in al)]
        out.append({"tema": nama, "items": items, "error": gaga})
    return out


# ---------------------------------------------------------------- render

def _identitas(p, f):
    info = p.get("info") or {}
    baris = [f"[bold]{ui.lit(info.get('longName') or p['code'])}[/]  ·  "
             f"{ui.lit(info.get('sector') or '?')} / {ui.lit(info.get('industry') or '?')}",
             f"Kapitalisasi pasar [bold]Rp {rp(info.get('marketCap'))}[/] · harga terakhir sumber "
             f"[bold]{idr(f['px'])}[/]"
             + (f" · lembar beredar {rp(f['shares'], 2)}" if f["shares"] else "")
             + (f" (float {f['float'] / f['shares'] * 100:.0f}%)" if f["shares"] and f["float"] else ""),
             f"Situs: {ui.lit(info.get('website') or 'n/a')}"]
    ring = ui.lit(str(info.get("longBusinessSummary") or "").strip())
    if ring:
        baris += ["", f"[grey62]Ringkasan bisnis dari Yahoo (bahasa Inggris, kalimat brosur — bukan "
                      f"hasil pembacaan kami):[/] {ring[:330]}" + ("…" if len(ring) > 330 else "")]
    return baris


def _tabel_keuangan(f):
    hdr = ["tahun", "pendapatan", "Δ", "laba bersih", "Δ", "margin", "EPS", "kas operasi", "belanja modal"]
    rows = []
    asing = f["skala"]["bed"]
    for i, y in enumerate(f["years"][:4]):
        g = lambda v: duit(f, v)
        # EPS per lembar: angka rupiah tetap bulat; angka bermata uang laporan kecil sekali sehingga
        # dua desimal membuatnya yang rugi tipis terbaca "0.00" — naik ke empat desimal di bawah 0,01
        ev = f["eps"][i]
        e = ("n/a" if ev is None else f"{ev:,.4f}" if asing and abs(ev) < 0.01
             else idr2(ev) if asing else idr(ev))
        rows.append((y[:4], g(f["rev"][i]), tanda(f["rev_yoy"][i]), g(f["ni"][i]),
                     tanda(f["ni_yoy"][i]), pc(f["margin"][i], 1, signed=False),
                     e, g(f["ocf"][i]), g(f["capex"][i])))
    return hdr, rows


def _rasio(p, f):
    info = p.get("info") or {}
    bank = f["bank"]
    out = []

    def baris(label, value, ket="", khusus_bank=False):
        if khusus_bank and bank:
            out.append(f"{label} ··· [grey62]tidak berlaku untuk bank (utang bank = dana nasabah)[/]")
            return
        out.append(f"{label} ··· [bold]{value}[/]" + (f"  [grey62]{ket}[/]" if ket else ""))

    def f1(v):
        v = num(v)
        return "n/a" if v is None else f"{v:.2f}".rstrip("0").rstrip(".") if v < 10 else f"{v:.1f}"

    eq, sh, px = f["equity"], f["shares"], f["px"]
    asing = f["skala"]["bed"]
    pbv_info, bvps = num(info.get("priceToBook")), (eq / sh) if eq and sh else None

    baris("PER trailing", f1(info.get("trailingPE")),
          (f"forward {f1(info.get('forwardPE'))}" if num(info.get("forwardPE")) else "")
          + (" · sumber menormalkan EPS ke rupiah bursa, jadi baris ini tetap sebanding" if asing else ""))
    if asing:
        baris("PBV", "[grey62]tidak sebanding[/]",
              f"nilai buku/lembar {lembar(f, info.get('bookValue'))} sementara harga {idr(px)} rupiah — "
              f"rasio {f1(pbv_info)} dari sumber adalah hasil bagi dua mata uang, bukan valuasi")
    else:
        baris("PBV", f1(pbv_info), f"nilai buku/lembar {idr(info.get('bookValue'))}")
    if bvps and px:
        if asing:
            baris("PBV dari tabel KEUANGAN", "[grey62]tidak dihitung[/]",
                  f"ekuitas {duit(f, eq)} ÷ lembar {rp(sh, 2)} = buku/lembar {lembar(f, bvps)} — "
                  "pembaginya rupiah, yang dibagi bukan; dua mata uang tidak saya satukan jadi satu angka")
        else:
            pbv_bs = px / bvps
            baris("PBV dari tabel KEUANGAN", f"{pbv_bs:.2f}",
                  f"ekuitas {duit(f, eq)} ÷ lembar {rp(sh, 2)} = buku/lembar {idr(bvps)}")
            if pbv_info and abs(pbv_bs / pbv_info - 1) > 0.05:
                out.append(f"[yellow]PERHATIAN:[/] {pbv_info:.2f} (sumber) ≠ {pbv_bs:.2f} (neraca di atas) — "
                           "Yahoo memakai basis ekuitas yang tidak sama dengan baris Stockholders Equity "
                           "tabel (biasanya di luar kepentingan minoritas, atau periode lebih tua). "
                           "[bold]Yang bisa diaudit ulang dari angka halaman ini hanyalah versi neraca[/]; "
                           "selisih >5% berarti salah satu basisnya tidak cocok dengan yang Anda kira.")
    baris("ROE", pc(info.get("returnOnEquity"), 1, signed=False), "laba / ekuitas")
    baris("ROA", pc(info.get("returnOnAssets"), 1, signed=False), "")
    baris("Margin laba bersih", pc(info.get("profitMargins"), 1, signed=False),
          "trailing 12 bulan · margin operasi "
          f"{pc(info.get('operatingMargins'), 1, signed=False)}"
          if num(info.get("operatingMargins")) else "trailing 12 bulan")
    baris("Pertumbuhan pendapatan", pc(info.get("revenueGrowth")), "trailing 12 bulan vs setahun "
          "sebelumnya — bukan angka setahun buku di tabel")
    baris("Pertumbuhan laba", pc(info.get("earningsGrowth")), "trailing 12 bulan vs setahun sebelumnya")
    de = num(info.get("debtToEquity"))
    baris("Debt / equity", f"{de:.0f}%" if de is not None else "n/a", khusus_bank=True)
    cr = num(info.get("currentRatio"))
    baris("Current ratio", f"{cr:.2f}" if cr is not None else "n/a", khusus_bank=True)
    nd = f["nd_ebitda"]
    baris("Net debt / EBITDA", f"{nd:.1f}×" if nd is not None else "n/a", khusus_bank=True)
    fcf = num(info.get("freeCashflow"))
    baris("Arus kas bebas", duit(f, fcf) if fcf is not None else "n/a", khusus_bank=True)
    baris("Kas / total utang", f"{duit(f, num(info.get('totalCash')))} / "
                               f"{duit(f, num(info.get('totalDebt')))}")
    baris("EBITDA", duit(f, num(info.get("ebitda"))) if num(info.get("ebitda")) else
          "n/a (tidak dikirim sumber; bank umumnya kosong)")
    return out


def _analis(p, f):
    info = p.get("info") or {}
    r = f["rec_row"] or {}
    out = [f"Rekomendasi [bold]{str(info.get('recommendationKey') or 'n/a').replace('_', ' ')}[/] dari "
           f"[bold]{int(num(info.get('numberOfAnalystOpinions')) or 0)}[/] analis"
           + (f" (indeks {num(info.get('recommendationMean')):.2f}; 1 = strong buy)"
              if num(info.get("recommendationMean")) else "")]
    if r:
        out.append("· " + " · ".join(
            f"{lbl} [bold]{int(num(r.get(k)) or 0)}[/]" for k, lbl in
            (("strongBuy", "strong buy"), ("buy", "buy"), ("hold", "hold"),
             ("sell", "sell"), ("strongSell", "strong sell"))))
    lo, mean, hi = (num(info.get("targetLowPrice")), num(info.get("targetMeanPrice")),
                    num(info.get("targetHighPrice")))
    if f["px"] and mean:
        out.append(f"Target harga {idr(lo)}–{idr(hi)}, rerata {idr(mean)} → terhadap harga {idr(f['px'])} "
                   f"berarti {tanda(mean / f['px'] - 1)}")
        out.append("[grey62]Yang ditutupi Yahoo untuk .JK sebagian besar lembaga global; target mereka "
                   "sering tidak memasukkan aksi korporasi lokal. Pakai sebagai rentang, bukan angka.[/]")
    if f["earn_date"]:
        kons = ""
        if f["est_eps"] or f["est_rev"]:
            kons = (f" · konsensus EPS {lembar(f, f['est_eps'])} · revenue {duit(f, f['est_rev'])}"
                    + ("  [grey62]konsensus sumber ikut mata uang laporan, bukan rupiah[/]"
                       if f["skala"]["bed"] else ""))
        out.append(f"Rilis laporan berikutnya [bold]{f['earn_date']}[/]" + kons)
    return out


def _dividen(p, f):
    out = []
    if f["div_years"]:
        out.append("Dividen per lembar per tahun: "
                   + " · ".join(f"{y} [bold]Rp {idr(v)}[/]" for y, v in sorted(f["div_years"].items())))
    if f["div_last12_dps"]:
        out.append(f"12 bulan terakhir [bold]Rp {idr(f['div_last12_dps'])}[/] per lembar = [bold]"
                   f"{f['div_yield']:.1f}%[/] dari harga {idr(f['px'])} (dihitung dari histori dividen, "
                   f"bukan angka broker) · yield rerata 5 tahun "
                   f"{pcu((p.get('info') or {}).get('fiveYearAvgDividendYield'))}")
    if f["exdiv"]:
        out.append(f"Ex-dividend di kalender: [bold]{f['exdiv']}[/]")
    sp = p.get("splits") or {}
    if sp:
        k, v = sorted(sp.items(), reverse=True)[0]
        out.append(f"Split terakhir {k} rasio [bold]1:{num(v):.0f}[/] — grafik dari tools lain yang "
                   "tidak split-adjust akan terlihat salah")
    pr = num((p.get("info") or {}).get("payoutRatio"))
    if pr is not None:
        out.append(f"Payout ratio {pr * 100:.0f}% dari laba")
    return out or ["[grey62]tidak ada catatan dividen/split dari sumber[/]"]


def _korporasi(p, f):
    out = []
    terbit = [(r["y"][:4], r["terbit"]) for r in f["rows"] if r["terbit"]]
    bb = [(r["y"][:4], r["buyback"]) for r in f["rows"] if r["buyback"]]
    if terbit:
        out.append("Penerbitan saham baru (jejak right issue/ESOP): "
                   + " · ".join(f"{y} {duit(f, v)}" for y, v in reversed(terbit)))
    if bb:
        out.append("Pembelian kembali saham (kas keluar): "
                   + " · ".join(f"{y} {duit(f, abs(v))}" for y, v in reversed(bb)))
    if not terbit and not bb:
        out.append("[grey62]tidak ada baris penerbitan/beli kembali saham di laporan arus kas "
                   f"{len(f['years'])} tahun terakhir[/]")
    if f["dilusi"] is not None:
        out.append(f"Lembar beredar {f['years'][-1][:4]} → {f['years'][0][:4]}: {tanda(f['dilusi'])} — "
                   "inilah dilusi yang nyata di angka per lembar")
    out.append("[grey62]Keterbukaan resminya (tanggal cum, harga pelaksanaan, rasio) hanya ada di IDX, "
               "dan endpoint-nya 403. Angka di atas diturunkan dari laporan keuangan: lebih lambat "
               "satu periode, tapi bukan perkiraan.[/]")
    return out


def _kepemilikan(p, f, temas):
    out = [f"Pengendalian (persentase dari sumber, bukan nama): insider [bold]"
           f"{pc(f['insiders'], 1, signed=False)}[/] · institusi [bold]{pc(f['institusi'], 1, signed=False)}[/]"
           + (f" · [bold]{int(f['n_institusi'])}[/] institusi tercatat" if f["n_institusi"] else "")]
    if f["shares"] and f["float"]:
        flt = f["float"] / f["shares"]
        out.append(f"Lembar beredar {rp(f['shares'], 2)} · yang benar-benar bebas diperdagangkan (float) "
                   f"{rp(f['float'], 2)} = {flt * 100:.1f}%")
        if f["insiders"] and f["insiders"] + flt > 1.0:
            out.append(f"[yellow]PERHATIAN:[/] insider {f['insiders'] * 100:.1f}% + float "
                       f"{flt * 100:.1f}% = [bold]{(f['insiders'] + flt) * 100:.1f}%[/] — melebihi 100%, "
                       "jadi kedua field itu dihitung dari [bold]estimator berbeda[/] (sumber berbeda, "
                       "tanggal berbeda) dan [bold]tidak boleh dijumlahkan[/]. Yang paling aman dipakai: "
                       f"sisa non-insider = 100% − insider = [bold]{(1 - f['insiders']) * 100:.1f}%[/].")
    ih = [x for x in (p.get("inst_holders") or []) if isinstance(x, dict)][:3]
    if ih:
        out.append("Daftar institusi yang dikirim sumber:")
        for x in ih:
            out.append(f"   · {ui.lit(str(x.get('Holder') or x.get('holder') or '?'))} "
                       f"{pc(num(x.get('pctHeld')), 3, signed=False)} per "
                       f"{str(x.get('Date Reported') or x.get('dateReported') or '?')[:10]}")
        out.append("[yellow]PERHATIAN:[/] daftar itu hampir selalu investor asing kecil — Yahoo tidak "
                   "mencantumkan pengendali lokal. Satu baris pun normal untuk emiten IDX. Jangan "
                   "disimpulkan sebagai struktur pemegang saham.")
    nama = [x for t in temas or [] for x in t["items"]
            if any(k in x["title"].lower() for k in ("grup", "dana", "holding", "danantara", "pengendali",
                                                     "emiten"))][:3]
    if nama:
        out.append("Nama/aksi pemegang yang [bold]terbaca di judul berita[/] (belum terverifikasi):")
        for x in nama:
            out.append(f"   · {ui.lit(x['title'][:70])} · [grey62]{str(x['date'])[:10]}[/]")
    out.append("[grey62]Struktur kepemilikan resmi + beneficial owner hanya ada di IDX/KSEI; seluruh "
               "endpoint idx.co.id menolak permintaan dari script (403 Cloudflare). Kalau nama "
               "pengendali penting, buka halaman profil emiten di browser manual.[/]")
    return out


def _tema(temas, days):
    out = []
    for t in temas or []:
        gaga = list(t.get("error") or [])
        items = [x for x in t["items"] if x["tags"] != ["error"]]
        out.append(f"[bold]{t['tema']}[/]")
        for x in gaga:
            out.append(f"   [red]{ui.lit(x['title'][:78])}[/]")
        if not items and not gaga:
            out.append(f"   [grey62]tidak ada headline yang menyebut emiten ini dalam {days} hari[/]")
            continue
        for x in items:
            b = x["bias"]
            col = "green" if b > 0 else "red" if b < 0 else "grey62"
            out.append(f"   [{col}]{b:+.1f}[/] {ui.lit(x['title'][:64])} "
                       f"[grey62]· {str(x['date'])[:10]} · {stocknews.umur_txt(x.get('age_h'))} · "
                       f"{ui.lit((x['source'] or '?')[:16])}[/]")
    return out


def _penanda(fl):
    if not fl:
        return ["[grey62]tidak ada penanda menurut aturan modul ini — bukan berarti sahamnya aman, "
                "hanya tidak ada angka yang mencolok[/]"]
    label = {"red": "[bold white on red]BAHAYA[/]", "yellow": "[bold yellow]PERHATIAN[/]",
             "green": "[bold green]PLUS[/]", "cyan": "[bold cyan]INFO[/]"}
    return [f"{label.get(lvl, '·')} {txt}" for lvl, txt in fl]


def render_profil(p, f, temas, fl, cfg):
    info = p.get("info") or {}
    umur = cache_age(p)
    fromcache = f" · dari cache {umur:.1f} jam" if p.get("cached") else ""
    ui.header(f"PROFIL {p['code']} — FUNDAMENTAL & KONTEKS",
              f"{ui.lit(info.get('longName') or p['code'])} · data per "
              f"{time.strftime('%d-%m-%Y %H:%M', time.localtime(p.get('ts', time.time())))}{fromcache} · "
              f"berita {cfg['profile_news_days']} hari · modul ini TIDAK mengubah skor teknis")
    ui.info_panel("IDENTITAS & USAHA", _identitas(p, f), "cyan")

    hdr, rows = _tabel_keuangan(f)
    if rows:
        basis = (f"mata uang laporan = {lab(f)}, BUKAN rupiah"
                 if f["skala"]["bed"] else "rupiah")
        ui.table(hdr, rows, caption=f"laporan via Yahoo — {basis}; T = triliun, M = miliar, jt = juta · "
                 "Δ = perubahan vs tahun sebelumnya · EPS per lembar"
                 + ("  ·  bank: baris kas operasi & belanja modal tidak sebanding dengan perusahaan "
                    "non-bank" if f["bank"] else ""))
    else:
        ui.info_panel("KEUANGAN", ["[red]tidak ada baris laporan keuangan yang berhasil diambil[/]"], "red")

    ui.info_panel("RASIO & KESEHATAN KEUANGAN", _rasio(p, f), "cyan")
    ui.info_panel("PENANDA — BACA SEBELUM PUTUSIN", _penanda(fl), "yellow")
    ui.info_panel("VALUASI MENURUT ANALIS", _analis(p, f), "magenta")
    ui.info_panel("DIVIDEN & SPLIT", _dividen(p, f), "green")
    ui.info_panel("AKSI KORPORASI (jejak di laporan arus kas)", _korporasi(p, f), "blue")
    ui.info_panel("PEMEGANG SAHAM", _kepemilikan(p, f, temas), "blue")
    ui.info_panel(f"PROYEK & RENCANA (judul berita {cfg['profile_news_days']} hari)",
                  _tema(temas, cfg["profile_news_days"]), "cyan")
    if f["err"]:
        ui.info_panel("BAGIAN YANG GAGAL DIAMBIL",
                      [f"· {k}: {v}" for k, v in f["err"].items()]
                      + ["[grey62]Yang gagal tidak diganti angka karangan — panel terkait "
                         "menampilkan n/a.[/]"], "red")

    sk = f["skala"]
    sk_txt = ((f"laporan emiten ini datang dalam {lab(f)} sedangkan harga dalam rupiah — kolom "
               "uang, nilai buku/lembar, PBV, kas/utang, EBITDA dan konsensus sumber ikut mata uang "
               "laporan dan TIDAK saya konversi (satu-satunya faktor yang bisa diturunkan di sini "
               "mencampur trailing-12-bulan dengan setahun buku, jadi hasil konversinya tidak bisa "
               "dipercaya). Yang tetap sebanding: PER, ROE, ROA, margin, D/E, net debt/EBITDA."
               if sk["bed"] else
               ("laporan emiten ini dikirim dalam rupiah, satu basis dengan harga." if sk["pasti"] else
                "tidak ada tanda laporan ini bukan rupiah — tapi itu [bold]perkiraan dari angka[/], "
                "bukan field mata uang dari sumber." if sk["uji"] else
                "pemeriksaan tidak conclusive.")) + f" [grey62]{sk['cara']}.[/]")

    ui.info_panel("CARA BACA & BATASAN", [
        "· [bold]Tidak ada angka di halaman ini yang masuk skor.[/] Skor tetap murni harga+volume yang "
        "sudah diuji walk-forward. Mencampurkan rasio yang belum diuji akan membuat klaim 'keyakinan = "
        "hitung-hitungan' jadi bohong.",
        "· Laporan keuangan datang dari Yahoo, BUKAN IDX. Cakupannya luas untuk .JK (diuji dari LQ45 "
        "sampai saham kecil) tapi bisa [bold]terlambat satu periode[/] atau direvisi; tanggal rilis "
        "berikutnya ada di panel VALUASI.",
        "· [bold]Skala mata uang diperiksa, tidak diasumsikan.[/] " + sk_txt,
        "· [bold]Bank & konglomerasi:[/] rasio arus kas, D/E, current ratio tidak bermakna. Beta Yahoo "
        "untuk .JK kacau (BBCA −0,017) sehingga tidak pernah ditampilkan.",
        "· [bold]Proyek/rencana/aksi korporasi[/] dibaca dari [bold]judul[/] berita lewat kata kunci. "
        "Isi artikel tidak dibaca, tidak diverifikasi, dan satu judul bisa salah kutip. Angka belanja "
        "modal di panel KEUANGAN adalah bukti yang lebih keras daripada judul.",
        "· [bold]Dua hal dari sumber yang TIDAK rekonsiliasi[/] dan tidak kami samarkan: PBV/nilai buku "
        "per lembar versi Yahoo sering beda dari `ekuitas ÷ lembar beredar` di tabel (basis ekuitas "
        "berbeda), dan persen insider + persen float bisa menjumlah di atas 100% karena dua estimator "
        "terpisah. Kalau panel mencetak PERHATIAN, yang bisa Anda audit ulang adalah versi tabel "
        "keuangan — angka sumber hanya pelengkap.",
        "· Nama pemegang saham & beneficial owner [bold]tidak tersedia[/] di jalur gratis (endpoint IDX "
        "403). Yang ada: persentase + nama yang kebetulan muncul di judul berita.",
        "· Dividen & target harga berguna sebagai [bold]konteks waktu[/] (kapan cum date, kapan rilis), "
        "bukan sebagai alasan beli.",
        "· Aturan keluar tetap teknikal: skor ≥ ambang, stop ×ATR, ukuran dari jatah risiko. Fundamental "
        "bagus tidak menyelamatkan posisi tanpa stop."], "blue")


def cmd_profil(ticker, cfg, force=False, days=None):
    code = code_of(ticker)
    with ui.wait(f"mengambil profil fundamental {code} (Yahoo, ±11 permintaan)…"):
        p = fetch(code, force=force, ttl_h=cfg["profile_ttl_hours"])
    f = facts(p)
    with ui.wait(f"mengambil headline bertema {code} (Google News RSS)…"):
        temas = topics(code, days=days or cfg["profile_news_days"],
                       limit=cfg["profile_news_limit"], force=force,
                       longname=(p.get("info") or {}).get("longName") or "")
    fl = flags(p, f, cfg, temas)
    render_profil(p, f, temas, fl, cfg)
    ui.info_panel("LANGKAH BERIKUT", [
        f"· Gabungkan dengan teknikal: [bold]idx.py check {code}[/] (skor, stop, ukuran lot) atau "
        f"[bold]idx.py minggu {code}[/] untuk horison {cfg['week_horizon']} hari.",
        f"· Cache: [bold]{p.get('file') or DIR}[/] — tarik ulang dengan "
        f"[bold]idx.py profil {code} --refresh[/].",
        "· Kalau penanda merah menunjuk ke rilis laporan yang tinggal beberapa hari, opsi termurah "
        "biasanya menunggu, bukan menebak."], "cyan")
    return p


def peek(ticker, cfg):
    """Baris pendek fundamental untuk panel mode lain — HANYA baca cache, tanpa request jaringan.

    Kalau profil belum pernah diambil, bilang begitu; jangan diam-diam mengunduh 11 endpoint.
    """
    code = code_of(ticker)
    path = os.path.join(DIR, code + ".json")
    if not os.path.exists(path):
        return [f"[grey62]profil {code} belum pernah diambil — jalankan [bold]idx.py profil {code}[/] "
                "untuk keuangan, dividen, aksi korporasi & penandanya.[/]"]
    try:
        with open(path, encoding="utf-8") as fh:
            p = json.load(fh)
    except (OSError, ValueError) as e:  # noqa: BLE001
        return [f"[yellow]cache profil {code} tidak terbaca ({type(e).__name__})[/]"]
    f = facts(p)
    derived(f)
    info = p.get("info") or {}
    out = []
    umur = cache_age(p)
    if umur > 24:
        out.append(f"[yellow]profil {code} berumur {umur / 24:.1f} hari[/] — ambil ulang dengan "
                   f"[bold]idx.py profil {code} --refresh[/]")
    thn = (f["years"][0][:4] if f["years"] else "?")
    p2b = num(info.get("priceToBook"))
    ringkas = [f"PER {num(info.get('trailingPE')):.1f} [grey62](12 bln)[/]"
               if num(info.get("trailingPE")) else None,
               "PBV [red]tidak sebanding[/]" if f["skala"]["bed"] else
               (f"PBV {p2b:.2f}" if p2b else None),
               f"ROE {pc(info.get('returnOnEquity'), 1, signed=False)} [grey62](12 bln)[/]"
               if num(info.get('returnOnEquity')) else None,
               f"div {f['div_yield']:.1f}% [grey62](12 bln)[/]" if f["div_yield"] else None,
               f"laba FY{thn} {tanda(f['ni_yoy'][0])}" if f["ni_yoy"][0] is not None else None]
    out.append("· fundamental: " + " · ".join(x for x in ringkas if x))
    out.append("[grey62]· dua dasar angka di baris atas berbeda: PER/ROE/div = trailing 12 bulan, "
               "laba = setahun buku (FY) — jangan dibandingkan langsung[/]")
    for lvl, txt in flags(p, f, cfg, None)[:4]:
        if lvl in ("red", "yellow"):
            out.append(f"· [{lvl}](!) {txt}[/]")
    out.append("[grey62]angka fundamental di atas tidak ikut menghitung skor[/]")
    return out
