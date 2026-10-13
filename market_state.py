"""Modul kondisi pasar + geopolitik untuk idx_analyzer (breadth, makro global, sentimen berita)."""

import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import yfinance as yf

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".market_state_cache.json")
CACHE_TTL = 1800

MAKRO = {"Brent": "BZ=F", "Emas": "GC=F", "DXY": "DX-Y.NYB", "USD/IDR": "IDR=X", "IHSG": "^JKSE"}

FEEDS = [
    "https://news.google.com/rss/search?q=IHSG+OR+saham+Indonesia+when:2d&hl=id&gl=ID&ceid=ID:id",
    "https://news.google.com/rss/search?q=geopolitik+OR+perang+OR+sanksi+OR+The+Fed+OR+rupiah+when:3d&hl=id&gl=ID&ceid=ID:id",
    "https://www.cnbcindonesia.com/rss",
    "https://www.republika.co.id/rss",
]

# (pola regex, bobot risiko, tema) — negatif = meredakan risiko
RISK_PATTERNS = [
    (r"perang|\bwar\b|serangan|strike|rudal|roket|militer|konflik|conflict|eskalasi", 3.0, "geopolitik"),
    (r"sanksi|sanction|embargo|tarif|tariff", 2.0, "geopolitik"),
    (r"teroris|terroris|bom\b|ledakan|bomb", 3.0, "keamanan"),
    (r"demonstrasi|demo\b|protes|ricuh|anarkis|makar|impeachment|pemakzulan|krisis politik", 2.5, "politik domestik"),
    (r"pilkada|pemilu|polling|electability", 1.0, "politik domestik"),
    (r"outflow|net ?sell asing|jual bersih|foreign outflow|asing (jual|melepas)", 2.0, "arus modal"),
    (r"rupiah melemah|rupiah tertekan|rupiah (?:turun|anjlok)|cadangan devisa turun", 1.5, "valas"),
    (r"suku bunga naik|rate hike|the fed (?:naik|kerek)|yield (?:naik|melompat)|inflasi (?:naik|tinggi)", 1.5, "moneter"),
    (r"resesi|recession|crash|anjlok|jatuh|tekor|auto reject|penghentian sementara|trading halt", 1.0, "sentimen pasar"),
    (r"msci|ftse|downgrade|outlook negatif|peringatan|warning|manipulasi", 1.0, "reputasi pasar"),
    (r"gencatan senjata|ceasefire|de.?eskalasi|perdamaian|kesepakatan damai", -3.0, "geopolitik"),
    (r"inflow|net ?buy asing|borong|asing (?:akumulasi|beli)|rupiah menguat", -1.5, "arus modal"),
    (r"rate cut|pemangkasan suku bunga|suku bunga turun|inflasi (?:terkendali|turun)", -1.0, "moneter"),
    (r"surplus|rekor|tertinggi|stabil|penguatan", -0.5, "sentimen pasar"),
]

SEKTOR_PATTERNS = {
    "Energi/Batubara": r"batubara|coal|energi|ipco|lbm|Harga Gas|HGA|DMO",
    "CPO/Agri": r"sawit|cpo|b50|b40|agro",
    "Perbankan": r"suku bunga|bi rate|lfl|nim|credit crunch|perbankan|bank ",
    "Komoditas logam": r"nikel|nickel|emas|gold|tembaga|copper|hsm|steel",
    "Infra/Telko": r"menara|tower|telko|data center|infra",
}

