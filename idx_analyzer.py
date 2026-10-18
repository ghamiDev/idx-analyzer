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
# Biaya nyata per sisi. FEE_ROUNDTRIP dipakai untuk KALIBRASI akurasi (target return bersih),
# sedangkan simulasi memakai dua sisi terpisah + slippage supaya bisa diuji kepekaannya.
FEE_BUY = 0.0015    # 0,15% beli
FEE_SELL = 0.0025   # 0,25% jual (fee + PPh final 0,1% biasanya di sisi jual)
SLIP = 0.0005       # slippage per sisi: 0,05% ~ 1 tick pada saham Rp1.000-an
FEE_ROUNDTRIP = FEE_BUY + FEE_SELL
MAX_POS_PCT = 0.20    # porsi maksimum satu posisi pada backtest portofolio
MAX_VOL_PCT = 0.01    # tidak boleh masuk di atas 1% nilai transaksi harian saham itu
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
        f"· Tiap metode memberi sinyal [green]{ui.ic('up')} BELI[/] / [red]{ui.ic('down')} JUAL[/] / "
        "[grey62]— netral[/]; skor adalah "
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

def compute_scan(tickers=None, ms=None, rebal=63, horizon=None, thresh=20.0):
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

def _side_costs(fee_in=None, fee_out=None):
    return (FEE_BUY + SLIP if fee_in is None else fee_in,
            FEE_SELL + SLIP if fee_out is None else fee_out)


def _mk_trade(idx, i0, i1, px_in, px_out, risk_frac, reason, fee_in, fee_out, open_pos=False):
    gross = px_out / px_in - 1
    return {"date_in": idx[i0], "date_out": idx[i1], "px_in": px_in, "px_out": px_out,
            "hold": i1 - i0 + 1, "reason": reason, "gross": gross,
            "net": gross - fee_in - fee_out, "risk_frac": risk_frac, "open": open_pos}


def simulate_trades(sim, c, atrn, g, entry=20.0, flat=0.0, ts_mult=2.5, min_hold=5,
                    fee_in=None, fee_out=None):
    """Bar posisi + daftar transaksi lengkap (tanggal, harga, hold, alasan keluar, P&L bersih).

    Konvensi tetap sama seperti sebelumnya: posisi dihitung dari close bar sinyal, return
    baru berjalan di bar berikutnya (eksekusi H+1). Biaya masuk dan keluar dipotong terpisah
    supaya bisa diuji kepekaannya sendiri-sendiri.
    """
    fi, fo = _side_costs(fee_in, fee_out)
    p, peak, age, streak = 0, np.nan, 0, 0
    i0 = px_in = risk_frac = None
    pos, trades = [], []
    for i in range(len(sim)):
        v, px, a = float(sim.iloc[i]), float(c.iloc[i]), float(atrn.iloc[i])
        streak = streak + 1 if v >= entry else 0
        if p == 1:
            peak = max(peak, px)
            age += 1
            ts_hit = px < peak * (1 - ts_mult * a)
            if (age >= min_hold and v <= flat) or ts_hit:
                trades.append(_mk_trade(c.index, i0, i, px_in, px, risk_frac,
                                        "trailing stop" if ts_hit else "skor turun", fi, fo))
                p, age = 0, 0
        if p == 0 and streak >= 2 and g[i]:
            p, peak, age = 1, px, 1
            i0, px_in, risk_frac = i, px, ts_mult * a
        pos.append(p)
    if p == 1:
        trades.append(_mk_trade(c.index, i0, len(sim) - 1, px_in, float(c.iloc[-1]), risk_frac,
                                "masih terbuka", fi, fo, open_pos=True))
    return pd.Series(pos, index=sim.index), trades


def simulate_positions(sim, c, atrn, g, entry=20.0, flat=0.0, ts_mult=2.5, min_hold=5):
    """Hanya bar posisinya — dipakai snapshot/scanner yang tidak butuh daftar trade."""
    return simulate_trades(sim, c, atrn, g, entry, flat, ts_mult, min_hold)[0]


def strategy_returns(pos, c, fee_in=None, fee_out=None):
    """Return harian strategi dari bar posisi: potong biaya masuk di bar masuk, keluar di bar keluar."""
    fi, fo = _side_costs(fee_in, fee_out)
    d = pos.diff().fillna(pos.iloc[0])
    return pos.shift(1).fillna(0) * c.pct_change().fillna(0) - fi * (d > 0) - fo * (d < 0)


def adv_rupiah(df, n=20):
    """Perkiraan nilai transaksi harian (rupiah) — dasar cap likuiditas backtest portofolio."""
    if "Volume" not in df.columns:
        return pd.Series(np.nan, index=df.index)
    return (df["Close"] * df["Volume"].fillna(0)).rolling(n).median()

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

