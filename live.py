"""Kutipan near-real-time IDX + pemeriksaan kesegaran/validitas data.

Hasil uji dari mesin ini (13-10-2026, jam 15:4x WIB):
  · api.idx.co.id, www.idx.co.id/primary/*, MobileWeb/StockSummary → HTTP 403 "Just a moment..."
    (Cloudflare turnstile) → tidak bisa dipakai script.
  · Yahoo v7/finance/quote → 401 (butuh crumb + cookie).
  · Google Finance → HTML tapi harganya dirender JavaScript, tidak ada di markup.
  · Yahoo v8/finance/chart dengan interval=1m → 200 dan BARAN: dua panggilan berjarak 20 detik
    memberi bar terakhir 15:36:42 → 15:37:21 (399 bar) dan harga ikut berubah.
Jadi sumber "real-time" di sini = kutipan publik berjeda ±1 menit (bukan feed tick berbayar
IDX/OVOP). Cukup untuk menjawab: data ini hari apa, jam berapa, sudah final atau masih berjalan,
dan apakah bar harian yang dipakai engine cocok dengan trade sebenarnya.
"""

import datetime as dt
import json
import time
import urllib.error
import urllib.request

WIB = dt.timezone(dt.timedelta(hours=7))
_S = {}
_TTL = 45
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/126.0.0.0 Safari/537.36", "Accept": "application/json"}
# jam kasar utk menandai "hari bursa" kalau meta tidak mengirim kalender;
# kalender sebenarnya diambil dari currentTradingPeriod (IDX: 09:00–15:20 + pascaperdagangan)
SESI = ((9 * 60, 11 * 60 + 59), (13 * 60 + 30, 15 * 60 + 20))


def sym(code):
    """BBRI / bbri.jk / ^JKSE → simbol Yahoo untuk IDX."""
    code = str(code).strip().upper()
    return code if code.startswith("^") else (code if code.endswith(".JK") else code + ".JK")


def _at(epoch, off):
    return dt.datetime.fromtimestamp(epoch, dt.timezone(dt.timedelta(seconds=off)))


def in_session(now=None):
    """True kalau WIB sekarang sedang di jam sesi reguler IDX (perkiraan; libur tidak dideteksi)."""
    n = now or dt.datetime.now(WIB)
    if n.weekday() >= 5:
        return False
    m = n.hour * 60 + n.minute
    return any(a <= m <= b for a, b in SESI)


def _blank(code):
    return {"code": str(code).upper().replace(".JK", ""), "symbol": sym(code), "price": None,
            "prev_close": None, "time": None, "age_min": None, "session": "tidak terambil",
            "day_open": None, "day_high": None, "day_low": None, "day_close_last": None,
            "volume": None, "n_bar": 0, "open_epoch": None, "close_epoch": None,
            "tz_off": 25200, "source": "yahoo v8 chart", "error": "belum dicoba", "stale": True}


def _parse(code, j, gran):
    res = (j.get("chart", {}).get("result") or [{}])[0]
    m = res.get("meta", {})
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    off = m.get("gmtoffset", 25200)
    tp = (m.get("currentTradingPeriod") or {}).get("regular") or {}
    when = _at(m["regularMarketTime"], off) if m.get("regularMarketTime") else None
    if when:
        when = when.replace(second=0, microsecond=0)
    o = h = l = c = None
    if ts and q.get("close"):
        op = [x for x in (q.get("open") or []) if x is not None]
        hi = [x for x in (q.get("high") or []) if x is not None]
        lo = [x for x in (q.get("low") or []) if x is not None]
        cl = [x for x in (q.get("close") or []) if x is not None]
        o, c = (op[0] if op else None), (cl[-1] if cl else None)
        h = max(hi) if hi else None
        l = min(lo) if lo else None
    F = lambda x: None if x is None else float(x)
    out = {"code": code, "symbol": m.get("symbol") or sym(code), "price": F(m.get("regularMarketPrice")),
           "prev_close": F(m.get("previousClose") or m.get("chartPreviousClose")),
           "time": when, "age_min": (dt.datetime.now(WIB) - when).total_seconds() / 60 if when else None,
           "session": _session(when, tp.get("start"), tp.get("end"), off),
           "day_open": F(o), "day_high": F(h or m.get("regularMarketDayHigh")),
           "day_low": F(l or m.get("regularMarketDayLow")), "day_close_last": F(c),
           "volume": F(m.get("regularMarketVolume")), "n_bar": len(ts),
           "open_epoch": tp.get("start"), "close_epoch": tp.get("end"), "tz_off": off,
           "source": f"yahoo v8 chart 1m ({len(ts)} bar)" if gran == "1m"
           else f"yahoo v8 chart 1d ({len(ts)} bar)",
           "granularity": gran, "error": None}
    out["stale"] = _is_stale(out["time"], out["session"], out["age_min"])
    return out


