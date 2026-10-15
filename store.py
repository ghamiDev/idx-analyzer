"""Lapisan penyimpanan: config, riwayat skor harian, dan laporan markdown."""

import csv
import datetime as dt
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "config.json")
HIST_FILE = os.path.join(HERE, "history.csv")
REPORT_DIR = os.path.join(HERE, "reports")

DEFAULTS = {
    "watchlist": None,            # None = pakai daftar bawaan idx_analyzer.LQ45
    "capital": 100_000_000,       # rupiah
    "risk_pct": 1.0,              # % modal yang dipertaruhkan per posisi (jarak ke stop)
    "max_total_risk_pct": 3.0,    # batas risiko agregat semua posisi terbuka
    "max_positions": 5,
    "entry": 20.0,                # ambang skor utk BELI
    "flat": 0.0,                  # skor <= ini = keluar (setelah min hold)
    "ts_mult": 2.5,               # trailing stop dalam ATR
    "gate": True,                 # syarat IHSG > EMA200
    "top": 15,                    # jumlah baris scanner
    "rebal": 63,                  # rekalibrasi bobot walk-forward tiap N bar
    "week_horizon": 5,            # hari bursa untuk mode `minggu`
    "week_risk_pct": 5.0,         # % modal yang boleh hilang per trade mingguan
    "week_max_pos_pct": 0.9,      # porsi maksimum 1 posisi (modal kecil tidak terikat 20%)
    "week_news_days": 14,         # jendela hari untuk berita per saham
    # --- biaya nyata (dipakai backtest / peka / portofolio) ---
    "fee_buy_pct": 0.15,          # % fee broker sisi beli
    "fee_sell_pct": 0.25,         # % fee broker sisi jual (PPh final 0,1% biasanya di sini)
    "slip_pct": 0.05,             # % slippage per sisi
    "max_pos_pct": 20.0,          # porsi maksimum 1 posisi pada backtest portofolio
    "max_volume_pct": 1.0,        # tidak boleh masuk di atas N% nilai transaksi harian saham itu
}


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({k: cfg[k] for k in DEFAULTS}, f, indent=2, ensure_ascii=False)
    return CONFIG_FILE


_CAST = {"capital": int, "risk_pct": float, "max_total_risk_pct": float, "max_positions": int,
         "entry": float, "flat": float, "ts_mult": float, "top": int, "rebal": int,
         "week_horizon": int, "week_risk_pct": float, "week_max_pos_pct": float,
         "week_news_days": int, "fee_buy_pct": float, "fee_sell_pct": float, "slip_pct": float,
         "max_pos_pct": float, "max_volume_pct": float}
_BOOL = {"gate"}


def set_config(cfg, key, value):
    if key not in DEFAULTS:
        raise KeyError(key)
    if key in _BOOL:
        cfg[key] = str(value).lower() in ("1", "true", "ya", "y", "on", "buka")
    elif key == "watchlist":
        cfg[key] = None if str(value).lower() in ("", "none", "default") else [
            t.strip().upper() for t in str(value).split(",") if t.strip()]
    else:
        cfg[key] = _CAST[key](value)
    return cfg[key]


HISTORY_COLS = ["date", "ticker", "raw", "final", "regime", "close"]


def append_history(rows, date=None):
    """rows: hasil compute_scan (list dict). Satu skor per (tanggal, ticker) — hitung ulang
    di hari yang sama mengganti yang lama, bukan menumpuk baris kembar."""
    date = date or dt.date.today().isoformat()
    old = [r for r in read_history() if not (r["date"] == date and any(
        r["ticker"] == x["ticker"] for x in rows))]
    os.makedirs(os.path.dirname(HIST_FILE) or ".", exist_ok=True)
    with open(HIST_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HISTORY_COLS)
        for r in old:
            w.writerow([r["date"], r["ticker"], f"{r['raw']:.2f}", f"{r['final']:.2f}",
                        r["regime"], f"{r['close']:.0f}"])
        for r in rows:
            w.writerow([date, r["ticker"], f"{r['raw']:.2f}", f"{r['final']:.2f}",
                        r["regime"], f"{r['close']:.0f}"])
    return HIST_FILE


def read_history(ticker=None, last_n_days=None):
    if not os.path.exists(HIST_FILE):
        return []
    with open(HIST_FILE, newline="", encoding="utf-8") as f:
        rows = [dict(zip(HISTORY_COLS, r)) for r in csv.reader(f) if r][1:]
    for r in rows:
        r["raw"], r["final"], r["close"] = float(r["raw"]), float(r["final"]), float(r["close"])
    if ticker:
        rows = [r for r in rows if r["ticker"] == ticker]
    if last_n_days:
        days = sorted({r["date"] for r in rows})[-last_n_days:]
        rows = [r for r in rows if r["date"] in days]
    return rows


def previous_scores(date=None):
    """Skor per ticker pada tanggal terakhir sebelum `date` — untuk menghitung delta."""
    rows = read_history()
    if not rows:
        return {}
    days = sorted({r["date"] for r in rows})
    if date and date in days:
        days = [d for d in days if d < date]
    elif not date:
        days = days[:-1]
    if not days:
        return {}
    d = days[-1]
    return {r["ticker"]: r["final"] for r in rows if r["date"] == d}


def write_report(markdown_text, date=None):
    os.makedirs(REPORT_DIR, exist_ok=True)
    date = date or dt.date.today().isoformat()
    p = os.path.join(REPORT_DIR, f"brief-{date}.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(markdown_text)
    return p