def snapshot(ticker, rebal=63, horizon=None, with_live=False, entry=20.0, flat=0.0, ts_mult=2.5):
    """SATU sumber angka untuk semua mode: skor walk-forward, bobot, rezim, ATR, status posisi.

    Dipakai compute_scan (scanner), decide (keputusan), portfolio (pemantauan) dan cmd_analyze,
    sehingga skor yang tampil di scanner = skor yang dipakai menghitung ukuran posisi.
    `horizon` = hari bursa ke depan yang jadi patokan akurasi bobot (default 20; mode minggu pakai 5).
    `with_live=True` hanya mengubah [harga yang dipakai untuk ukuran posisi] (`close`), TIDAK
    mengubah bar yang dipakai menghitung skor — jadi angka SKOR di scanner, `check`, `pos`, dan
    `minggu` selalu sama. Kutipan yang lebih baru dari bar harian dilaporkan lewat `live_ahead`.
    `entry`/`flat`/`ts_mult` dipakai [simulasi posisi & statistik historik] saja — skor tidak
    berubah oleh ketiganya, jadi pemanggil wajib meneruskan nilai config agar aturan keluar yang
    tampil di layar sama dengan yang dihitung `pos`/`brief`/`backtest`.
    """
    H = horizon or HORIZON
    df = load(ticker)
    q = cek = None
    live_px = None
    if with_live and live:
        q = live.quote(ticker)
        if not q.get("error") and q["price"] is not None and q["time"]:
            live_px = float(q["price"])
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
    pos = simulate_positions(sim, c, atrn, g_ser.to_numpy(), entry, flat, ts_mult)
    bar_close = float(df.Close.iloc[-1])
    pxl = live_px if live_px is not None else bar_close
    a = float(atr(df).iloc[-1])
    raw = float(sim.iloc[-1])
    ahead = bool(q and not q.get("error") and q.get("time") and live_px is not None
                 and q["time"].date() > df.index[-1].date())
    adx_now, chop_now = float(reg["ADX"].iloc[-1]), float(reg["CHOP"].iloc[-1])
    regime = ("trending" if (adx_now >= 20 and chop_now < 38.2)
              else "choppy" if chop_now >= 61.8 else "netral")
    tot = sum((w_last or {}).values())
    agr = sum(wt for name, wt in (w_last or {}).items()
              if np.sign(np.nan_to_num(float(sig[name][1].iloc[-1]))) == np.sign(raw) and np.sign(raw) != 0)
    return {"ticker": ticker, "df": df, "sig": sig, "sim": sim, "c": c, "pos": pos,
            "weights": w_last or {}, "w_total": tot, "raw": raw,
            "conf": agr / tot * 100 if tot and raw != 0 else 0.0,
            "close": pxl, "bar_close": bar_close, "live_px": live_px, "live_ahead": ahead,
            "atr": a, "atr_pct": a / pxl * 100, "adx": adx_now, "chop": chop_now,
            "regime": regime, "gate": bool(g_ser.iloc[-1]), "gate_series": g_ser,
            "holding": int(pos.iloc[-1]) == 1, "stats": trade_stats(pos, c),
            "date": str(df.index[-1].date()), "atrn": atrn, "horizon": H,
            "live": q, "cek": cek}

def perf(strat, pos=None, c=None):
    """Ringkasan kinerja dari return harian — dipakai backtest tunggal, uji peka, dan portofolio."""
    eq = (1 + strat).cumprod()
    yrs = max(len(strat) / 252, 1e-9)
    out = {"eq": eq, "ret": float(eq.iloc[-1] - 1), "cagr": float(eq.iloc[-1] ** (1 / yrs) - 1),
           "dd": float((eq / eq.cummax() - 1).min()),
           "sharpe": float(strat.mean() / (strat.std() or 1) * np.sqrt(252)), "yrs": yrs}
    if pos is not None:
        out["exposure"] = float(pos.mean())
    if c is not None:
        bh = c / c.iloc[0]
        out.update(bh_ret=float(bh.iloc[-1] - 1), bh_cagr=float(bh.iloc[-1] ** (1 / yrs) - 1),
                   bh_dd=float((bh / bh.cummax() - 1).min()), bh_eq=bh)
    return out


def _fmt_pct(v, good=None):
    s = f"{v * 100:+.1f}%"
    if good is None:
        return s
    return f"[{'green' if v >= good else 'red'}]{s}[/]"


def trade_rows(tr, last=12):
    """Baris tabel transaksi: harga dibulatkan ke tick, P&L sudah bersih dari biaya dua sisi."""
    rows = []
    for t in tr[-last:]:
        rows.append([t["date_in"].strftime("%Y-%m-%d"),
                     t["date_out"].strftime("%Y-%m-%d") + ("*" if t["open"] else ""),
                     f"{t['hold']}", f"{t['px_in']:,.0f}", f"{t['px_out']:,.0f}",
                     _fmt_pct(t["net"], 0), t["reason"]])
    return rows


def trade_summary(tr):
    if not tr:
        return "Tidak ada satu pun transaksi pada rentang ini."
    done = [t for t in tr if not t["open"]]
    win = sum(1 for t in done if t["net"] > 0) / max(len(done), 1)
    avg = float(np.mean([t["net"] for t in done])) if done else 0.0
    best = max((t["net"] for t in tr), default=0.0)
    worst = min((t["net"] for t in tr), default=0.0)
    hold = float(np.median([t["hold"] for t in tr]))
    alasan = {}
    for t in done:
        alasan[t["reason"]] = alasan.get(t["reason"], 0) + 1
    urut = sorted(alasan.items(), key=lambda kv: -kv[1])
    return (f"[bold]{len(tr)}[/] transaksi · menang [bold]{win*100:.0f}%[/] dari "
            f"{len(done)} yang tertutup · median hold [bold]{hold:.0f}[/] hari · "
            f"rata-rata bersih [bold]{avg*100:+.1f}%[/] · terbaik {best*100:+.1f}% / "
            f"terburuk {worst*100:+.1f}% · keluar karena "
            + ", ".join(f"{k} ×{v}" for k, v in urut))