SECTOR_OF = {
    "BBCA": "Perbankan", "BBRI": "Perbankan", "BMRI": "Perbankan", "BBNI": "Perbankan", "BRIS": "Perbankan",
    "TLKM": "Infra/Telko", "ISAT": "Infra/Telko", "TOWR": "Infra/Telko", "JSMR": "Infra/Telko", "META": "Infra/Telko",
    "ADRO": "Energi/Batubara", "PTBA": "Energi/Batubara", "ITMG": "Energi/Batubara", "BYAN": "Energi/Batubara",
    "DOID": "Energi/Batubara", "BREN": "Energi/Batubara", "PGAS": "Energi/Batubara", "MEDC": "Energi/Batubara",
    "ELSA": "Energi/Batubara", "AKRA": "Energi/Batubara", "ICBP": "CPO/Agri", "CPIN": "CPO/Agri", "SIDO": "CPO/Agri",
    "AALI": "CPO/Agri", "LSIP": "CPO/Agri", "TINS": "Komoditas logam", "ANTM": "Komoditas logam", "MDKA": "Komoditas logam",
    "MBMA": "Komoditas logam", "PSAB": "Komoditas logam", "NCKL": "Komoditas logam", "ARCI": "Komoditas logam",
    "AMMN": "Komoditas logam", "CUAN": "Komoditas logam", "BRMS": "Komoditas logam", "SMGR": "Komoditas logam",
    "KLBF": "CPO/Agri", "UNVR": "CPO/Agri", "MYOR": "CPO/Agri", "AMRT": "CPO/Agri", "INDF": "CPO/Agri",
}

def sector_penalty(ticker, state):
    """Penalti (negatif) jika sektor emiten sedang berisiko menurut berita."""
    n = state.get("news")
    if not n or n["risk"] <= 0:
        return 0.0
    sec = SECTOR_OF.get(ticker.upper().replace(".JK", ""))
    if sec and sec in n.get("sektor_panas", []):
        return float(-np.clip(n["risk"] * 6, 0, 8))
    return 0.0

def _chg(series, n=20):
    s = series.dropna()
    if len(s) < n + 1:
        return np.nan
    return float((s.iloc[-1] / s.iloc[-1 - n] - 1) * 100)

# -------------------------------------------------- makro & indeks

def macro_block():
    out = {}
    for name, t in MAKRO.items():
        try:
            h = yf.Ticker(t).history(period="6mo", interval="1d")["Close"].dropna()
            ret = np.log(h / h.shift()).dropna()
            out[name] = {"last": float(h.iloc[-1]), "chg5": _chg(h, 5), "chg20": _chg(h, 20),
                         "hv20": float(ret.tail(20).std() * np.sqrt(252) * 100)}
        except Exception:
            out[name] = None
    # kutipan live IHSG: bar harian bisa tertinggal/masih separuh jalan, ini pembandingnya
    try:
        import live
        q = live.quote("^JKSE")
        if not q.get("error") and q["price"]:
            out["IHSG_live"] = {"last": q["price"], "prev": q["prev_close"],
                                "time": q["time"].isoformat() if q["time"] else None,
                                "session": q["session"], "high": q["day_high"], "low": q["day_low"],
                                "age_min": q["age_min"], "stale": q.get("stale"),
                                "chg1": (q["price"] / q["prev_close"] - 1) * 100 if q["prev_close"] else np.nan,
                                "daily_last": (out["IHSG"] or {}).get("last")}
            if out["IHSG"]:
                out["IHSG"]["last_daily"] = out["IHSG"]["last"]
                out["IHSG"]["last"] = q["price"]
    except Exception:
        pass
    # skor risk-on/off: dolar kuat & rupiah melemah & Brent melonjak = risiko utk IDX
    def sc(x, lo, hi):
        return np.nan if x is None or np.isnan(x) else np.clip((hi - x) / (hi - lo), 0, 1) - 0.5
    parts = []
    if out.get("DXY"): parts.append(sc(out["DXY"]["chg20"], 0.0, 6.0))
    if out.get("USD/IDR"): parts.append(sc(out["USD/IDR"]["chg20"], -2.0, 6.0))
    if out.get("Brent"): parts.append(sc(out["Brent"]["chg20"], -5.0, 20.0))
    if out.get("IHSG"): parts.append(sc(-out["IHSG"]["chg20"], -10.0, 10.0))
    risk = float(np.nanmean(parts)) if parts else 0.0
    out["_risk_score"] = round(risk, 3)  # + = risk-on, - = risk-off
    return out

# -------------------------------------------------- breadth

def breadth_block(tickers):
    above_ema200 = above_ema50 = counted = 0
    for t in tickers:
        try:
            c = yf.Ticker(t).history(period="1y", interval="1d")["Close"].dropna()
            if len(c) < 60:
                continue
            counted += 1
            e200 = c.ewm(span=200, adjust=False).mean()
            e50 = c.ewm(span=50, adjust=False).mean()
            above_ema200 += int(c.iloc[-1] > e200.iloc[-1]) if len(c) >= 200 else 0
            above_ema50 += int(c.iloc[-1] > e50.iloc[-1])
        except Exception:
            pass
    if not counted:
        return None
    return {"n": counted, "pct>EMA50": above_ema50 / counted,
            "pct>EMA200": (above_ema200 / counted) if counted >= 20 else np.nan,
            "skor": (above_ema50 / counted - 0.5) * 30}

