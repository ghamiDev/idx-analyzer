"""Berita per emiten IDX lewat Google News RSS.

Yahoo Finance tidak mengembalikan berita untuk ticker .JK (sudah diuji: .news kosong), jadi sumber
terpisah. Ini tetap penanda tekanan/perhatian, BUKAN peramal arah harga.
"""

import datetime as dt
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

try:
    import market_state
except ImportError:
    market_state = None

UA = "Mozilla/5.0 (Windows NT 10.0) idx-decision-desk/1.0"
_CACHE = {}

# kata kunci positif spesifik emiten (di luar RISK_PATTERNS makro milik market_state)
PLUS_PATTERNS = [
    (r"laba|laba bersih|naik \d+|melonjak|menguat|cuan|profit|kinerja positif|rekor|tertinggi", 1.5, "kinerja"),
    (r"dividen", 1.0, "dividen"),
    (r"target harga|target price|naik peringkat|upgrade|rekomendasi (?:beli|akumulasi|add)", 1.5, "analis"),
    (r"akuisisi|merger|ekspansi|kapasitas baru|kontrak|pesanan|impor .*(?:naik|mulai)", 1.0, "ekspansi"),
    (r"buyback|pembelian kembali", 1.0, "buyback"),
    (r"prospek|optimis|peluang|bagus|positif|menjanjikan", 0.5, "sentimen +"),
]


def _score(title):
    """Bilangan + untuk headline yang mendukung, − untuk yang menekan, plus label temanya."""
    s, tags = 0.0, []
    text = title.lower()
    for pat, w, tag in PLUS_PATTERNS:
        if re.search(pat, text):
            s += w
            tags.append(tag)
    if market_state:
        for pat, w, tag in market_state.RISK_PATTERNS:
            if re.search(pat, text):
                s -= w  # w negatif = meredakan risiko → menambah skor
                tags.append(tag)
    for pat, w, tag in [
        (r"anjlok|jatuh|terkoreksi|melemah|tekor|merah|ambruk|turun|_projection|penurunan|rugi", 1.5, "harga turun"),
        (r"downgrade|jual|take profit|provokasi|teguran|sanksi|manipulasi|gugatan|pailit|delisting", 2.0, "negatif"),
        (r"volatil|risiko|waspada|hati.?hati|spekulatif", 0.8, "peringatan"),
    ]:
        if re.search(pat, text):
            s -= w
            tags.append(tag)
    return s, list(dict.fromkeys(tags))[:3]


def headlines(code, days=14, limit=8, force=False):
    """Ambil headline terbaru untuk satu kode saham. Return list dict: title/source/date/link/bias/tags."""
    code = code.upper().replace(".JK", "")
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    return search(f'"{code}" saham OR "emiten {code}" after:{since}', days, limit, force)


def by_query(query, days=90, limit=6, force=False):
    """Headline untuk kueri Google News bebas — dipakai `company.py` untuk tema
    (proyek, aksi korporasi, kepemilikan) yang tidak selalu menyebut kata 'saham'."""
    return search(query, days, limit, force)


def search(q, days, limit, force=False):
    """Mesin pengambil + penyekor satu kueri RSS. Cache keyed on (q, days)."""
    key = (q, days)
    if force:
        _CACHE.pop(key, None)
    if key in _CACHE:
        return _CACHE[key]
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q)
           + "&hl=id&gl=ID&ceid=ID:id")
    out = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        root = ET.fromstring(urllib.request.urlopen(req, timeout=20).read())
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            if not title:
                continue
            link = (it.findtext("link") or "").strip()
            src = (it.findtext("source") or "").strip()
            raw_d = (it.findtext("pubDate") or "").strip()
            when = None
            if raw_d:
                try:
                    when = parsedate_to_datetime(raw_d).replace(tzinfo=None)
                except (TypeError, ValueError):
                    when = None
            bias, tags = _score(title)
            age_h = (dt.datetime.now() - when).total_seconds() / 3600 if when else None
            out.append({"title": title, "link": link, "source": src, "date": when, "age_h": age_h,
                        "bias": bias, "tags": tags})
    except Exception as e:
        return [{"title": f"[gagal mengambil berita: {type(e).__name__} — {e}]", "link": "", "source": "",
                 "date": None, "age_h": None, "bias": 0.0, "tags": ["error"]}]
    out.sort(key=lambda x: (x["date"] or dt.datetime(1970, 1, 1)), reverse=True)
    out = out[:limit]
    _CACHE[key] = out
    return out


def umur_txt(age_h):
    """Umur headline dalam bahasa manusia; None kalau tidak ada pubDate."""
    if age_h is None:
        return "umur tak diketahui"
    if age_h < 1:
        return "baru (<1 jam)"
    if age_h < 24:
        return f"{age_h:.0f} jam lalu"
    return f"{age_h/24:.1f} hari lalu"


def summarize(items):
    """Ringkasan untuk decision desk: skor agregat + pemisah pendukung vs penekan."""
    if not items or items[0]["tags"] == ["error"]:
        return {"n": 0, "net": 0.0, "plus": [], "minus": [], "segar_h": None, "basi": True,
                "note": (items[0]["title"] if items else
                         "tidak ada headline dalam jendela waktu")}
    plus = [x for x in items if x["bias"] > 0]
    minus = [x for x in items if x["bias"] < 0]
    net = sum(x["bias"] for x in items)
    ages = [x.get("age_h") for x in items if x.get("age_h") is not None]
    segar = min(ages) if ages else None
    return {"n": len(items), "net": net, "plus": plus, "minus": minus, "segar_h": segar,
            "basi": segar is None or segar > 72,
            "note": (f"{len(plus)} headline mendukung, {len(minus)} menekan, "
                     f"{len(items) - len(plus) - len(minus)} netral · "
                     f"paling baru {umur_txt(segar)}")}