def cmd_backtest(ticker, entry=20.0, flat=0.0, rebal=63, ts_mult=2.5, gate=True,
                 fee_in=None, fee_out=None, slip=None, show_trades=12):
    df = load(ticker)
    sig, _ = build_signals(df)
    comp, w_last = walkforward_scores(df, sig, rebal)
    sim = comp.dropna()
    if len(sim) < 120:
        raise SystemExit("History belum cukup utk walk-forward (butuh >210 bar).")
    c = df.Close.loc[sim.index]
    atrn = (atr(df) / df.Close).loc[sim.index]
    g = index_gate().reindex(c.index, method="ffill").fillna(False).to_numpy() if gate else np.ones(len(c), bool)
    fi, fo = _side_costs(fee_in, fee_out)
    pos, tr = simulate_trades(sim, c, atrn, g, entry, flat, ts_mult, fee_in=fi, fee_out=fo)
    p = perf(strategy_returns(pos, c, fi, fo), pos, c)
    eq, bh = p["eq"], p["bh_eq"]
    yrs = p["yrs"]
    ui.header(f"BACKTEST WALK-FORWARD {ticker}",
              f"{sim.index[0].date()} → {sim.index[-1].date()} · {len(sim)} bar ({yrs:.1f} th) · "
              f"rekalibrasi bobot tiap {rebal} bar")
    atur = (f"[bold]Aturan:[/] LONG skor ≥ [green]{entry:+.0f}[/] 2 hari berturut"
            f"{' DAN [cyan]IHSG>EMA200[/]' if gate else ''}; keluar saat skor ≤ [red]{flat:+.0f}[/] "
            f"(min. hold 5 hari) ATAU trailing stop [red]{ts_mult}×ATR[/]; eksekusi H+1.")
    sl = SLIP if slip is None else slip
    biaya = (f"[bold]Biaya:[/] masuk [dim]{fi*100:.2f}%[/] + keluar [dim]{fo*100:.2f}%[/] per posisi "
             f"(total [bold]{(fi+fo)*100:.2f}%[/], sudah termasuk slippage "
             f"[dim]{sl*100:.2f}%[/] per sisi). Ubah: [bold]idx.py cfg set slip_pct 0.2[/].")
    lolos = ("Metode lolos edge-minimum (kalibrasi terakhir, "
             f"{len(w_last or {})}): [bold]" + ", ".join(sorted((w_last or {}).keys(), key=lambda k: -w_last[k])[:6]) + "[/]")
    ui.info_panel("ATURAN SIMULASI", [atur, biaya, "", lolos], "cyan")
    rel = (eq.iloc[-1] - bh.iloc[-1]) * 100
    rows = [
        ["Return total", _fmt_pct(p["ret"], 0), _fmt_pct(p["bh_ret"]), f"{rel:+.1f}%"],
        ["CAGR", _fmt_pct(p["cagr"], 0), _fmt_pct(p["bh_cagr"]),
         _fmt_pct(p["cagr"] - p["bh_cagr"], 0)],
        ["Max drawdown", f"[red]{p['dd']*100:.1f}%[/]", f"[red]{p['bh_dd']*100:.1f}%[/]",
         "[green]lebih dangkal[/]" if p["dd"] > p["bh_dd"] else "[red]lebih dalam[/]"],
        ["Exposure", f"{p['exposure']*100:.1f}%", "100.0%", ""],
        ["Jumlah entry", f"[bold]{len(tr)}[/]", "1", ""],
        ["Sharpe", f"[bold]{p['sharpe']:.2f}[/]", "", ""],
    ]
    ui.table(["METRIK", "STRATEGI", "BUY & HOLD", "SELISIH"], rows,
             caption="long-only + gerbang rezim, biaya dua sisi dipotong")
    ui.info_panel(f"TRANSAKSI ({min(len(tr), show_trades)} terakhir dari {len(tr)})",
                  [trade_summary(tr), "",
                   "Tabel berikutnya: tanggal masuk → keluar, harga, P&L bersih, alasan keluar."],
                  "magenta")
    if tr:
        ui.table(["MASUK", "KELUAR", "HOLD", "HARGA MASUK", "HARGA KELUAR", "P&L BERSIH", "ALASAN KELUAR"],
                 trade_rows(tr, show_trades),
                 caption="* = masih terbuka sampai bar terakhir")
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
        "yang intip. Biaya dua sisi sudah dipotong, tapi eksekusi masih diidealkan: harga penutupan pas, "
        "tanpa gap intraday, order selalu terisi.",
        "· [bold]SELISIH[/] positif bukan berarti alfa: dengan exposure rendah strategi hanya harus lebih "
        "kecil rugi dibanding buy & hold. Manfaat utamanya di [bold]drawdown[/], bukan return.",
        "· [bold]Exposure rendah[/] = sistem lebih sering diam; itu disengaja (gerbang rezim + ambang skor). "
        "Sharpe < 0 berarti periode ini tidak ramah untuk strategi long-only.",
        "· Kolom ALASAN KELUAR menunjukkan aturan mana yang benar-benar bekerja: kalau hampir semua keluar "
        "karena trailing stop, disiplin keluar itulah sumber utamanya — bukan kemampuan memilih arah.",
        "· Jangan percaya satu set angka: jalankan [bold]idx.py peka "
        f"{ticker.replace('.JK','')}[/] untuk menguji seluruh grid ambang & stop."], "cyan")