# -------------------------------------------------- berita

def _fetch_rss(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    body = urllib.request.urlopen(req, timeout=15).read()
    items = []
    try:
        root = ET.fromstring(body)
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            if title:
                items.append(title)
    except ET.ParseError:
        items = re.findall(r"<title[^>]*>(.{10,200}?)</title>", body.decode("utf8", "ignore"))
    return items[:80]

def news_block():
    titles = []
    seen = set()
    for u in FEEDS:
        try:
            for t in _fetch_rss(u):
                key = t.lower()[:60]
                if key not in seen and "google berita" not in key and "rss" not in key:
                    seen.add(key)
                    titles.append(t)
        except Exception:
            continue
    if not titles:
        return None
    total, per_tema, top_hits = 0.0, {}, []
    for t in titles:
        low = t.lower()
        w = 0.0
        hits = []
        for pat, bw, tema in RISK_PATTERNS:
            if re.search(pat, low):
                w += bw
                hits.append(tema)
                per_tema[tema] = per_tema.get(tema, 0) + bw
        w = np.clip(w, -6, 6)
        if abs(w) >= 2:
            top_hits.append((w, t[:90]))
        total += w
    risk = float(total / len(titles))  # positif = berisiko
    sektor = {s: r for s, r in SEKTOR_PATTERNS.items() if sum(1 for t in titles if re.search(r, t.lower())) >= 3}
    return {"n": len(titles), "risk": round(risk, 2),
            "skor": float(np.clip(-risk * 15, -15, 15)),
            "tema": per_tema, "sektor_panas": list(sektor),
            "top": sorted(top_hits, key=lambda x: x[0], reverse=True)[:5]}

# -------------------------------------------------- agregasi

def get_market_state(tickers, use_cache=True):
    if use_cache and os.path.exists(CACHE_FILE):
        try:
            c = json.load(open(CACHE_FILE, encoding="utf8"))
            if time.time() - c["ts"] < CACHE_TTL:
                return c["state"]
        except Exception:
            pass
    mac, bre, news = macro_block(), breadth_block(tickers), news_block()
    adjust = float(np.clip(
        (news["skor"] if news else 0) + (bre["skor"] if bre else 0) + mac["_risk_score"] * 10, -30, 30))
    mood = ("SANGAT BERISIKO" if adjust <= -15 else "BERISIKO" if adjust <= -5
            else "MENGIJINKAN" if adjust >= 10 else "NETRAL")
    state = {"macro": mac, "breadth": bre, "news": news, "adjust": round(adjust, 1), "mood": mood,
             "ts": time.strftime("%Y-%m-%d %H:%M")}
    try:
        json.dump({"ts": time.time(), "state": state}, open(CACHE_FILE, "w", encoding="utf8"))
    except Exception:
        pass
    return state

def print_panel(state):
    try:
        import ui
    except ImportError:
        ui = None
    if ui is None:
        return _print_panel_plain(state)
    m, b, n = state["macro"], state["breadth"], state["news"]
    ui.header(f"KONDISI PASAR GLOBAL & POLITIK  ({state['ts']})",
              f"Mood pasar: {ui.mood_markup(state['mood'])}   |   "
              f"Penyesuaian skor saham: [bold][{'green' if state['adjust'] >= 0 else 'red'}]"
              f"{state['adjust']:+.1f}[/][/]")
    rows = []
    for k in ["IHSG", "Brent", "Emas", "DXY", "USD/IDR"]:
        if m.get(k):
            d = m[k]
            rows.append([k, f"{d['last']:>12,.2f}",
                         f"[{'green' if d['chg20'] >= 0 else 'red'}]{d['chg20']:+6.2f}%[/]",
                         f"[{'red' if d['hv20'] > 25 else 'green'}]{d['hv20']:5.1f}%[/]"])
    lv = m.get("IHSG_live")
    if lv:
        try:
            import datetime as _dt
            t = _dt.datetime.fromisoformat(lv["time"]) if lv.get("time") else None
            jam = t.strftime("%H:%M") if t else "?"
            colj = "green" if not lv.get("stale") else "yellow"
            rows.append(["IHSG live", f"{lv['last']:>12,.2f}",
                         f"[{'green' if (lv['chg1'] or 0) >= 0 else 'red'}]{lv['chg1']:+6.2f}%[/]",
                         f"[{colj}]{jam} · {round(lv['age_min'] or 0)} mnt[/]"])
        except Exception:
            pass
    if rows:
        ui.table(["INDEKS MAKRO", "level", "20 hari", "HV20 / jam"], rows)
    lines = []
    if lv:
        _baris = f"· IHSG live {lv['last']:,.0f} @ {lv['session']}"
        if lv.get("daily_last") and abs(lv["last"] - lv["daily_last"]) / lv["daily_last"] > 0.001:
            _baris += (f" — bar harian yang dipakai indikator masih {lv['daily_last']:,.0f} "
                       f"(beda {abs(lv['last']-lv['daily_last'])/lv['daily_last']*100:.2f}%)")
        else:
            _baris += " — cocok dengan bar harian"
        lines.append(_baris)
    if b:
        p2 = f" | >EMA200 [bold]{b['pct>EMA200']*100:.0f}%[/]" if not np.isnan(b.get("pct>EMA200") or np.nan) else ""
        wide = b["pct>EMA50"]
        lines.append(f"Breadth LQ45 ({b['n']} saham): >EMA50 "
                     f"[{'green' if wide > 0.6 else 'yellow' if wide > 0.4 else 'red'}]{wide*100:.0f}%[/]{p2}")
    if n:
        lines.append(f"Berita ({n['n']} judul): skor risiko "
                     f"[{'red' if n['risk'] > 0.2 else 'green'}]{n['risk']:+.2f}[/] [dim](positif = berisiko)[/]")
        if n["tema"]:
            top = sorted(n["tema"].items(), key=lambda x: -x[1])[:4]
            lines.append("  Tema panas: [bold]" + ", ".join(f"{k} ({v:.1f})" for k, v in top if v > 0) + "[/]")
        if n["sektor_panas"]:
            lines.append("  Sektor terdampak: [dark_orange]" + ", ".join(n["sektor_panas"]) + "[/]")
        for w, t in n["top"][:3]:
            lines.append(f"  [{'red' if w >= 4 else 'yellow'}]{w:+.1f}[/] {t}")
    if lines:
        ui.info_panel("TEKANAN PASAR", lines, "orange_red1" if state["adjust"] < -8 else "cyan")
    return None


def _print_panel_plain(state):
    print(f"\n--- KONDISI PASAR GLOBAL & POLITIK ({state['ts']}) ---")
    m = state["macro"]
    for k in ["IHSG", "Brent", "Emas", "DXY", "USD/IDR"]:
        if m.get(k):
            print(f"{k:<8} last {m[k]['last']:>10.2f} | 20d {m[k]['chg20']:+6.2f}% | HV20 {m[k]['hv20']:5.1f}%")
    b = state["breadth"]
    if b:
        p2 = f" | >EMA200 {b['pct>EMA200']*100:.0f}%" if not np.isnan(b.get("pct>EMA200") or np.nan) else ""
        print(f"Breadth ({b['n']} saham): >EMA50 {b['pct>EMA50']*100:.0f}%{p2}")
    n = state["news"]
    if n:
        print(f"Sentimen berita ({n['n']} judul): skor risiko {n['risk']:+.2f} (positif=berisiko)")
        if n["tema"]:
            top = sorted(n["tema"].items(), key=lambda x: -x[1])[:4]
            print("  Tema panas: " + ", ".join(f"{k} ({v:.1f})" for k, v in top if v > 0))
        if n["sektor_panas"]:
            print("  Sektor terdampak: " + ", ".join(n["sektor_panas"]))
        for w, t in n["top"][:3]:
            print(f"  [{w:+.1f}] {t}")
    print(f"→ Penyesuaian skor saham: {state['adjust']:+.1f} | Mood pasar: {state['mood']}")