def _is_stale(when, session, age_min):
    if when is None or age_min is None:
        return True
    if "masih berjalan" in session:
        return age_min > 10          # sesi aktif: cetak lebih tua dari 10 menit = mencurigakan
    return age_min > 6 * 60          # sudah tutup: yang penting tanggalnya, bukan umur per menit


def _session(when, start, end, off):
    """Status sesi dari kalender bursa yang dikirim sumber data, bukan dari jam perkiraan."""
    now = dt.datetime.now(WIB)
    if start and end:
        s, e = _at(start, off), _at(end, off)
        if s <= now <= e:
            # sesi reguler IDX efektif 09:00–15:20; setelah itu lelang pascaperdagangan yang
            # datanya masih mengalir tapi candle hariannya praktis sudah final
            if now.hour * 60 + now.minute > 15 * 60 + 20:
                return ("PASCAPERDAGANGAN · candle hari ini masih pasca sesi "
                        "(lelang terus-menerus, bukan sesi reguler)")
            return "PASAR BUKA · candle hari ini masih berjalan"
        if now > e and (now - e).total_seconds() <= 24 * 3600 and when and when.date() == now.date():
            sisa = (now - e).total_seconds() / 60
            return (f"SESI SELESAI (kalender: {s:%H:%M}–{e:%H:%M} WIB)"
                    + (f" · {sisa:.0f} menit pasca sesi" if sisa <= 45 else " · harga terakhir hari ini"))
        if now < s and when and when.date() == now.date():
            return "SEBELUM PEMBUKAAN · data masih hari kerja sebelumnya"
    if when:
        if when.weekday() >= 5:
            return "HARI LIBUR BURSA (akhir pekan)"
        if when.date() == now.date():
            return "di luar jam sesi · harga terakhir hari ini"
        return "harga terakhir hari bursa sebelumnya"
    return "status tidak dikenali"


def quote(code, force=False):
    """Kutipan hari ini satu kode. Tidak pernah melempar exception — gagal → dict['error'] terisi."""
    key = sym(code)
    now = time.time()
    hit = _S.get(key)
    if hit and not force and now - hit[0] < _TTL:
        return hit[1]
    out = _blank(code)
    for gran, rng in (("1m", "1d"), ("1d", "2d")):
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{key}?interval={gran}&range={rng}"
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, headers=_UA)
                with urllib.request.urlopen(req, timeout=15) as r:
                    j = json.loads(r.read().decode("utf-8", "replace"))
                p = _parse(out["code"], j, gran)
                if p["price"] is not None and p["time"] is not None:
                    out = p
                    break
                if p["price"] is not None and gran == "1m":
                    out = p          # harga ada tapi bar 1m kosong → tunggu bar harian utk OHLC
                else:
                    out["error"] = "sumber menjawab tapi tidak ada harga (result kosong)"
            except Exception as e:  # noqa: BLE001
                out["error"] = f"{type(e).__name__}: {e}"
                time.sleep(0.6 + attempt * 1.2)
        if out["price"] is not None:
            break
    _S[key] = (now, out)
    return out


def trading_days(days=45, force=False):
    """Tanggal hari bursa menurut kalender sumber data (indeks komposit). Return list[date]."""
    key = "^JKSE:cal"
    hit = _S.get(key)
    if hit and not force and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    dates = []
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EJKSE?interval=1d&range=3mo"
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=15) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        res = j["chart"]["result"][0]
        off = res["meta"].get("gmtoffset", 25200)
        q = (res["indicators"]["quote"][0].get("close") or [])
        for t, c in zip(res.get("timestamp") or [], q):
            if c is not None:
                dates.append(_at(t, off).date())
        dates = dates[-days:]
    except Exception:  # noqa: BLE001
        dates = []
    _S[key] = (time.time(), dates)
    return dates


def fresh(q, tol_min=3.0):
    """Apakah kutipan ini benar-benar terkini untuk kondisi pasar sekarang."""
    if not q or q.get("error") or q.get("time") is None or q["time"].weekday() >= 5:
        return False
    if q.get("stale"):
        return False
    if "masih berjalan" in q["session"]:
        return q["age_min"] <= max(tol_min, 10)
    return q["time"].date() >= (dt.datetime.now(WIB) - dt.timedelta(days=3)).date()