def cmd_backtest_portfolio(res, cfg, show_trades=15):
    """Render hasil portfolio_backtest(): ekuitas bersama, rebutan slot, cap likuiditas."""
    d, eq, bh = res["dates"], res["eq"], res["bh"]
    ui.header(f"BACKTEST PORTOFOLIO ({res['legs']} SAHAM)",
              f"{d[0].date()} → {d[-1].date()} · {len(d)} bar ({res['yrs']:.1f} th) · "
              f"modal awal Rp {res['capital']:,.0f}")
    atur = (f"[bold]Aturan rebutan modal:[/] skor ≥ [green]{cfg['entry']:+.0f}[/] 2 hari berturut "
            f"{'DAN gerbang IHSG>EMA200 terbuka' if cfg['gate'] else 'tanpa gerbang rezim'}; "
            f"keluar saat skor ≤ [red]{cfg['flat']:+.0f}[/] (min. hold 5 hari) atau trailing stop "
            f"[red]{cfg['ts_mult']}×ATR[/]. Maksimum [bold]{cfg['max_positions']}[/] posisi bersamaan, "
            f"risiko per posisi [bold]{cfg['risk_pct']}%[/] ekuitas berjalan.")
    batas = (f"[bold]Cap ukuran posisi:[/] paling besar [bold]{cfg['max_pos_pct']:.0f}%[/] ekuitas dan "
             f"[bold]{cfg['max_volume_pct']:.1f}%[/] nilai transaksi harian saham itu (ADV 20 hari). "
             f"Sinyal yang tidak kebagian uang/slot dicatat sebagai [bold]ditolak[/], bukan dihapus.")
    biaya = (f"[bold]Biaya:[/] masuk [dim]{cfg['fee_buy_pct']:.2f}%[/] + keluar "
             f"[dim]{cfg['fee_sell_pct']:.2f}%[/] + slippage [dim]{cfg['slip_pct']:.2f}%[/] per sisi · "
             f"total terbayar pada simulasi ini [bold]Rp {res['fees']:,.0f}[/].")
    ui.info_panel("ATURAN SIMULASI", [atur, batas, "", biaya], "cyan")
    rel = (res["ret"] - res["bh_ret"]) * 100
    rows = [
        ["Return total", _fmt_pct(res["ret"], 0), _fmt_pct(res["bh_ret"]), f"{rel:+.1f}%"],
        ["CAGR", _fmt_pct(res["cagr"], 0), "", ""],
        ["Max drawdown", f"[red]{res['dd']*100:.1f}%[/]", f"[red]{res['bh_dd']*100:.1f}%[/]",
         "[green]lebih dangkal[/]" if res["dd"] > res["bh_dd"] else "[red]lebih dalam[/]"],
        ["Exposure", f"{res['exposure']*100:.1f}%", "100.0%", "bar dengan ≥1 posisi"],
        ["Sharpe", f"[bold]{res['sharpe']:.2f}[/]", "", ""],
        ["Posisi dibuka / ditolak", f"[bold]{res['taken']}[/] / {res['rejected']}", "", ""],
    ]
    ui.table(["METRIK", "PORTOFOLIO", "BELI SEMUA (samaan bobot)", "SELISIH"], rows,
             caption="kolom pembanding adalah sama rata 1/N dari saham yang sama — bukan IHSG")
    if "ihsg_ret" in res:
        ui.info_panel("PEMBANDING PASAR", [
            f"· IHSG pada periode yang sama: return [bold]{res['ihsg_ret']*100:+.1f}%[/], "
            f"max drawdown [bold]{res['ihsg_dd']*100:.1f}%[/].",
            "· Kalau portofolio kalah dari IHSG dengan drawdown yang tidak lebih dangkal, "
            "sebaiknya beli indeks saja."], "magenta")
    tolak = [f"[bold]{res['cap_slot']}[/] karena slot penuh ({cfg['max_positions']} posisi)",
             f"[bold]{res['cap_pos']}[/] karena batas {cfg['max_pos_pct']:.0f}% ekuitas",
             f"[bold]{res['cap_liq']}[/] karena cap likuiditas {cfg['max_volume_pct']:.1f}% ADV",
             f"[bold]{res['cap_cash']}[/] karena uang tunai tidak cukup (min. 1 lot)"]
    cash_bound = res["cap_cash"] >= max(res["taken"], 1)
    ui.info_panel("SAAT SINYAL DITOLAK", [
        "Sinyal beli yang muncul tapi tidak bisa dieksekusi tetap dihitung — itu harga dari aturan "
        "ukuran posisi yang ketat:",
        "· " + "\n· ".join(tolak),
        "",
        ("[yellow]Pola yang terlihat:[/] penolakan terbesar adalah 'tidak cukup uang tunai'. Itu batasan "
         "modal, bukan sinyal yang buruk — dengan modal sekecil ini, satu lot sudah memakan porsi besar "
         "portofolio sehingga aturan risiko per posisi tidak bisa dipenuhi. Kurangi daftar saham, naikkan "
         "modal, atau pegang beberapa saham pilihan saja."
         if cash_bound else ""),
        "[grey62]Kalau 'slot penuh' mendominasi, sistem menghasilkan sinyal lebih banyak daripada yang "
        "bisa ditampung modal; urutan antrean mengikuti daftar ticker, jadi hasilnya sedikit bergantung "
        "pada urutan itu.[/]"],
        "yellow")
    per = {}
    for t in res["trades"]:
        p = per.setdefault(t["ticker"], {"n": 0, "win": 0, "ret": 0.0, "not": 0.0})
        p["n"] += 1
        p["win"] += 1 if t["ret"] > 0 else 0
        p["ret"] += t["ret"]
        p["not"] += t["notional"]
    if per:
        tak_kebagian = [t for t in res["tickers"] if t not in per]
        ui.table(["SAHAM", "TRADE TERTUTUP", "MENANG", "RATA-RATA P&L", "MODAL DIPAKAI"],
                 [[t, f"{v['n']}", f"{v['win']/v['n']*100:.0f}%", _fmt_pct(v["ret"] / v["n"], 0),
                   f"Rp {v['not']:,.0f}"] for t, v in
                  sorted(per.items(), key=lambda kv: -kv[1]["ret"] / kv[1]["n"])],
                 caption="hanya posisi yang benar-benar dieksekusi oleh scheduler; modal dipakai = total "
                         "nilai beli sepanjang periode"
                         + (f" · tidak pernah kebagian: {', '.join(tak_kebagian)}"
                            if tak_kebagian else ""))
    if res["trades"]:
        ui.table(["SAHAM", "MASUK", "KELUAR", "HOLD", "MODAL", "P&L BERSIH", "ALASAN KELUAR"],
                 [[t["ticker"], t["in"].strftime("%Y-%m-%d"), t["out"].strftime("%Y-%m-%d"),
                   f"{t['hold']}", f"Rp {t['notional']:,.0f}", _fmt_pct(t["ret"], 0), t["reason"]]
                  for t in res["trades"][-show_trades:]],
                 caption=f"{min(len(res['trades']), show_trades)} terakhir dari "
                         f"{len(res['trades'])} posisi tertutup")
    eq_q = eq.resample("QE").last().iloc[-8:]
    ui.info_panel("EKUITAS PER KUARTAL", [
        "  " + "  ".join(f"[cyan]{x.year % 100}Q{(x.month - 1) // 3 + 1}:[/] [bold]{v:,.0f}[/]"
                         for x, v in eq_q.items()),
        f"· Akhir: [bold]Rp {eq.iloc[-1]:,.0f}[/] dari Rp {res['capital']:,.0f} "
        f"({_fmt_pct(res['ret'], 0)})."], "magenta")
    ui.info_panel("CARA BACA", [
        "· Nilai utama backtest portofolio ada di [bold]drawdown[/] dan [bold]exposure[/], bukan di "
        "return: modal yang terbagi ke beberapa saham seharusnya meratakan guncangan.",
        "· [bold]Ditolak[/] bukan bug — itu konsekuensi wajar dari membatasi risiko. Tapi kalau "
        "persentase penolakannya tinggi, artinya aturanmu terlalu ketat untuk jumlah saham segitu.",
        "· Saham yang sama bisa muncul beberapa kali di daftar 'ditolak' pada hari berbeda; antrean "
        "diproses mengikuti urutan daftar ticker, jadi hasilnya sedikit bergantung pada urutan itu.",
        "· Semua asumsi masih optimistis: dieksekusi di harga penutupan, tanpa gap intraday, "
        "tanpa batas harga (auto-rejection), dan tanpa T+2 yang menahan uang.",
        "· [bold]Uji peka[/] konfigurasi ini sebelum percaya: jalankan "
        f"[bold]idx.py peka {res['tickers'][0].replace('.JK','')}[/]."], "cyan")


