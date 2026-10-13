"""Mesin analisa teknikal robust untuk saham/IHSG (IDX) berbasis data Yahoo Finance.

Usage:
  python idx_analyzer.py analyze <TICKER>       # konfluensi + level entry/stop/target 1 saham
  python idx_analyzer.py score   <TICKER>       # skor akurasi historis tiap metode (train/test split)
  python idx_analyzer.py scan [--top N]         # ranking konfluensi untuk daftar saham liquid
  python idx_analyzer.py market                 # panel kondisi pasar: makro global, breadth, berita/geopolitik
  python idx_analyzer.py backtest <TICKER>      # simulasi strategi long-only OOS vs buy&hold [--entry 20 --flat 0 --ts 2.5 --rebal 63 --nogate]
  python idx_analyzer.py decide <TICKER>        # KEPUTUSAN: beli/hold/jual/diam + rencana entry-stop-target + ukuran lot [--modal 1e8 --risiko 1]

UI: tabel & warna lewat `pip install rich`; tanpa rich tampilan otomatis teks polos.
Lebar saat dipipe/direkam bisa diatur dengan env IDX_UI_WIDTH (default 104).
"""

import argparse
import datetime as dt
import sys
import time
import warnings

import numpy as np
import pandas as pd
import yfinance as yf

try:
    import market_state
except ImportError:
    market_state = None

try:
    import live
except ImportError:
    live = None

try:
    import ui
except ImportError:
    import re as _re

    _TAG = _re.compile(r"\[/\]|\[/?[a-z#][a-z0-9_# ;]*\]")

    class _PlainUI:
        @staticmethod
        def progress(*_a, **_kw):
            return None

        def __getattr__(self, k):
            def _f(*a, **_kw):
                if k.endswith("_markup"):
                    return _TAG.sub("", str(a[0]))
                if k == "table":
                    for r in (list(a[0]), *a[1]):
                        print("  ".join(_TAG.sub("", str(x)) for x in r))
                    return
                if k == "header":
                    print(f"\n=== {_TAG.sub('', str(a[0]))} ===")
                    if len(a) > 1:
                        print(_TAG.sub("", str(a[1])))
                    return
                print(_TAG.sub("", "\n".join(str(x) for x in a)))
            return _f

    ui = _PlainUI()

warnings.filterwarnings("ignore", category=FutureWarning)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PERIOD = "5y"
HORIZON = 20  # hari bursa ke depan utk evaluasi akurasi
FEE_ROUNDTRIP = 0.004  # beli ~0,15% + jual ~0,25% (IDX)
TRAIN_FRAC = 0.7

# ---------------------------------------------------------------- data

def load(ticker: str) -> pd.DataFrame:
    """Bar harian dari Yahoo v8 chart.

    Sumber ini TIDAK menjanjikan real-time: bar hari ini muncul begitu sesi buka dan isinya
    bertambah sepanjang sesi (sudah dibuktikan: timestamp bar maju setiap panggilan ulang).
    Kesegaran & kecocokan bar ini diperiksa `live.py` sebelum angka dipakai memutuskan.

    Simbol selalu dipaksa ke format IDX (`.JK`) dan zona waktu bar divalidasi: "BBCA" tanpa .JK
    di Yahoo adalah ETF Amerika dengan nama yang sama (pernah masuk ke analisa tanpa ketahuan).
    """
    sym = (live.sym(ticker) if live else str(ticker).upper().replace(".JK", "") + ".JK")
    df = yf.Ticker(sym).history(period=PERIOD, interval="1d", auto_adjust=False)
    if getattr(df.index, "tz", None) is not None and len(df):
        off = df.index[0].utcoffset().total_seconds() / 3600
        if off != 7:
            raise SystemExit(f"{sym} tidak menghasilkan data bursa Jakarta (zona waktu {off:+.0f} jam) — "
                             "simbol mirip di bursa lain, ditolak.")
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df[df["Volume"].fillna(0) >= 0].copy()
    if len(df) < 300:
        raise SystemExit(f"Data {sym} kurang dari 300 bar ({len(df)}), tidak cukup untuk analisa.")
    if getattr(df.index, "tz", None) is not None:
        # Yahoo mengirim indeks UTC; konversi ke zona bursa supaya tanggal bar = tanggal sesi,
        # bukan tanggal yang bergeser 7 jam.
        try:
            df.index = df.index.tz_convert("Asia/Jakarta").tz_localize(None)
        except Exception:  # noqa: BLE001
            df.index = df.index.tz_localize(None)
    return df

# ---------------------------------------------------------------- indikator

def sma(s, n): return s.rolling(n).mean()
def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)

def stoch(df, n=14, k=3, d=3):
    lo, hi = df.Low.rolling(n).min(), df.High.rolling(n).max()
    raw = 100 * (df.Close - lo) / (hi - lo)
    ks = raw.rolling(k).mean()
    return ks, ks.rolling(d).mean()

def stoch_rsi(close, n=14, m=14):
    r = rsi(close, n)
    lo, hi = r.rolling(m).min(), r.rolling(m).max()
    return 100 * (r - lo) / (hi - lo)

def macd(close, f=12, s=26, sig=9):
    line = ema(close, f) - ema(close, s)
    return line, line.ewm(span=sig, adjust=False).mean()