def integrity(daily, cal=None, first_only=False):
    """Periksa struktur bar harian engine: bar kosong & hari bursa yang hilang vs kalender indeks.

    `first_only=True` dipakai pemanggil yang sudah tahu jawabannya (crosscheck memanggil ini
    sekali per saham; kalender indeks diambil sekali per proses).
    """
    out = []
    if daily is None or len(daily) == 0:
        return ["bar harian kosong — tidak ada yang bisa diperiksa"]
    tail = daily.tail(12)
    n = int(tail[["Open", "High", "Low", "Close"]].isna().any(axis=1).sum())
    v0 = int((tail["Volume"].fillna(0) == 0).sum())
    if n:
        out.append(f"{n} dari 12 bar terakhir punya OHLC kosong (sumber mengirim bar tanpa harga; "
                   "bar itu dibuang engine sehingga riwayat indikator terpotong)")
    if v0:
        out.append(f"{v0} dari 12 bar terakhir tercatat volume 0 padahal ada harganya")
    cal = cal if cal is not None else trading_days()
    if cal:
        have = {t.date() for t in daily.index}
        win = [d for d in cal if d >= min(cal[-1] - dt.timedelta(days=21), cal[0])]
        miss = [d for d in win if d not in have]
        if miss:
            out.append(f"{len(miss)} hari bursa menurut kalender indeks tidak punya bar untuk saham ini "
                       "(" + ", ".join(d.strftime("%d-%m") for d in miss[:6]) +
                       (f" +{len(miss)-6} lagi" if len(miss) > 6 else "") +
                       ") — riwayat indikator terputus; bila terjadi pada banyak saham sekaligus, "
                       "itu cacat feed sumber, bukan bursa libur")
    return out


def crosscheck(code, daily, lookback=4):
    """Bandingkan bar harian engine dengan kutipan live. Return (masalah, cocok, kutipan)."""
    q = quote(code)
    return _compare(code, q, daily, lookback) + (q,)


def _compare(code, q, daily, lookback=4):
    problems, ok = [], []
    cal = trading_days()
    if q.get("error"):
        return ([("Bahaya", f"kutipan live {code} gagal diambil ({q['error']}) — kesegaran data "
                  "TIDAK bisa diverifikasi")], ok)
    if daily is None or len(daily) == 0:
        return ([("Bahaya", f"bar harian {code} kosong — tidak ada apa pun utk divalidasi")], ok)
    last_date = daily.index[-1].date()
    live_date = q["time"].date() if q["time"] else None
    pasca = "pasca sesi" in q["session"]
    running = "masih berjalan" in q["session"] and not pasca
    px = q.get("day_close_last") or q.get("price") or 0
    tol = max(round(px * 0.0025) if px else 1, 1)
    if running:
        problems.append(("Perhatian", f"pasar sedang buka — bar {last_date} SEPARUH JALAN (volume "
                         f"{q['volume']:,.0f} lembar). Semua indikator dihitung dari candle yang belum "
                         "terbentuk; skor masih bisa berubah sampai penutupan."))
    elif live_date == last_date:
        ok.append(f"bar harian terakhir = {last_date}, sama dengan trade terakhir live "
                  f"({q['time']:%H:%M} WIB, harga {q['price']:,.0f})"
                  + (" — cetak pasca sesi masih mengalir, penutupan resmi bisa beda sedikit" if pasca else ""))
    if live_date and last_date and live_date != last_date:
        gap = (live_date - last_date).days
        if in_session():
            problems.append(("Bahaya", f"harga live {code} per {live_date} = {q['price']:,.0f} tapi bar "
                             f"harian terakhir engine masih {last_date} (selisih {gap} hari) — "
                             "data yang dianalisa TERTINGGAL, jangan pakai skor ini untuk eksekusi."))
        elif gap > lookback:
            problems.append(("Perhatian", f"bar harian terakhir {last_date} sudah {gap} hari tidak "
                             "bergerak padahal bursa buka — sumber data kemungkinan tidak update."))
    d = daily.iloc[-1]
    for field, key, label in (("Close", "day_close_last", "close"), ("High", "day_high", "high"),
                              ("Low", "day_low", "low")):
        a, b = d.get(field), q.get(key)
        if a is None or b is None or (running and field == "Close"):
            continue
        if abs(float(a) - float(b)) > max(tol, 0.01 * float(b)):
            problems.append(("Bahaya", f"{label} {code} di bar harian {float(a):,.0f} ≠ live "
                             f"{float(b):,.0f} (beda {abs(float(a)-float(b))/max(float(b),1)*100:.1f}%) "
                             "— salah satu sumber salah atau simbol berbeda."))
            break
    else:
        if not running and px:
            ok.append(f"OHLC {last_date} cocok dengan agregasi bar 1 menit (toleransi {tol:,.0f})")
    if q.get("price") and q.get("prev_close"):
        chg = (q["price"] / q["prev_close"] - 1) * 100
        if abs(chg) > 25:
            problems.append(("Perhatian", f"pergerakan {code} {chg:+.1f}% vs penutupan kemarin "
                             f"({q['prev_close']:,.0f} → {q['price']:,.0f}) — di luar lazim, cek ARA/ARB, "
                             "aksi korporasi/pembagian saham, atau kemungkinan data salah."))
    if q.get("volume") == 0 and (running or pasca):
        problems.append(("Perhatian", f"volume live {code} = 0 padahal sesi masih/pasbar baru berjalan — "
                         "data tidak lengkap."))
    for msg in integrity(daily, cal):
        problems.append(("Perhatian", msg))
    return problems, ok