def _sim_window(df, sig, rebal, gate_series):
    """Skor walk-forward + close + ATR normal + gate pada jendela yang sama (buang NaN awal)."""
    comp, _ = walkforward_scores(df, sig, rebal)
    sim = comp.dropna()
    if len(sim) < 120:
        return None
    c = df.Close.loc[sim.index]
    atrn = (atr(df) / df.Close).loc[sim.index]
    g = gate_series.reindex(sim.index, method="ffill").fillna(False).to_numpy()
    return sim, c, atrn, g


GRID_ENTRY = (10.0, 15.0, 20.0, 25.0, 30.0)
GRID_TS = (2.0, 2.5, 3.0)
GRID_REBAL = (42, 63, 84)


def sweep(ticker, entries=GRID_ENTRY, tss=GRID_TS, rebals=GRID_REBAL, flat=0.0, gate=True,
          fee_in=None, fee_out=None):
    """Uji kepekaan: seluruh kombinasi ambang × trailing stop × jarak kalibrasi pada data yang sama.

    Skor walk-forward dihitung satu kali per `rebal` (bagian mahalnya), sisanya hanya mensimulasi
    ulang aturan masuk/keluar — jadi 45 kombinasi tetap selesai dalam hitungan detik.
    """
    df = load(ticker)
    sig, _ = build_signals(df)
    gs = index_gate() if gate else pd.Series(True, index=df.index)
    out = []
    for rebal in rebals:
        w = _sim_window(df, sig, rebal, gs)
        if not w:
            continue
        sim, c, atrn, g = w
        for e in entries:
            for ts in tss:
                pos, tr = simulate_trades(sim, c, atrn, g, e, flat, ts, fee_in=fee_in, fee_out=fee_out)
                p = perf(strategy_returns(pos, c, fee_in, fee_out), pos, c)
                out.append({"rebal": rebal, "entry": e, "ts": ts, "trades": len(tr),
                            "exposure": p["exposure"], "ret": p["ret"], "cagr": p["cagr"],
                            "dd": p["dd"], "sharpe": p["sharpe"], "bh_ret": p["bh_ret"],
                            "bh_dd": p["bh_dd"], "start": sim.index[0], "end": sim.index[-1]})
    return out


def sweep_verdict(rows, cur=None):
    """Kesimpulan grid: berapa banyak kombinasi yang masih lebih aman dari buy & hold."""
    if not rows:
        return {"n": 0}
    dd_win = [r for r in rows if r["dd"] > r["bh_dd"]]
    ret_win = [r for r in rows if r["ret"] > r["bh_ret"]]
    dds = sorted(r["dd"] for r in rows)
    med_dd = float(np.median(dds))
    beat = len(dd_win) / len(rows)
    verdict = "STABIL" if beat >= 0.8 else ("SEBAGIAN" if beat >= 0.5 else "RENTAN / OVERFIT")
    v = {"n": len(rows), "beat_dd": beat, "beat_ret": len(ret_win) / len(rows),
         "med_dd": med_dd, "best_dd": max(dds), "worst_dd": min(dds),
         "med_ret": float(np.median([r["ret"] for r in rows])),
         "med_sharpe": float(np.median([r["sharpe"] for r in rows])),
         "bh_dd": float(np.median([r["bh_dd"] for r in rows])),
         "bh_ret": float(np.median([r["bh_ret"] for r in rows])), "verdict": verdict}
    if cur:
        rank = sorted(rows, key=lambda r: -r["dd"])
        pos = next((i for i, r in enumerate(rank)
                    if (r["entry"], r["ts"], r["rebal"]) == (cur["entry"], cur["ts"], cur["rebal"])), None)
        v["cur_rank"] = (pos + 1) if pos is not None else None
        v["cur_n"] = len(rows)
    return v


