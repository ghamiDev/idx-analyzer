"""Pencatat posisi & money management multi-posisi, terhubung ke mesin idx_analyzer.

data/positions.json  : posisi terbuka
data/trades.jsonl    : riwayat (tiap tambah/kurang/keluar dicatat)
"""

import datetime as dt
import json
import os

import idx_analyzer as eng
import store

HERE = os.path.dirname(os.path.abspath(__file__))
POS_FILE = os.path.join(HERE, "data", "positions.json")
TRADES_FILE = os.path.join(HERE, "data", "trades.jsonl")
LOT = 100  # 1 lot IDX = 100 lembar


def _load():
    if os.path.exists(POS_FILE):
        with open(POS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(p):
    os.makedirs(os.path.dirname(POS_FILE), exist_ok=True)
    with open(POS_FILE, "w", encoding="utf-8") as f:
        json.dump(p, f, indent=2, ensure_ascii=False)


def _log(event, **kw):
    os.makedirs(os.path.dirname(TRADES_FILE), exist_ok=True)
    rec = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "event": event}
    rec.update(kw)
    with open(TRADES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def normalize(t):
    t = t.upper().replace(".JK", "")
    return t if t.startswith("^") else f"{t}.JK"


def add(ticker, lots=None, price=None, note="", cfg=None, d=None):
    """Catat posisi baru. Harga/stop/target default diambil dari mesin keputusan."""
    cfg = cfg or store.load_config()
    t = normalize(ticker)
    if d is None:
        d = eng.decide(t, cfg["capital"], cfg["risk_pct"], entry=cfg["entry"],
                       ts_mult=cfg["ts_mult"], rebal=cfg.get("rebal", 63), flat=cfg.get("flat", 0.0))
    px = float(price or d["close"])
    qty = (lots * LOT) if lots else d["qty"]
    if qty < LOT:
        raise SystemExit(f"Tidak ada ukuran yang layak (risiko {cfg['risk_pct']}% dari "
                         f"{cfg['capital']:,.0f} tidak cukup untuk 1 lot di stop {d['stop']:,.0f}).")
    p = _load()
    cur = p.get(t, {"lots": 0, "avg": px})
    new_lots = cur["lots"] + qty // LOT
    avg = (cur["lots"] * cur["avg"] + (qty // LOT) * px) / new_lots if new_lots else px
    R = cfg["ts_mult"] * (px * d["atr_pct"] / 100)
    p[t] = {"lots": new_lots, "avg": round(avg, 2), "entry_date": cur.get("entry_date", str(dt.date.today())),
            "stop_initial": round(px - R, 2), "t1": round(px + 1.5 * R, 2), "t2": round(px + 3 * R, 2),
            "score_at_entry": round(d["final"], 1), "note": note or cur.get("note", "")}
    _save(p)
    _log("add", ticker=t, lots=qty // LOT, price=px, avg=round(avg, 2), score=round(d["final"], 1))
    return p[t], qty, R


def reduce_or_close(ticker, lots=None, price=None):
    """Lots kosong = keluar semua."""
    cfg = store.load_config()
    t = normalize(ticker)
    p = _load()
    if t not in p:
        raise SystemExit(f"{t} tidak ada di positions.json.")
    px = float(price if price is not None else eng.load(t).Close.iloc[-1])
    pos = p[t]
    exit_lots = pos["lots"] if lots is None else min(int(lots), pos["lots"])
    left = pos["lots"] - exit_lots
    pnl = (px - pos["avg"]) * exit_lots * LOT
    pnl_pct = (px / pos["avg"] - 1) * 100
    if left > 0:
        pos["lots"] = left
    else:
        del p[t]
    _save(p)
    _log("close" if left == 0 else "reduce", ticker=t, lots=exit_lots, price=px,
         pnl=round(pnl), pnl_pct=round(pnl_pct, 2))
    return {"ticker": t, "lots_out": exit_lots, "price": px, "pnl": pnl, "pnl_pct": pnl_pct,
            "lots_left": left, "capital_pct": pnl / cfg["capital"] * 100}


def status(ticker, pos, cfg, d=None, ms=None):
    """Kondisi posisi: P&L, jarak stop/target, apakah aturan keluar sudah memicu."""
    t = normalize(ticker)
    d = d or eng.decide(t, cfg["capital"], cfg["risk_pct"], entry=cfg["entry"], ts_mult=cfg["ts_mult"],
                         ms=ms, rebal=cfg.get("rebal", 63), with_live=True, flat=cfg.get("flat", 0.0))
    df = d["df"]  # muat sekali: decide() sudah bawa DataFrame dari snapshot
    px = float(df.Close.iloc[-1])
    daily_px, live_px = px, None
    q = d.get("live") or {}
    # P&L dipantau dari kutipan live kalau sumber sudah mencetak trade hari ini
    if q.get("price") and not q.get("error") and q.get("time") and q["time"].date() >= df.index[-1].date():
        px, live_px = float(q["price"]), float(q["price"])
    entry_date = pos.get("entry_date", str(df.index[-1].date()))
    since = df.loc[df.index >= dt.datetime.strptime(entry_date, "%Y-%m-%d")]
    bars_held = int(len(since)) or 1
    peak = float(since.Close.max()) if len(since) else px
    a = d["atr"]
    atr_pct = d["atr_pct"]
    stop = float(pos["stop_initial"])
    trail = peak - cfg["ts_mult"] * a
    eff_stop = max(stop, trail)
    lots = pos["lots"]
    qty = lots * LOT
    pnl = (px - pos["avg"]) * qty
    out = {
        "ticker": t, "lots": lots, "avg": pos["avg"], "px": px, "pnl": pnl,
        "px_daily": daily_px, "px_live": live_px, "live": q or None,
        "pnl_pct": (px / pos["avg"] - 1) * 100, "bars_held": bars_held,
        "risk_now": qty * max(px - eff_stop, 0), "capital": cfg["capital"],
        "stop_initial": stop, "trail": trail, "eff_stop": eff_stop, "peak": peak,
        "dist_stop_pct": (px / eff_stop - 1) * 100, "t1": pos["t1"], "t2": pos["t2"],
        "score": d["final"], "raw": d["raw"], "act": d["act"], "gate": d["gate"],
        "mood": d["mood"], "atr_pct": atr_pct, "note": pos.get("note", ""),
    }
    why = []
    if px <= eff_stop:
        why.append("harga ≤ stop/trailing")
    if d["final"] <= cfg["flat"] and bars_held >= 5:
        why.append(f"skor {d['final']:+.0f} ≤ {cfg['flat']:+.0f}")
    if d["final"] <= -20:
        why.append("skor akhir bearish berat")
    out["exit_now"] = bool(why)
    out["exit_reason"] = ", ".join(why)
    # satu kosakata aksi untuk tabel terminal maupun laporan markdown
    out["action"] = ("KELUAR" if out["exit_now"] else "KURANGI" if d["final"] <= -15
                     else "PEGANG / TAMBAH" if d["final"] >= cfg["entry"] else "PEGANG")
    if px >= pos["t2"]:
        out["note_out"] = f"Target 2 tercapai — trailing {cfg['ts_mult']}×ATR satu-satunya rem."
    elif px >= pos["t1"] and pos.get("t1_done"):
        out["note_out"] = "Target 1 sudah diambil."
    elif px >= pos["t1"]:
        out["note_out"] = "Target 1 tercapai → ambil 50%, geser stop ke harga modal."
    else:
        out["note_out"] = ""
    return out


def recent_trades(n=12):
    """N catatan tambah/kurang/keluar terakhir dari data/trades.jsonl."""
    if not os.path.exists(TRADES_FILE):
        return []
    with open(TRADES_FILE, encoding="utf-8") as f:
        recs = [json.loads(line) for line in f if line.strip()]
    return recs[-n:]


def budget(positions_status, cfg):
    """Risiko agregat posisi aktif vs batas risiko portofolio."""
    used = sum(p["risk_now"] for p in positions_status)
    cap_risk = cfg["capital"] * cfg["max_total_risk_pct"] / 100
    per = cfg["capital"] * cfg["risk_pct"] / 100
    room = max(cap_risk - used, 0.0)
    slots = max(cfg["max_positions"] - len(positions_status), 0)
    return {"used": used, "cap": cap_risk, "room": room, "per_position": per,
            "can_add": int(room // per) if per else 0, "slots": slots,
            "used_pct": used / cfg["capital"] * 100, "cap_pct": cfg["max_total_risk_pct"]}