def mark(q):
    """Satu baris status data untuk header panel."""
    if not q or q.get("error"):
        return ("[bold red]STATUS DATA:[/] kutipan live gagal — angka di bawah murni bar harian terakhir "
                "dan mungkin bukan hari ini")
    col = "green" if fresh(q) else "yellow"
    t = f"{q['time']:%d-%m %H:%M}" if q["time"] else "?"
    basi = (" · [bold yellow]cetak terakhir berumur > 10 menit — kemungkinan sumber terlambat[/]"
            if q.get("stale") and "masih berjalan" in q["session"] else "")
    return (f"[{col}]STATUS DATA:[/] live [bold]{q['price']:,.0f}[/] @ {t} WIB "
            f"({q['age_min']:.0f} mnt lalu) · {q['session']} · sumber {q['source']}{basi}")


def check_lines(code, problems, ok, q):
    """Baris panel validasi, diurutkan Bahaya → Perhatian → cocok."""
    lines = [mark(q)]
    order = {"Bahaya": 0, "Perhatian": 1}
    for lvl, msg in sorted(problems, key=lambda x: order.get(x[0], 9)):
        col = "red" if lvl == "Bahaya" else "yellow"
        lines.append(f"[{col}]X {lvl}:[/] {msg}")
    for msg in ok:
        lines.append(f"[green]ok:[/] {msg}")
    if not problems:
        lines.append("[green]tidak ada masalah validasi terdeteksi[/]")
    return lines


def probe_all():
    """Coba satu per satu kandidat sumber data → list (nama, status, keterangan)."""
    cand = [
        ("Yahoo v8 chart 1m", "https://query1.finance.yahoo.com/v8/finance/chart/BBRI.JK"
                              "?interval=1m&range=1d", "harga, OHLC & kalender sesi hari ini"),
        ("Yahoo v8 chart 1d", "https://query1.finance.yahoo.com/v8/finance/chart/%5EJKSE"
                              "?interval=1d&range=3mo", "bar harian — bahan utama engine"),
        ("IDX api.idx.co.id", "https://api.idx.co.id/stock-data/trading-quotes/BBRI",
         "resmi, tapi di balik Cloudflare"),
        ("IDX GetSecurities", "https://www.idx.co.id/primary/ListedSecurity/GetSecurities?keyword=BBRI"
                              "&maxRows=3", "daftar emiten resmi"),
        ("Yahoo v7 quote", "https://query1.finance.yahoo.com/v7/finance/quote?symbols=BBRI.JK",
         "butuh crumb + cookie"),
    ]
    out = []
    for name, url, ket in cand:
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=12) as r:
                body = r.read()
            hidup = b'"chart"' in body or b"<item" in body or b'"quoteResponse"' in body
            out.append((name, "HDUP" if hidup else "jawab tapi tak berisi",
                        f"HTTP 200 · {len(body):,} byte · {(time.time()-t0)*1000:.0f} ms · {ket}"))
        except urllib.error.HTTPError as e:
            out.append((name, "MATI", f"HTTP {e.code} {e.reason} · {ket}"))
        except Exception as e:  # noqa: BLE001
            out.append((name, "MATI", f"{type(e).__name__} · {ket}"))
    return out