def cmd_peka(ticker, cfg, entries=GRID_ENTRY, tss=GRID_TS, rebals=GRID_REBAL):
    slip = float(cfg.get("slip_pct", SLIP * 100))
    fi = (float(cfg.get("fee_buy_pct", FEE_BUY * 100)) + slip) / 100
    fo = (float(cfg.get("fee_sell_pct", FEE_SELL * 100)) + slip) / 100
    ui.header(f"UJI PEKA {ticker}", "grid ambang skor × trailing stop × jarak kalibrasi — "
              "kalau hasilnya hanya bagus di satu sel, itu overfit, bukan edge")
    rows = sweep(ticker, entries, tss, rebals, cfg["flat"], cfg["gate"], fi, fo)
    if not rows:
        raise SystemExit("History terlalu pendek untuk grid ini.")
    cur = {"entry": cfg["entry"], "ts": cfg["ts_mult"], "rebal": cfg["rebal"]}
    v = sweep_verdict(rows, cur)
    ui.table(["ENTRY", "TS×ATR", "REBAL", "TRADE", "EXPOSURE", "RETURN", "CAGR", "MAX DD",
              "SHARPE", "DD B&H", "NILAI"],
             [[f"{r['entry']:+.0f}", f"{r['ts']:.1f}", f"{r['rebal']}", f"{r['trades']}",
               f"{r['exposure']*100:.0f}%", _fmt_pct(r["ret"]), _fmt_pct(r["cagr"]),
               f"{r['dd']*100:.1f}%", f"{r['sharpe']:.2f}", f"{r['bh_dd']*100:.1f}%",
               ("[bold cyan]◄ konfigurasi aktif[/]" if (r["entry"], r["ts"], r["rebal"]) ==
                (cur["entry"], cur["ts"], cur["rebal"]) else "")]
              for r in sorted(rows, key=lambda r: -r["dd"])],
             caption=f"{rows[0]['start'].date()} → {rows[0]['end'].date()} · biaya dua sisi "
                     f"{(fi+fo)*100:.2f}% per putaran · diurut dari drawdown paling dangkal")
    warna = {"STABIL": "green", "SEBAGIAN": "yellow"}.get(v["verdict"], "red")
    ui.info_panel("KESIMPULAN GRID", [
        f"[{warna}]{v['verdict']}[/] dari [bold]{v['n']}[/] kombinasi:",
        f"· drawdown lebih dangkal dari buy & hold pada [bold]{v['beat_dd']*100:.0f}%[/] sel; "
        f"return lebih baik hanya pada [bold]{v['beat_ret']*100:.0f}%[/] sel.",
        f"· median drawdown [bold]{v['med_dd']*100:.1f}%[/] (terbaik {v['best_dd']*100:.1f}%, "
        f"terburuk {v['worst_dd']*100:.1f}%) vs buy & hold [dim]{v['bh_dd']*100:.1f}%[/].",
        f"· median return [bold]{v['med_ret']*100:+.1f}%[/] vs buy & hold [dim]{v['bh_ret']*100:+.1f}%[/], "
        f"median Sharpe [bold]{v['med_sharpe']:.2f}[/].",
        ("· [yellow]CATATAN:[/] 'STABIL' di sini berarti [bold]konsisten lebih kecil rugi[/], bukan "
         "konsisten untung — median return masih negatif, jadi grid ini mengukur ketahanan, bukan cuan."
         if v["med_ret"] < 0 else ""),
        (f"· Setelan aktif kamu (entry {cur['entry']:+.0f}, ts {cur['ts']}, rebal {cur['rebal']}) "
         f"peringkat [bold]{v['cur_rank']}[/] dari {v['cur_n']} paling aman."
         if v.get("cur_rank") else ""),
        "",
        "[grey62]Bacaan jujur: klaim 'aman' hanya berlaku kalau sebagian besar grid searah. Kalau "
        "hanya 1-2 sel yang bagus dan sisanya buruk, angka bagus di layar kamu adalah hasil "
        "pemilihan, bukan perilaku sistem.[/]"], warna)
    best = max(rows, key=lambda r: r["dd"])
    ui.info_panel("LANGKAH BERIKUT", [
        f"· Sel paling aman: entry [bold]{best['entry']:+.0f}[/], ts [bold]{best['ts']}[/], rebal "
        f"[bold]{best['rebal']}[/] → terapkan satu per satu: [bold]idx.py cfg set entry "
        f"{best['entry']:.0f}[/] · [bold]cfg set ts_mult {best['ts']}[/] · [bold]cfg set rebal "
        f"{best['rebal']}[/]",
        "· Jangan pakai sel terbaik hanya karena angkanya paling keren — pilih yang masih masuk akal "
        "untuk gaya tradingmu (lihat kolom EXPOSURE dan TRADE).",
        "· Bandingkan beberapa saham: [bold]idx.py backtest --semua[/] (modal bersama, slot, likuiditas).",
        "· Ulangi setelah data bertambah — grid ini sensitif terhadap periode yang diambil."], "cyan")