def atr(df, n=14):
    tr = pd.concat([df.High - df.Low,
                    (df.High - df.Close.shift()).abs(),
                    (df.Low - df.Close.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()

def adx(df, n=14):
    up = df.High.diff()
    dn = -df.Low.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(df, n)
    pdi = 100 * pd.Series(plus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * pd.Series(minus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    return dx.ewm(alpha=1 / n, adjust=False).mean(), pdi, mdi

def bollinger(close, n=20, k=2):
    mid = sma(close, n)
    sd = close.rolling(n).std()
    return mid + k * sd, mid, mid - k * sd

def obv(df):
    sign = np.sign(df.Close.diff()).fillna(0)
    return (sign * df.Volume.fillna(0)).cumsum()

def cmf(df, n=20):
    vol = df.Volume.fillna(0)
    mult = ((df.Close - df.Low) - (df.High - df.Close)) / (df.High - df.Low).replace(0, np.nan)
    return (mult * vol).rolling(n).sum() / vol.rolling(n).sum().replace(0, np.nan)

def mfi(df, n=14):
    tp = (df.High + df.Low + df.Close) / 3
    mv = tp * df.Volume.fillna(0)
    d = mv.diff()
    pos = d.clip(lower=0).rolling(n).sum()
    neg = (-d.clip(upper=0)).rolling(n).sum()
    return 100 - 100 / (1 + pos / neg.replace(0, np.nan))

def chop(df, n=14):
    a = atr(df, n) * n
    rng = df.High.rolling(n).max() - df.Low.rolling(n).min()
    return 100 * np.log10((a / rng).clip(lower=1e-9)) / np.log10(n)

def supertrend(df, n=10, k=3):
    a = atr(df, n)
    hl2 = (df.High + df.Low) / 2
    ub, lb = hl2 + k * a, hl2 - k * a
    trend = pd.Series(1, index=df.index)
    st = pd.Series(np.nan, index=df.index)
    fub, flb = ub.copy(), lb.copy()
    for i in range(1, len(df)):
        fub.iloc[i] = ub.iloc[i] if (ub.iloc[i] < fub.iloc[i - 1] or df.Close.iloc[i - 1] > fub.iloc[i - 1]) else fub.iloc[i - 1]
        flb.iloc[i] = lb.iloc[i] if (lb.iloc[i] > flb.iloc[i - 1] or df.Close.iloc[i - 1] < flb.iloc[i - 1]) else flb.iloc[i - 1]
        if trend.iloc[i - 1] == 1:
            trend.iloc[i] = -1 if df.Close.iloc[i] < flb.iloc[i] else 1
        else:
            trend.iloc[i] = 1 if df.Close.iloc[i] > fub.iloc[i] else -1
        st.iloc[i] = flb.iloc[i] if trend.iloc[i] == 1 else fub.iloc[i]
    return trend, st

# ---------------------------------------------------------------- sinyal (per metode)
# 1 = bullish, -1 = bearish, 0 = netral. Semua dihitung tanpa data masa depan.

def build_signals(df):
    c, v = df.Close, df.Volume.fillna(0)
    r14 = rsi(c)
    ks, kd = stoch(df)
    sr = stoch_rsi(c)
    ml, ms = macd(c)
    ob, obm = obv(df), sma(obv(df), 20)
    cm = cmf(df)
    mf = mfi(df)
    adx_v, pdi, mdi = adx(df)
    st_tr, st_line = supertrend(df)
    e20, e50, e200 = ema(c, 20), ema(c, 50), ema(c, 200)
    bb_u, bb_m, bb_l = bollinger(c)
    roc = c.pct_change(12) * 100
    wr = -100 * (df.High.rolling(14).max() - c) / (df.High.rolling(14).max() - df.Low.rolling(14).min())
    vr = v / sma(v, 50)
    vwap_dev = (c - sma((df.High + df.Low + c) / 3 * 1, 20)) / sma((df.High + df.Low + c) / 3, 20) * 100
    atr_n = atr(df) / c * 100
    ch = chop(df)

    s = {}
    s["EMA20>EMA50 (tren)"] = ("tren", (e20 > e50).astype(int).where(e20.notna() & e50.notna(), 0) * 2 - 1)
    s["Close>EMA200 (tren)"] = ("tren", (c > e200).astype(int) * 2 - 1)
    s["Golden cross EMA20/50"] = ("tren", pd.Series(np.where((e20 > e50) & (e20.shift() <= e50.shift()), 1,
                                                    np.where((e20 < e50) & (e20.shift() >= e50.shift()), -1, 0)), index=c.index))
    s["MACD line vs signal"] = ("tren", np.sign((ml - ms)).fillna(0))
    s["Supertrend"] = ("tren", st_tr)
    s["ADX +DI/-DI (>=20)"] = ("tren", pd.Series(np.where((adx_v >= 20) & (pdi > mdi), 1,
                                                 np.where((adx_v >= 20) & (mdi > pdi), -1, 0)), index=c.index))
    s["RSI14 vs 50"] = ("momentum", (r14 > 50).astype(int) * 2 - 1)
    s["RSI14 oversold/overbought"] = ("momentum", pd.Series(np.where(r14 < 30, 1, np.where(r14 > 70, -1, 0)), index=c.index))
    s["Stoch %K/%D"] = ("momentum", pd.Series(np.where((ks > kd) & (ks < 80), 1, np.where((ks < kd) & (ks > 20), -1, 0)), index=c.index))
    s["StochRSI oversold/overbought"] = ("momentum", pd.Series(np.where(sr < 20, 1, np.where(sr > 80, -1, 0)), index=c.index))
    s["ROC12 > 0"] = ("momentum", np.sign(roc).fillna(0))
    s["Williams %R"] = ("momentum", pd.Series(np.where(wr > -20, -1, np.where(wr < -80, 1, 0)), index=c.index))
    s["Bollinger %B meanrev"] = ("meanrev", pd.Series(np.where(c < bb_l, 1, np.where(c > bb_u, -1, 0)), index=c.index))
    s["Deviasi dari EMA20"] = ("meanrev", pd.Series(np.where(vwap_dev < -3, 1, np.where(vwap_dev > 3, -1, 0)), index=c.index))
    s["OBV vs MA20 (volume)"] = ("volume", pd.Series(np.where(ob > obm, 1, -1), index=c.index).where(ob.notna() & (v > 0), 0))
    s["CMF20 > 0"] = ("volume", np.sign(cm).fillna(0))
    s["MFI oversold/overbought"] = ("volume", pd.Series(np.where(mf < 20, 1, np.where(mf > 80, -1, 0)), index=c.index))
    s["Volume surge (VR>1.5 searah candle)"] = ("volume", pd.Series(
        np.where(vr > 1.5, np.sign(c.diff()), 0), index=c.index).fillna(0))
    return s, {"ADX": adx_v, "CHOP": ch, "ATRn": atr_n}

# ---------------------------------------------------------------- evaluasi akurasi

def evaluate(df, horizon=None):
    H = horizon or HORIZON
    sig, reg = build_signals(df)
    fwd = df.Close.shift(-H) / df.Close - 1 - FEE_ROUNDTRIP
    n = len(df)
    test_start = int(n * TRAIN_FRAC)
    base = fwd.iloc[test_start:-H].dropna()
    base_up = float((base > 0).mean())
    rows = []
    for name, (cat, s) in sig.items():
        rec = {"metode": name, "kategori": cat}
        for split, idx_slice in (("train", slice(0, test_start)), ("test", slice(test_start, n - H))):
            ss, ff = s.iloc[idx_slice], fwd.iloc[idx_slice]
            bull, bear = (ss == 1) & ff.notna(), (ss == -1) & ff.notna()
            nb, nr = int(bull.sum()), int(bear.sum())
            hit_b = float((ff[bull] > 0).mean()) if nb else np.nan
            hit_r = float((ff[bear] < 0).mean()) if nr else np.nan
            exp_b = float(ff[bull].mean() * 100) if nb else np.nan
            exp_r = float(-ff[bear].mean() * 100) if nr else np.nan
            z = (hit_b - base_up) * np.sqrt(nb) / 0.5 if nb >= 10 else np.nan
            rec[split] = dict(cov_bull=nb / max(len(ss), 1), hit_b=hit_b, hit_r=hit_r,
                              exp_b=exp_b, exp_r=exp_r, z=z, n=nb + nr)
        rows.append(rec)
    return rows, base_up, reg, sig, fwd

def grade(hit_b, hit_r, z, n):
    if n < 20 or (np.isnan(hit_b) and np.isnan(hit_r)):
        return "D*"
    best = max(hit_b or 0, hit_r or 0)
    if best >= 0.60 and z >= 1.5:
        return "A"
    if best >= 0.55:
        return "B"
    if best >= 0.50:
        return "C"
    return "D"

# ---------------------------------------------------------------- verdict

def action_for(final, gate=True, holding=False, entry=20.0):
    """SATU sumber verdik untuk scanner, analyze dan keputusan — tidak ada dua label berbeda."""
    if final >= 35 and gate:
        return "BELI KUAT", "konfluensi kuat + rezim indeks sehat"
    if final >= entry and gate:
        return "BELI", "skor lulus ambang entry dan indeks di atas EMA200"
    if final >= entry and not gate:
        return "SIAGA (maks 1/3 lot, stop ketat)", "skor kuat TAPI IHSG di bawah EMA200 — risiko sistemik"
    if 5 <= final < entry:
        return "WATCHLIST — tunggu pullback", "skor positif tapi belum sekuat ambang entry"
    if -5 < final < 5:
        return ("HOLD" if holding else "TUNGGU"), "skor netral"
    if holding or final >= -20:
        return "KURANGI / JUAL BERTAHAP", "skor memburuk, posisi lama lebih baik dikurangi"
    return "JUAL / HINDARI", "konfluensi bearish"

def verdict(score, gate=True, entry=20.0):
    """Label pendek untuk tabel — isinya turunan langsung dari `action_for`."""
    return action_for(score, gate, False, entry)[0].split(" — ")[0]

# ---------------------------------------------------------------- output

def fmt_pct(x): return "n/a" if x is None or np.isnan(x) else f"{x*100:.1f}%"
def fmt_num(x): return "n/a" if x is None or np.isnan(x) else f"{x:.2f}"

def _sc_score(x, hi_good=True):
    if x is None or np.isnan(x):
        return "n/a"
    col = "green" if (x > 0) == hi_good else "red"
    return f"[{col}]{x:.2f}[/]"

def cmd_score(ticker):
    df = load(ticker)
    rows, base_up, *_ = evaluate(df)
    n = len(df)
    ui.header(f"SKOR AKURASI METODE — {ticker}",
              f"{n} bar harian | horizon {HORIZON} hari | train {int(n*TRAIN_FRAC)} / test OOS {n-int(n*TRAIN_FRAC)} bar | "
              f"base rate naik(test) {base_up*100:.1f}% | fee round-trip {FEE_ROUNDTRIP*100:.2f}%")
    order = sorted(rows, key=lambda r: -(r["test"]["hit_b"] or 0))
    trows = []
    for r in order:
        t = r["test"]
        g = grade(t["hit_b"], t["hit_r"], t["z"], t["n"])
        hb = _sc_score((t["hit_b"] or 0) * 100 - base_up * 100 if not np.isnan(t["hit_b"]) else np.nan)
        trows.append((r["metode"][:30], {"tren": "TR", "momentum": "MO", "meanrev": "MR", "volume": "VU"}.get(
            r["kategori"], r["kategori"][:2]), f"{t['cov_bull']*100:.0f}",
                      fmt_pct(t["hit_b"]), hb, fmt_pct(t["hit_r"]),
                      _sc_score(t["exp_r"]), fmt_num(t["z"]), ui.grade_markup(g)))
    ui.table(["METODE", "kat", "cvg%", "bull%", "vs bse", "bear%", "exp%R", "z", "nilai"],
             trows, first_w=30,
             caption="A akurat OOS · D tak lebih baik dari acak · exp%R ekspektasi return bersih/sinyal · "
                     "vs bse selisih hit rate vs acak · kat: TR tren MO momentum MR mean-revert VU volume")
    ui.info_panel("CARA BACA", [
        "· [bold]bull%[/] = persen sinyal beli yang benar naik dalam 20 hari ke depan; bandingkan dengan "
        "[bold]vs bse[/] (di atas 0 berarti lebih baik dari peluang acak).",
        "· [bold]bear%[/] = persen sinyal jual yang benar turun. [bold]exp%R[/] = rata-rata return bersih "
        "setelah fee 0,40% per sinyal — inilah ukuran uang, bukan hit rate.",
        "· [bold]z[/] = kekuatan bukti; |z| ≥ 2 baru layak dipercaya. [bold]cvg%[/] = seberapa sering metode "
        "ini memberi sinyal (rendah = contoh sedikit, hati-hati overfit).",
        "· Hanya metode dengan edge positif yang mendapat bobot di skor konfluensi — sisanya diabaikan, "
        "bukan dipaksa ikut."], "cyan")

def cmd_analyze(ticker, rebal=63, entry=20.0, ts_mult=2.5):
    """Bedah satu saham dengan skor yang SAMA persis dengan scanner dan mode keputusan."""
    d = decide(ticker, entry=entry, ts_mult=ts_mult, rebal=rebal)
    s, pxl, a = d["sn"], d["close"], d["atr"]
    final, act, why = d["final"], d["act"], d["why"]
    ui.header(f"ANALISA — {ticker}", f"{d['date']} | close {pxl:,.0f} | rezim {d['regime']} "
              f"(ADX {d['adx']:.0f}, CHOP {d['chop']:.0f})")
    ms = market_state.get_market_state(UNIVERSE) if market_state else None
    if ms:
        market_state.print_panel(ms)
    tot = s["w_total"] or 1
    sec = f" + sektor {d['sector_adj']:+.1f}" if d["sector_adj"] else ""
    ui.decision_banner(act, why, final)
    ui.info_panel("KOMPOSISI SKOR", [
        f"Skor teknis walk-forward: [{'green' if d['raw']>=0 else 'red'}]{d['raw']:+.1f}[/]   |   "
        f"penyesuaian pasar: [{'green' if d['mkt_adj']>=0 else 'red'}]{d['mkt_adj']:+.1f}[/]{sec}",
        f"[bold]Skor akhir[/] [{'green' if final>=0 else 'red'}]{final:+.1f}[/] → {ui.verdict_markup(act)}",
        "Angka ini identik dengan yang tampil di scanner dan [bold]idx.py check[/] — satu sumber, satu angka."],
        "cyan")
    rows = []
    for name, wt in sorted(s["weights"].items(), key=lambda x: -x[1]):
        v = float(np.nan_to_num(s["sig"][name][1].iloc[-1]))
        rows.append((name[:34], {"tren": "TR", "momentum": "MO", "meanrev": "MR", "volume": "VU"}.get(
            s["sig"][name][0], s["sig"][name][0][:2]), ui.signal_markup(v), f"{wt/tot*100:.1f}"))
    ui.table(["METODE", "kat", "sinyal", "bobot%"], rows, first_w=34,
             caption="hanya metode yang punya edge luar-sampel diberi bobot; sisanya tidak dihitung")
    ui.info_panel(f"LEVEL (ATR 14 = {d['atr_pct']:.1f}%/hari)", [
        f"Zona entry [green]{pxl - a:,.0f} – {pxl:,.0f}[/]   |   stop {ts_mult}×ATR [red]{d['stop']:,.0f}[/]",
        f"Target 1 (+1.5R) [green]{d['t1']:,.0f}[/]   Target 2 (+3R) [green]{d['t2']:,.0f}[/]",
        f"Ukuran posisi utk modal {d['capital']:,.0f} / risiko {d['risk_pct']}%: lihat "
        f"[bold]idx.py check {ticker}[/]."], border="magenta")
    ui.info_panel("CARA BACA", [
        "· Tiap metode memberi sinyal [green]▲ BELI[/] / [red]▼ JUAL[/] / [grey62]— netral[/]; skor adalah "
        "rata-rata berbobot akurasi luar-sampel tiap metode (dikalibrasi ulang tiap 63 bar), lalu digeser "
        "kondisi pasar & sektor.",
        "· [bold]bobot%[/] = kontribusi metode pada skor. Metode berbobot kecil tapi searah = konfirmator, "
        "bukan penentu; skor bisa positif meski banyak metode netral.",
        "· Penyesuaian pasar [bold]bukan prediktor[/], melainkan penanda tekanan (geopolitik/valas/breadth) — "
        "gunakannya untuk mengecilkan posisi, bukan untuk membalik arah sinyal teknis.",
        "· Verdict memakai ambang yang sama dgn mode keputusan: ≥ +35 BELI KUAT, ≥ +20 BELI (gate terbuka), "
        "+5…+20 WATCHLIST, −5…+5 TUNGGU/HOLD, sisanya kurangi/jual."], "cyan")
    if d["regime"] == "choppy" and abs(final) >= 15:
        ui.info_panel("CATATAN", ["[yellow]Rezim saham ini choppy — sinyal tren kurang andal, "
                                  "tunggu konfirmasi break.[/]"], "yellow")

LQ45 = ["BBCA.JK","BBRI.JK","BMRI.JK","BBNI.JK","BRIS.JK","TLKM.JK","ISAT.JK","TOWR.JK",
        "ASII.JK","UNVR.JK","ICBP.JK","INDF.JK","CPIN.JK","AMRT.JK","MYOR.JK","KLBF.JK",
        "GOTO.JK","BREN.JK","ADRO.JK","PTBA.JK","ITMG.JK","BYAN.JK","MDKA.JK","ANTM.JK",
        "PGAS.JK","MEDC.JK","AKRA.JK","JSMR.JK","TINS.JK","SMGR.JK","INKP.JK","BRMS.JK",
        "MBMA.JK","PSAB.JK","ARCI.JK","NCKL.JK","DSNG.JK","CUAN.JK","AMMN.JK","RAJA.JK"]

UNIVERSE = list(LQ45)

def compute_scan(tickers=None, ms=None, quiet=False, rebal=63, horizon=None, thresh=20.0):
    """Skor mentah + akhir tiap saham di universe — memakai `snapshot` yang sama dgn mode keputusan.

    `horizon` diisi (mis. 5 utk mode mingguan) → skor dikalibrasi utk horison itu dan tiap baris
    ikut membawa `rec`: rekam jejak sinyal serupa di saham yang sama.
    """
    tickers = tickers or UNIVERSE
    if ms is None and market_state:
        ms = market_state.get_market_state(tickers)
    adj = ms["adjust"] if ms else 0.0
    out = []
    for i, t in enumerate(tickers, 1):
        try:
            s = snapshot(t, rebal, horizon)
            sp = market_state.sector_penalty(t, ms) if ms else 0.0
            final = float(np.clip(s["raw"] + adj + sp, -100, 100))
            row = {"ticker": t, "raw": s["raw"], "final": final, "sector_adj": sp,
                   "regime": s["regime"], "close": s["close"], "date": s["date"],
                   "gate": s["gate"], "atr_pct": s["atr_pct"], "conf": s["conf"], "sn": s}
            if horizon:
                row["rec"] = track_record(s["sim"], s["c"], None, horizon, thresh)
                row["rec_gate"] = track_record(s["sim"], s["c"], s["gate_series"], horizon, thresh)
            out.append(row)
        except SystemExit:
            pass
        except Exception as e:
            print(f"  skip {t}: {e}", file=sys.stderr)
        if not quiet:
            ui.progress(f"memindai {t}", i, len(tickers))
    ui.progress(None, len(tickers), len(tickers))
    out.sort(key=lambda r: -r["final"])
    return out, ms

def render_scan(res, ms, top=15, title="SCANNER"):
    ui.header(f"{title} {len(res)} SAHAM",
              "MENTAH = konfluensi teknis · AKHIR = + pasar + sektor · fee & T+2 IDX diperhitungkan")
    trows = []
    for i, r in enumerate(res[:top], 1):
        v = verdict(r["final"], r["gate"])
        fcol = "green" if r["final"] >= 20 else "red" if r["final"] <= -5 else "grey74"
        trows.append((str(i), f"[bold]{r['ticker']}[/]", f"{r['raw']:+.1f}",
                      f"[{fcol}]{r['final']:+.1f}[/]", ui.verdict_markup(v),
                      r["regime"], f"{r['close']:,.0f}",
                      f"[yellow]sektor -{abs(r['sector_adj']):.1f}[/]" if r["sector_adj"] else ""))
    ui.table(["#", "KODE", "MENTAH", "AKHIR", "VERDIK", "REZIM", "HARGA", "catatan"], trows)
    ui.info_panel("CARA BACA", [
        "· [bold]MENTAH[/] = konfluensi teknis walk-forward saham itu; [bold]AKHIR[/] = setelah penyesuaian "
        "pasar global/politik ([cyan]+[/]) dan penalti sektor yang sedang tertekan berita.",
        "· [bold]REZIM[/] dari ADX & Choppiness: [green]trending[/] = sinyal tren lebih layak diikuti, "
        "[yellow]choppy[/] = sinyal mudah bolak-balik, [grey62]netral[/] = di antaranya.",
        "· Peringkat bukan rekomendasi beli semuanya — yang layak dilihat hanya yang [bold]AKHIR ≥ +20[/] "
        "saat [bold]gate IHSG>EMA200 terbuka[/]. Verifikasi per saham dengan [bold]decide[/].",
        "· HARGA di tabel = penutupan bar terakhir yang diunduh sumber; scan 40 saham TIDAK "
        "menyentuh kutipan live. Harga keputusan sebenarnya ada di `check`/`minggu` yang menempelkan "
        "kutipan live ±1 menit — lihat `idx.py cekdata`. Fee & settlement T+2 IDX sudah diperhitungkan dalam skor."],
        "cyan")

def cmd_scan(top):
    ms = market_state.get_market_state(UNIVERSE) if market_state else None
    if ms:
        market_state.print_panel(ms)
    res, _ = compute_scan(UNIVERSE, ms)
    render_scan(res, ms, top)

MIN_EDGE = 0.003
WARM = 210

def walkforward_scores(df, sig, rebal=63, horizon=None):
    """Skor konfluensi dgn bobot yang dikalibrasi ulang tiap `rebal` bar, hanya pakai data s/d saat itu.

    `horizon` = jumlah hari bursa ke depan yang jadi target akurasi (default HORIZON 20 hari;
    mode mingguan memakai 5).
    """
    H = horizon or HORIZON
    fwd = df.Close.shift(-H) / df.Close - 1 - FEE_ROUNDTRIP
    n = len(df)
    comp = pd.Series(np.nan, index=df.index)
    w = None
    for i in range(n):
        if w is None or i % rebal == 0:
            he = max(0, i - H)
            if he >= WARM:
                w = {}
                for name, (cat, s) in sig.items():
                    m = s.iloc[:he].to_numpy()
                    ff = fwd.iloc[:he]
                    bull, bear = ff[m == 1], ff[m == -1]
                    eb = float(bull.mean()) if len(bull) >= 15 else 0.0
                    er = float(-bear.mean()) if len(bear) >= 15 else 0.0
                    e = max(0.0, np.nan_to_num(eb), np.nan_to_num(er))
                    if e > MIN_EDGE:
                        w[name] = e
            else:
                w = None
        if w and sum(w.values()) > 0:
            v = 0.0
            for name, wt in w.items():
                s = sig[name][1].iloc[i]
                v += (0.0 if np.isnan(s) else float(s)) * wt
            comp.iloc[i] = v / sum(w.values()) * 100
    return comp, w

def index_gate():
    idx = yf.Ticker("^JKSE").history(period="5y")["Close"].dropna()
    idx.index = pd.to_datetime(idx.index).tz_localize(None)
    return idx > idx.ewm(span=200, adjust=False).mean()

def simulate_positions(sim, c, atrn, g, entry=20.0, flat=0.0, ts_mult=2.5, min_hold=5):
    p, peak, age, streak = 0, np.nan, 0, 0
    pos = []
    for i in range(len(sim)):
        v, px, a = float(sim.iloc[i]), float(c.iloc[i]), float(atrn.iloc[i])
        streak = streak + 1 if v >= entry else 0
        if p == 1:
            peak = max(peak, px)
            age += 1
            if (age >= min_hold and v <= flat) or px < peak * (1 - ts_mult * a):
                p, age = 0, 0
        if p == 0 and streak >= 2 and g[i]:
            p, peak, age = 1, px, 1
        pos.append(p)
    return pd.Series(pos, index=sim.index)

def trade_stats(pos, c):
    runs, s = [], None
    for i in range(len(pos)):
        if pos.iloc[i] and s is None:
            s = i
        if not pos.iloc[i] and s is not None:
            runs.append((s, i - 1))
            s = None
    open_run = s is not None
    if open_run:
        runs.append((s, len(pos) - 1))
    done = [(c.iloc[a_], c.iloc[b], b - a_ + 1) for a_, b in runs if not open_run or (a_, b) != runs[-1]]
    if not done:
        return {"n": 0, "win": np.nan, "median_hold": np.nan, "avg_hold": np.nan}
    wins = sum(1 for e, x, d in done if x > e)
    holds = [d for _, _, d in done]
    return {"n": len(done), "win": wins / len(done),
            "median_hold": float(np.median(holds)), "avg_hold": float(np.mean(holds))}

def snapshot(ticker, rebal=63, horizon=None, with_live=False):
    """SATU sumber angka untuk semua mode: skor walk-forward, bobot, rezim, ATR, status posisi.

    Dipakai compute_scan (scanner), decide (keputusan), portfolio (pemantauan) dan cmd_analyze,
    sehingga skor yang tampil di scanner = skor yang dipakai menghitung ukuran posisi.
    `horizon` = hari bursa ke depan yang jadi patokan akurasi bobot (default 20; mode minggu pakai 5).
    `with_live=True` → pakai kutipan real-time bila bar harian belum ada hari ini, plus hasil
    validasi silang (`live`, `cek`) di dict hasilnya.
    """
    H = horizon or HORIZON
    df = load(ticker)
    q = cek = None
    if with_live and live:
        q = live.quote(ticker)
        if not q.get("error") and q["price"] is not None and q["time"]:
            last = df.index[-1].date()
            if q["time"].date() > last and abs(q["price"] - float(df.Close.iloc[-1])) > 0.5:
                # bar harian sumber belum berisi hari ini → susuli dengan kutipan live
                cols = [c for c in ("Open", "High", "Low", "Close", "Volume", "Adj Close") if c in df.columns]
                val = {"Open": q["day_open"] or q["price"], "High": q["day_high"] or q["price"],
                       "Low": q["day_low"] or q["price"], "Close": q["price"],
                       "Volume": q["volume"] or 0, "Adj Close": q["price"]}
                new = pd.DataFrame([[val.get(c, np.nan) for c in cols]],
                                   index=[pd.Timestamp(dt.datetime.combine(q["time"].date(), dt.time(9, 0)))],
                                   columns=cols)
                df = pd.concat([df, new])
            elif q["time"].date() == last:
                # bar hari ini sudah ada tapi isinya setua unduhan terakhir — tempelkan kutipan
                # live supaya stop/target/ukuran dihitung dari harga yang benar-benar tercetak
                i = df.index[-1]
                df = df.copy()
                px = float(q["price"])
                df.loc[i, "Close"] = px
                if q.get("day_high") is not None:
                    df.loc[i, "High"] = max(float(q["day_high"]), px)
                if q.get("day_low") is not None:
                    df.loc[i, "Low"] = min(float(q["day_low"]), px)
                if q.get("volume"):
                    df.loc[i, "Volume"] = float(q["volume"])
        try:
            cek = live.crosscheck(ticker, df)
        except Exception as e:  # noqa: BLE001
            cek = ([("Bahaya", f"validasi gagal: {type(e).__name__}: {e}")], [], q)
    sig, reg = build_signals(df)
    comp, w_last = walkforward_scores(df, sig, rebal, H)
    sim = comp.dropna()
    if len(sim) < 120:
        raise SystemExit("History belum cukup (butuh >210 bar).")
    c = df.Close.loc[sim.index]
    g_ser = index_gate().reindex(c.index, method="ffill").fillna(False)
    atrn = (atr(df) / df.Close).loc[sim.index]
    pos = simulate_positions(sim, c, atrn, g_ser.to_numpy())
    pxl, a = float(df.Close.iloc[-1]), float(atr(df).iloc[-1])
    raw = float(sim.iloc[-1])
    adx_now, chop_now = float(reg["ADX"].iloc[-1]), float(reg["CHOP"].iloc[-1])
    regime = ("trending" if (adx_now >= 20 and chop_now < 38.2)
              else "choppy" if chop_now >= 61.8 else "netral")
    tot = sum((w_last or {}).values())
    agr = sum(wt for name, wt in (w_last or {}).items()
              if np.sign(np.nan_to_num(float(sig[name][1].iloc[-1]))) == np.sign(raw) and np.sign(raw) != 0)
    return {"ticker": ticker, "df": df, "sig": sig, "sim": sim, "c": c, "pos": pos,
            "weights": w_last or {}, "w_total": tot, "raw": raw,
            "conf": agr / tot * 100 if tot and raw != 0 else 0.0,
            "close": pxl, "atr": a, "atr_pct": a / pxl * 100, "adx": adx_now, "chop": chop_now,
            "regime": regime, "gate": bool(g_ser.iloc[-1]), "gate_series": g_ser,
            "holding": int(pos.iloc[-1]) == 1, "stats": trade_stats(pos, c),
            "date": str(df.index[-1].date()), "atrn": atrn, "horizon": H,
            "live": q, "cek": cek}

def cmd_backtest(ticker, entry=20.0, flat=0.0, rebal=63, ts_mult=2.5, gate=True):
    df = load(ticker)
    sig, _ = build_signals(df)
    comp, w_last = walkforward_scores(df, sig, rebal)
    sim = comp.dropna()
    if len(sim) < 120:
        raise SystemExit("History belum cukup utk walk-forward (butuh >210 bar).")
    c = df.Close.loc[sim.index]
    atrn = (atr(df) / df.Close).loc[sim.index]
    g = index_gate().reindex(c.index, method="ffill").fillna(False).to_numpy() if gate else np.ones(len(c), bool)
    ret = c.pct_change().fillna(0)
    pos = simulate_positions(sim, c, atrn, g, entry, flat, ts_mult)
    trades = int((pos.diff() == 1).sum() + (pos.iloc[0] == 1))
    turn = pos.diff().abs().fillna(pos.iloc[0])
    strat = pos.shift(1).fillna(0) * ret - FEE_ROUNDTRIP * turn
    eq = (1 + strat).cumprod()
    bh = c / c.iloc[0]
    yrs = len(c) / 252
    def cagr(e_): return e_.iloc[-1] ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    bhd = (bh / bh.cummax() - 1).min()
    sharpe = strat.mean() / (strat.std() or 1) * np.sqrt(252)
    ui.header(f"BACKTEST WALK-FORWARD {ticker}",
              f"{sim.index[0].date()} → {sim.index[-1].date()} · {len(sim)} bar ({yrs:.1f} th) · "
              f"rekalibrasi bobot tiap {rebal} bar")
    atur = (f"[bold]Aturan:[/] LONG skor ≥ [green]{entry:+.0f}[/] 2 hari berturut"
            f"{' DAN [cyan]IHSG>EMA200[/]' if gate else ''}; keluar saat skor ≤ [red]{flat:+.0f}[/] "
            f"(min. hold 5 hari) ATAU trailing stop [red]{ts_mult}×ATR[/]; eksekusi H+1; "
            f"fee [dim]{FEE_ROUNDTRIP*100:.2f}%[/].")
    lolos = ("Metode lolos edge-minimum (kalibrasi terakhir, "
             f"{len(w_last or {})}): [bold]" + ", ".join(sorted((w_last or {}).keys(), key=lambda k: -w_last[k])[:6]) + "[/]")
    ui.info_panel("ATURAN SIMULASI", [atur, "", lolos], "cyan")
    def _d(v, unit="%", good=None):
        s = f"{v:+.1f}{unit}"
        if good is None:
            return s
        return f"[{'green' if v >= good else 'red'}]{s}[/]"
    rel = (eq.iloc[-1] - bh.iloc[-1]) * 100
    rows = [
        ["Return total", _d((eq.iloc[-1]-1)*100, good=0), _d((bh.iloc[-1]-1)*100), f"{rel:+.1f}%"],
        ["CAGR", _d(cagr(eq)*100, good=0), _d(cagr(bh)*100, good=0),
         _d((cagr(eq)-cagr(bh))*100, good=0)],
        ["Max drawdown", f"[red]{dd*100:.1f}%[/]", f"[red]{bhd*100:.1f}%[/]",
         "[green]lebih dangkal[/]" if dd > bhd else "[red]lebih dalam[/]"],
        ["Exposure", f"{pos.mean()*100:.1f}%", "100.0%", ""],
        ["Jumlah entry", f"[bold]{trades}[/]", "1", ""],
        ["Sharpe", f"[bold]{sharpe:.2f}[/]", "", ""],
    ]
    ui.table(["METRIK", "STRATEGI", "BUY & HOLD", "SELISIH"], rows, caption="long-only + gerbang rezim, fee PP dipotong")
    cur = float(sim.iloc[-1])
    curpos = "[green]LONG[/]" if pos.iloc[-1] else "[grey62]FLAT[/]"
    eq12 = eq.resample("QE").last().iloc[-8:]
    ui.info_panel("SKOR HARI INI", [
        f"Skor konfluensi (bobot walk-forward, tanpa penyesuaian pasar): [bold]{cur:+.1f}[/] → posisi {curpos}",
        "Kurva ekuitas per kuartal (indeks 100):",
        "  " + "  ".join(f"[cyan]{d.year % 100}Q{(d.month - 1) // 3 + 1}:[/] [bold]{v*100:.0f}[/]"
                         for d, v in eq12.items()),
    ], "magenta")
    ui.info_panel("CARA BACA", [
        "· Bobot metode dihitung ulang tiap kuartal hanya dari data yang sudah lewat — tidak ada masa depan "
        "yang intip; hasil tetap optimistis karena asumsi eksekusi ideal & likuiditas.",
        "· [bold]SELISIH[/] positif bukan berarti alfa: dengan exposure ~10% strategi hanya harus lebih kecil "
        "rugi dibanding buy & hold. Manfaat utamanya di [bold]drawdown[/], bukan return.",
        "· [bold]Exposure rendah[/] = sistem lebih sering diam; itu disengaja (gerbang rezim + ambang skor). "
        "Sharpe < 0 berarti periode ini tidak ramah untuk strategi long-only.",
        "· Uji kepekaan sebelum percaya: ubah [bold]--entry --flat --ts --rebal[/] dan [bold]--nogate[/]; "
        "jika hasil hanya bagus pada satu set angka, itu overfit."], "cyan")

def track_record(sim, c, gate_series=None, horizon=5, thresh=20.0):
    """Rekam jejak sinyal INI di saham INI — pengganti klaim 'yakin profit'.

    Untuk tiap bar di mana skor walk-forward ≥ ambang (dan gate terbuka, jika diminta),
    berapa persen harga 5 hari kemudian lebih tinggi, dan berapa return bersih rata-ratanya
    setelah fee round-trip. dibandingkan dengan base rate periode yang sama.
    """
    cut = sim.index[:-horizon] if len(sim) > horizon else sim.index
    fwd = (c.shift(-horizon) / c - 1 - FEE_ROUNDTRIP).reindex(cut)
    s = sim.reindex(cut)
    m = (s >= thresh).to_numpy()
    if gate_series is not None:
        m = m & gate_series.reindex(cut).fillna(False).astype(bool).to_numpy()
    f = fwd[m]
    return {"n": int(m.sum()), "thresh": thresh, "horizon": horizon,
            "prob_up": float((f > 0).mean()) if len(f) else np.nan,
            "avg_net": float(f.mean()) if len(f) else np.nan,
            "median_net": float(f.median()) if len(f) else np.nan,
            "worst": float(f.min()) if len(f) else np.nan,
            "base_up": float((fwd.dropna() > 0).mean()),
            "edge_pp": (float((f > 0).mean()) - float((fwd.dropna() > 0).mean())) * 100 if len(f) else np.nan}

def decide(ticker, capital=100_000_000, risk_pct=1.0, ms=None, entry=20.0, ts_mult=2.5, rebal=63,
           horizon=None, max_pos_pct=0.20, with_live=False):
    """Angka keputusan satu saham dalam dict — sumber skornya `snapshot()`, sama seperti scanner."""
    s = snapshot(ticker, rebal, horizon, with_live=with_live)
    pxl, a, st, gate_now = s["close"], s["atr"], s["stats"], s["gate"]
    if ms is None and market_state:
        ms = market_state.get_market_state(UNIVERSE)
    sp = market_state.sector_penalty(ticker, ms) if ms else 0.0
    mkt_adj = ms["adjust"] if ms else 0.0
    adj = mkt_adj + sp
    raw, final = s["raw"], float(np.clip(s["raw"] + adj, -100, 100))
    act, why = action_for(final, gate_now, s["holding"], entry)

    R = ts_mult * a
    qty = int(capital * risk_pct / 100 / R) // 100 * 100
    max_qty = int(capital * max_pos_pct / pxl) // 100 * 100
    qty = max(min(qty, max_qty), 0)
    qty_full = qty
    if act.startswith("SIAGA"):
        # gate tertutup → porsi 1/3; brief memakai angka yang sama, tidak membagi ulang
        qty = qty // 3 // 100 * 100
    d = dict(s)
    d.update({"mood": ms["mood"] if ms else "n/a", "act": act, "why": why, "adj": adj, "final": final,
              "sector_adj": sp, "mkt_adj": mkt_adj, "qty_full": qty_full, "entry": entry,
              "bullish": final >= 5, "risk_per_share": R, "stop": pxl - R, "zone_lo": pxl - a,
              "t1": pxl + 1.5 * R, "t2": pxl + 3 * R, "qty": qty, "max_pos_pct": max_pos_pct,
              "capped_20pct": qty == max_qty and qty > 0, "capital": capital, "risk_pct": risk_pct,
              "holding_sim": s["holding"], "sn": s})
    return d

def cmd_decide(ticker, capital=100_000_000, risk_pct=1.0, entry=20.0, ts_mult=2.5, rebal=63):
    d = decide(ticker, capital, risk_pct, entry=entry, ts_mult=ts_mult, rebal=rebal)
    render_decision(d)

def data_status_lines(d):
    """Baris panel validasi data dari hasil `snapshot(with_live=True)`."""
    if not live or not d.get("live"):
        return []
    pr, ok, q = d["cek"] if d.get("cek") else ([], [], d["live"])
    return live.check_lines(d["ticker"], pr, ok, q)


def render_decision(d, compact=False):
    ticker, pxl, a = d["ticker"], d["close"], d["atr"]
    R, stop, t1, t2 = d["risk_per_share"], d["stop"], d["t1"], d["t2"]
    st, final, act, why = d["stats"], d["final"], d["act"], d["why"]
    ds = data_status_lines(d)
    if ds:
        ui.info_panel("VALIDASI DATA", ds, "red" if any("X Bahaya" in x for x in ds) else "cyan")
    ui.header(f"KEPUTUSAN {ticker}", f"{d['date']} · harga [bold]{pxl:,.0f}[/] · "
              f"gate indeks [bold]{'TERBUKA' if d['gate'] else 'TERTUTUP'}[/]"
              + (f" · {live.mark(d['live'])}" if live and d.get("live") else ""))
    ui.decision_banner(act, why, final)
    rc = {"trending": "green", "choppy": "yellow"}.get(d["regime"], "grey62")
    ev = [
        f"Skor teknis walk-forward ··· [bold]{d['raw']:+.1f}[/]",
        f"Penyesuaian pasar + sektor · [bold]{d['adj']:+.1f}[/]  (mood: {ui.mood_markup(d['mood'])})",
        f"[bold]SKOR AKHIR[/] ............. [bold][{'green' if final >= 0 else 'red'}]{final:+.1f}[/][/]  "
        f"| keyakinan metode searah: [bold]{d['conf']:.0f}%[/]",
        "Gate indeks (IHSG>EMA200) ·· " + ("[green]TERBUKA — rezim historis ramah[/]" if d["gate"]
          else "[bold white on red]TERTUTUP — pasar secara historis sedang rusak[/]"),
        f"Rezim saham ini ............. ADX [bold]{d['adx']:.0f}[/], CHOP [bold]{d['chop']:.0f}[/] "
        f"[{rc}]{d['regime']}[/], ATR [bold]{d['atr_pct']:.1f}%[/]/hari",
        "Sedang LONG di simulasi ... " + ("[green]YA[/]" if d["holding_sim"] else "[grey62]TIDAK[/]"),
    ]
    if st["n"]:
        ev.append(f"Historik trade simulasi ..... [bold]{st['n']}[/] posisi, win rate "
                  f"[{'green' if st['win']>=0.5 else 'yellow'}]{st['win']*100:.0f}%[/], median hold "
                  f"[bold]{st['median_hold']:.0f}[/] hari bursa (~{st['median_hold']/21:.1f} bulan)")
    ui.info_panel("EVIDEN", ev, "cyan")

    if d["bullish"]:
        plan = [
            f"Zona entry ideal : [green]{pxl - a:,.0f} – {pxl:,.0f}[/]  (pullback 1 ATR s/d harga kini)",
            f"Stop awal        : [red]{stop:,.0f}[/]  (2,5×ATR, risiko per lembar ≈ {R:,.0f})",
            f"Target 1 (+1.5R) : [bold]{t1:,.0f}[/] → ambil 50% profit, geser stop ke harga modal",
            f"Target 2 (+3R)   : [bold]{t2:,.0f}[/] → sisanya biarkan lari dengan trailing stop 2,5×ATR dari puncak",
        ]
        if st["n"]:
            plan.append(f"Durasi realistis : ~[bold]{st['median_hold']:.0f}[/] hari bursa (median historik); "
                        "keluar lebih awal jika skor walk-forward jatuh di bawah 0.")
        ui.info_panel("RENCANA TRANSAKSI (acuan jika Anda memutuskan masuk)", plan, "green")
    else:
        ui.info_panel("RENCANA (posisi baru ditolak — ini aturan keluar)", [
            "[bold]Sistem tidak membuka posisi baru di skor ini.[/]",
            f"Jika masih memegang: [red]KURANGI/JUAL[/] bertahap; bila bertahan, patuhi "
            f"trailing stop [red]{stop:,.0f}[/] (2,5×ATR dari harga kini) dan [bold]keluar total[/] "
            "saat skor akhir jatuh di bawah −20.",
            "Ubah keputusan jadi BELI hanya jika: skor akhir naik ke ≥ +20 [bold]dan[/] IHSG kembali di "
            "atas EMA200 (gate terbuka).",
        ], "red")

    cap, rp = d["capital"], d["risk_pct"]
    risk_idr = cap * rp / 100
    qty = d["qty"]
    sz = [f"Modal [bold]{cap:,.0f}[/], risiko {rp}% = [bold]{risk_idr:,.0f}[/]"]
    if not d["bullish"]:
        sz.append("[bold]Ukuran posisi baru: 0 lembar[/] — skor di bawah ambang, uang lebih baik "
                  "ditaruh di instrumen risiko rendah (SBN/redepo) sampai setup membaik.")
    elif qty >= 100:
        cap_note = f" [dim](dibatasi {d['max_pos_pct']*100:.0f}% modal)[/]" if d["capped_20pct"] else ""
        sz.append(f"Maks [bold cyan]{qty:,} lembar ({qty//100} lot)[/] ≈ {qty*pxl:,.0f} rupiah "
                  f"= {qty*pxl/cap*100:.0f}% modal{cap_note}")
        if act.startswith("SIAGA"):
            sz.append(f"[yellow]Sudah dipangkas ke 1/3[/] dari {d['qty_full']//100} lot karena gate indeks "
                      "tertutup — risiko sistemik tidak bisa dihindari dengan diversifikasi antar-saham.")
    else:
        sz.append("[red]Tidak ada ukuran yang memenuhi risiko — jarak stop terlalu lebar untuk modal ini.[/]")
    if act.startswith("SIAGA") and qty < 100:
        sz.append("[yellow]1/3 dari jatah normal kurang dari 1 lot[/] — artinya sistem bilang jangan masuk.")
    ui.info_panel("UKURAN POSISI", sz, "magenta")
    if not compact:
        _decision_legend(d.get("entry", 20.0))

def _decision_legend(entry=20.0):
    ui.info_panel("CARA BACA & ATURAN MAIN", [
        f"· [bold]Keputusan[/] datang dari SKOR AKHIR, bukan dari satu indikator. Ambang yang berlaku: "
        f"≥ +35 BELI KUAT, ≥ {entry:+.0f} BELI (hanya jika gate terbuka), +5…{entry:+.0f} WATCHLIST, "
        "−5…+5 HOLD/TUNGGU, sisanya kurangi/jual.",
        "· [bold]Gate indeks tertutup[/] = secara historis IHSG di bawah EMA200; di rezim itu breakout lebih "
        "sering gagal, jadi skor kuat sekalipun hanya boleh jadi posisi kecil ([yellow]SIAGA[/]).",
        "· [bold]Median hold[/] adalah perkiraan waktu, bukan janji — keluar lebih awal bila skor jatuh ≤ 0 "
        "atau trailing stop 2,5×ATR tersentuh. Jangan menahan posisi yang sudah kena stop.",
        "· [bold]keyakinan metode searah[/] rendah (<40%) berarti skor digerakkan sedikit metode; perlakukan "
        "sebagai sinyal lemah. Sistem ini pengendali risiko, bukan mesin prediksi — modal terbesar Anda tetap "
        "stop loss."], "cyan")

def cmd_market():
    if not market_state:
        raise SystemExit("market_state.py tidak ditemukan.")
    market_state.print_panel(market_state.get_market_state(UNIVERSE, use_cache=False))
    ui.info_panel("CARA BACA", [
        "· [bold]20 hari[/] = momentum makro; [bold]HV20[/] = volatilitas tersurat tahunan. HV Brent > 25% atau "
        "USD/IDR naik tajam = tekanan pada IHSG dan saham berbiaya impor/utang dolar.",
        "· [bold]Breadth[/] = porsi LQ45 di atas EMA50/EMA200. Di bawah 40% berarti kenaikan lebih rapuh — "
        "sedikit motor, penurunan mudah menjalar.",
        "· Skor berita berasal dari kata kunci risiko di judul (geopolitik, valas, regulasi) — ia "
        "[bold]penanda tekanan[/], bukan peramal arah. Tema panas → sektor terkait kena penalti kecil di scanner.",
        "· [bold]Penyesuaian[/] −30…+30 ditambahkan ke skor teknis tiap saham; mood "
        "[green]MENGIJINKAN[/]/[yellow]NETRAL[/]/[red]BERISIKO[/] adalah ringkasannya."], "cyan")

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=["analyze", "score", "scan", "market", "backtest", "decide"])
    p.add_argument("ticker", nargs="?", default="^JKSE")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--modal", type=float, default=100_000_000)
    p.add_argument("--risiko", type=float, default=1.0)
    p.add_argument("--entry", type=float, default=20.0)
    p.add_argument("--flat", type=float, default=0.0)
    p.add_argument("--ts", type=float, default=2.5)
    p.add_argument("--rebal", type=int, default=63)
    p.add_argument("--nogate", action="store_true", help="matikan filter rezim IHSG>EMA200 pada backtest")
    a = p.parse_args()
    if a.mode == "score":
        cmd_score(a.ticker)
    elif a.mode == "analyze":
        cmd_analyze(a.ticker, a.rebal, a.entry, a.ts)
    elif a.mode == "market":
        cmd_market()
    elif a.mode == "backtest":
        cmd_backtest(a.ticker, a.entry, a.flat, a.rebal, a.ts, not a.nogate)
    elif a.mode == "decide":
        cmd_decide(a.ticker, a.modal, a.risiko, a.entry, a.ts, a.rebal)
    else:
        cmd_scan(a.top)

if __name__ == "__main__":
    main()