def portfolio_backtest(tickers, capital=100_000_000, risk_pct=1.0, max_positions=5,
                       entry=20.0, flat=0.0, ts_mult=2.5, rebal=63, gate=True,
                       max_pos_pct=MAX_POS_PCT, max_vol_pct=MAX_VOL_PCT,
                       fee_in=None, fee_out=None, progress=None):
    """Backtest portofolio: beberapa saham berebut satu modal lewat aturan ukuran posisi yang sama
    dengan mode `check` — risiko per posisi, porsi maksimum, dan cap likuiditas nilai harian.

    Dua tahap: (1) daftar transaksi tiap saham dihasilkan dari skornya masing-masing,
    (2) penjadwal menjalankan transaksi itu secara berurutan selama slot dan uang masih ada.
    """
    fi, fo = _side_costs(fee_in, fee_out)
    gs = index_gate() if gate else None
    legs, per = {}, {}
    dates = None
    for i, t in enumerate(tickers):
        if progress:
            progress(f"skor walk-forward {t}", i + 1, len(tickers))
        try:
            df = load(t)
        except SystemExit:
            continue
        sig, _ = build_signals(df)
        w = _sim_window(df, sig, rebal, gs if gate else pd.Series(True, index=df.index))
        if not w:
            continue
        sim, c, atrn, g = w
        pos, tr = simulate_trades(sim, c, atrn, g, entry, flat, ts_mult, fee_in=fi, fee_out=fo)
        legs[t] = {"c": c, "pos": pos, "trades": tr, "adv": adv_rupiah(df).reindex(c.index)}
        idx = set(c.index)
        dates = idx if dates is None else (dates & idx)
    if progress:
        progress(None, 1, 1)
    if not legs or not dates:
        raise SystemExit("Tidak ada irisan tanggal yang cukup antar saham — kurangi daftarnya.")
    dates = pd.DatetimeIndex(sorted(dates))
    if len(dates) < 120:
        raise SystemExit(f"Irisan tanggal terlalu pendek ({len(dates)} bar) untuk backtest portofolio.")

    entries_by, exits_by = {}, {}
    dateset = set(dates)
    for t, L in legs.items():
        for tr in L["trades"]:
            if tr["date_in"] in dateset:
                entries_by.setdefault(tr["date_in"], []).append((t, tr))
            if not tr["open"] and tr["date_out"] in dateset:
                exits_by.setdefault(tr["date_out"], []).append((t, tr))
    px = pd.DataFrame({t: L["c"].reindex(dates) for t, L in legs.items()}).ffill()

    cash, open_pos, curve = float(capital), {}, []
    taken = rejected = 0
    cap_slot = cap_pos = cap_liq = cap_cash = 0
    realized = []
    fees_paid = 0.0
    bars_in_market = 0
    for d in dates:
        for t, tr in exits_by.get(d, []):
            o = open_pos.pop(t, None)
            if not o:
                continue
            sell_px = float(px.at[d, t])
            fees_paid += o["shares"] * sell_px * fo
            proceeds = o["shares"] * sell_px * (1 - fo)
            cash += proceeds
            realized.append({"ticker": t, "in": tr["date_in"], "out": d, "hold": tr["hold"],
                             "ret": proceeds / o["cost"] - 1, "reason": tr["reason"],
                             "notional": o["cost"]})
        for t, tr in entries_by.get(d, []):
            if t in open_pos:
                continue
            if len(open_pos) >= max_positions:
                cap_slot += 1
                rejected += 1
                continue
            buy_px = float(px.at[d, t])
            equity_now = curve[-1] if curve else cash
            stop_frac = max(tr["risk_frac"], 1e-4)
            # risiko per posisi membatasi jumlah lembar; porsi maksimum & likuiditas membatasi nilai
            shares = (equity_now * risk_pct / 100.0) / (buy_px * stop_frac)
            adv = legs[t]["adv"].get(d, np.nan)
            liq = max_vol_pct * float(adv) if adv and adv == adv else np.inf
            budget = min(cash, equity_now * max_pos_pct)
            limit = min(budget, liq)
            if shares * buy_px > limit:
                if limit == liq:
                    cap_liq += 1
                else:
                    cap_pos += 1
                shares = limit / buy_px
            shares = int(shares // 100 * 100)
            cost = shares * buy_px * (1 + fi)
            if shares < 100 or cost > cash:
                cap_cash += 1
                rejected += 1
                continue
            fees_paid += shares * buy_px * fi
            cash -= cost
            open_pos[t] = {"shares": shares, "cost": cost}
            taken += 1
        mv = sum(o["shares"] * float(px.at[d, t]) for t, o in open_pos.items())
        if open_pos:
            bars_in_market += 1
        curve.append(cash + mv)
    eq = pd.Series(curve, index=dates)
    strat = eq.pct_change().fillna(0)
    bh = px.div(px.iloc[0]).mean(axis=1)
    yrs = max(len(dates) / 252, 1e-9)
    out = {"dates": dates, "eq": eq, "bh": bh, "yrs": yrs, "legs": len(legs),
           "tickers": list(legs), "trades": realized, "taken": taken, "rejected": rejected,
           "cap_slot": cap_slot, "cap_pos": cap_pos, "cap_liq": cap_liq, "cap_cash": cap_cash,
           "fees": fees_paid, "capital": capital,
           "ret": float(eq.iloc[-1] / capital - 1),
           "cagr": float((eq.iloc[-1] / capital) ** (1 / yrs) - 1),
           "dd": float((eq / eq.cummax() - 1).min()),
           "sharpe": float(strat.mean() / (strat.std() or 1) * np.sqrt(252)),
           "exposure": bars_in_market / len(dates),
           "bh_ret": float(bh.iloc[-1] - 1), "bh_dd": float((bh / bh.cummax() - 1).min())}
    try:
        ihsg = yf.Ticker("^JKSE").history(start=dates[0], end=dates[-1])["Close"].dropna()
        if len(ihsg) > 30:
            out["ihsg_ret"] = float(ihsg.iloc[-1] / ihsg.iloc[0] - 1)
            out["ihsg_dd"] = float((ihsg / ihsg.cummax() - 1).min())
    except Exception:  # noqa: BLE001
        pass
    return out


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
           horizon=None, max_pos_pct=0.20, with_live=False, flat=0.0):
    """Angka keputusan satu saham dalam dict — sumber skornya `snapshot()`, sama seperti scanner."""
    s = snapshot(ticker, rebal, horizon, with_live=with_live, entry=entry, flat=flat, ts_mult=ts_mult)
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
              "flat": flat, "ts_mult": ts_mult,
              "bullish": final >= 5, "risk_per_share": R, "stop": pxl - R, "zone_lo": pxl - a,
              "t1": pxl + 1.5 * R, "t2": pxl + 3 * R, "qty": qty, "max_pos_pct": max_pos_pct,
              "capped_20pct": qty == max_qty and qty > 0, "capital": capital, "risk_pct": risk_pct,
              "holding_sim": s["holding"], "sn": s})
    return d

def cmd_decide(ticker, capital=100_000_000, risk_pct=1.0, entry=20.0, ts_mult=2.5, rebal=63):
    d = decide(ticker, capital, risk_pct, entry=entry, ts_mult=ts_mult, rebal=rebal)
    render_decision(d)

def basis_note(d):
    """Baris yang menjelaskan bar mana yang menghasilkan skor dan harga mana yang dipakai mengukur posisi."""
    lp, bc = d.get("live_px"), d.get("bar_close", d["close"])
    if lp is not None and abs(lp - bc) > 0.5:
        return (f"skor dari bar [bold]{d['date']}[/] (harga {bc:,.0f}) · stop/target/lot dari harga live "
                f"[bold]{lp:,.0f}[/] ({(lp/bc-1)*100:+.1f}%)")
    if d.get("live_ahead"):
        return (f"[yellow]kutipan live lebih baru dari bar terakhir — skor masih dari {d['date']}, "
                "indikator belum melihat hari ini[/]")
    return f"skor & harga dari bar harian yang sama ({d['date']}) — identik dengan tabel scanner"


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
    entry, flat, tsm = d.get("entry", 20.0), d.get("flat", 0.0), d.get("ts_mult", 2.5)
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
    ev.append(f"Basis angka ............. {basis_note(d)}")
    ui.info_panel("EVIDEN", ev, "cyan")

    if d["bullish"]:
        plan = [
            f"Zona entry ideal : [green]{pxl - a:,.0f} – {pxl:,.0f}[/]  (pullback 1 ATR s/d harga kini)",
            f"Stop awal        : [red]{stop:,.0f}[/]  ({tsm}×ATR, risiko per lembar ≈ {R:,.0f})",
            f"Target 1 (+1.5R) : [bold]{t1:,.0f}[/] → ambil 50% profit, geser stop ke harga modal",
            f"Target 2 (+3R)   : [bold]{t2:,.0f}[/] → sisanya biarkan lari dengan trailing stop "
            f"{tsm}×ATR dari puncak",
        ]
        if st["n"]:
            plan.append(f"Durasi realistis : ~[bold]{st['median_hold']:.0f}[/] hari bursa (median historik); "
                        f"keluar lebih awal jika skor akhir jatuh ≤ [red]{flat:+.0f}[/].")
        ui.info_panel("RENCANA TRANSAKSI (acuan jika Anda memutuskan masuk)", plan, "green")
    else:
        sudah = final <= flat
        ui.info_panel("RENCANA (posisi baru ditolak — ini aturan keluar)", [
            "[bold]Sistem tidak membuka posisi baru di skor ini.[/]",
            f"Aturan keluar yang berlaku: skor akhir ≤ [red]{flat:+.0f}[/] "
            + (f"([bold]SUDAH TERPENUHI[/] — skor kini {final:+.1f}); "
               if sudah else f"(belum tersentuh — skor kini {final:+.1f}); ")
            + f"trailing stop [red]{stop:,.0f}[/] ({tsm}×ATR dari harga kini) — "
            "di `pos`/`brief` baris ini memakai angka yang sama.",
            f"Jika masih memegang: [red]KURANGI/JUAL[/] bertahap dan patuhi stop; menunda tidak "
            "mengubah verdik.",
            f"Ubah keputusan jadi BELI hanya jika: skor akhir naik ke ≥ [green]{entry:+.0f}[/] [bold]dan[/] "
            "IHSG kembali di atas EMA200 (gate terbuka).",
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
        _decision_legend(entry, flat, tsm)

def _decision_legend(entry=20.0, flat=0.0, ts_mult=2.5):
    ui.info_panel("CARA BACA & ATURAN MAIN", [
        f"· [bold]Keputusan[/] datang dari SKOR AKHIR, bukan dari satu indikator. Ambang yang berlaku: "
        f"≥ +35 BELI KUAT, ≥ {entry:+.0f} BELI (hanya jika gate terbuka), +5…{entry:+.0f} WATCHLIST, "
        f"−5…+5 HOLD/TUNGGU, ≤ {flat:+.0f} keluar.",
        "· [bold]Gate indeks tertutup[/] = secara historis IHSG di bawah EMA200; di rezim itu breakout lebih "
        "sering gagal, jadi skor kuat sekalipun hanya boleh jadi posisi kecil ([yellow]SIAGA[/]).",
        f"· [bold]Median hold[/] adalah perkiraan waktu, bukan janji — keluar lebih awal bila skor jatuh ≤ "
        f"{flat:+.0f} atau trailing stop {ts_mult}×ATR tersentuh. Jangan menahan posisi yang sudah kena stop.",
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
