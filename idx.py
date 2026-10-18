"""IDX Decision Desk — satu pintu untuk semua mode analisa & pendukung keputusan.

  python idx.py                       # MENU interaktif: pilih nomor, perintah dijalankan
  python idx.py brief                 # dashboard keputusan: pasar → scanner → portofolio → aksi
  python idx.py brief --refresh       # paksa hitung ulang (tanpa memakai riwayat skor hari ini)
  python idx.py check <TICKER>        # keputusan 1 saham + rencana entry/stop/target + ukuran lot
  python idx.py scan|analyze|score|market|backtest <TICKER|--top N>
  python idx.py backtest <TICKER>     # aturan yang sama atas sejarah: transaksi per posisi + biaya dua sisi
  python idx.py backtest --semua      # portofolio gabungan: satu modal bersama, rebutan slot, cap likuiditas
  python idx.py peka <TICKER>         # grid ambang skor × trailing stop × jarak kalibrasi + vonis overfit
  python idx.py profil <TICKER>       # fundamental & konteks: laporan keuangan, rasio, dividen,
                                      # aksi korporasi, kepemilikan, berita bertema proyek/rencana
                                      # (tidak mengubah skor teknis — cache 12 jam, --refresh untuk ulang)
  python idx.py pos                   # status posisi terbuka: P&L, stop, skor, sisa risiko
  python idx.py pos add <T> [--lot N] [--harga X] [--cat "..."]
  python idx.py pos cut <T> [--lot N] [--harga X]
  python idx.py kalender [<T> ...]    # jadwal rilis laporan & tanggal ex-dividen sebatas watchlist —
                                      # tanggal inilah yang sering menendang stop tanpa peringatan;
                                      # kolom POSISI menandai emiten yang Anda pegang (--semua, --hari N)
  python idx.py cfg [show|set K V]    # watchlist, modal, risiko, ambang
  python idx.py hist [<T>] [--days N] # riwayat skor, delta, transaksi & rekap P&L realisasi
  python idx.py cekdata [<T> ...]     # dari mana angka datang: sumber hidup/matinya, umur data,
                                      # kecocokan bar harian vs kutipan live (append --semua)
  python idx.py ikon                  # uji tampil: apakah ikon tergambar & kolom rata di terminal ini
  python idx.py minggu [<T>]          # "dengan modal ini, saham apa yang layak beli 1 minggu?"
                                      # ranking horison 5 hari + rekam jejak + 1 rencana + berita
                                      # <T> diisi → kartu: BERITA → HASIL ANALISA → PENDUKUNG → KEPUTUSAN
                                      # --kartu untuk melihat tampilan kartu dari pilihan teratas ranking

Override sekali jalan (tidak disimpan): --modal 250000000 --risiko 0.5 --entry 25 --ts 3 --top 10 --rebal 42 --slip 0.2

Semua mode memakai config.json yang sama, jadi hasilnya saling terhubung
(scanner → kandidat → ukuran posisi → pencatat portofolio → laporan harian).
"""

import argparse
import datetime as dt
import json
import sys

import numpy as np
import pandas as pd

import idx_analyzer as eng
import live
import market_state
import portfolio as pf
import stocknews
import store
import ui
import company as co
import kalender as kal


def _universe(cfg):
    return cfg["watchlist"] or eng.UNIVERSE


def _cfg_panel(cfg, extra=""):
    lines = [
        f"Modal [bold]{cfg['capital']:,.0f}[/] · risiko/posisi [bold]{cfg['risk_pct']}%[/] "
        f"= {cfg['capital']*cfg['risk_pct']/100:,.0f} · batas risiko total "
        f"[bold]{cfg['max_total_risk_pct']}%[/] · maks [bold]{cfg['max_positions']}[/] posisi",
        f"Ambang: entry ≥ [green]{cfg['entry']:+.0f}[/], keluar ≤ [red]{cfg['flat']:+.0f}[/], "
        f"trailing [red]{cfg['ts_mult']}×ATR[/], gate IHSG>EMA200 "
        f"{'[green]AKTIF[/]' if cfg['gate'] else '[grey62]NONAKTIF[/]'}",
        f"Watchlist: [bold]{len(_universe(cfg))}[/] saham" + (f" · {extra}" if extra else ""),
    ]
    ui.info_panel("KONFIGURASI AKTIF", lines, "blue")


# --------------------------------------------------------------- pos

def cmd_pos(cfg, action=None, ticker=None, lots=None, price=None, cat=""):
    if action == "add":
        with ui.wait(f"memuat data + walk-forward {pf.normalize(ticker).upper()}…"):
            d = eng.decide(pf.normalize(ticker), cfg["capital"], cfg["risk_pct"], entry=cfg["entry"],
                           ts_mult=cfg["ts_mult"], ms=_market(cfg), rebal=cfg["rebal"], flat=cfg["flat"])
        pos, qty, R = pf.add(ticker, lots, price, cat, cfg, d)
        ui.header(f"POSISI DITAMBAH — {pf.normalize(ticker)}",
                  f"{pos['lots']} lot @ {pos['avg']:,.0f} · tercatat {dt.date.today()}")
        ui.info_panel("YANG BARU DICATAT", [
            f"Lot total [bold]{pos['lots']}[/], harga rata-rata [bold]{pos['avg']:,.0f}[/], "
            f"skor saat masuk {d['final']:+.1f}",
            f"Stop awal [red]{pos['stop_initial']:,.0f}[/] (risiko/lembar {R:,.0f}) · "
            f"T1 [green]{pos['t1']:,.0f}[/] · T2 [green]{pos['t2']:,.0f}[/]",
            "Aturan keluar otomatis di cek tiap `brief`: harga ≤ stop/trailing ATR ATAU skor ≤ "
            f"{cfg['flat']:+.0f} setelah ≥5 hari.",
        ], "green")
        return
    if action == "cut":
        r = pf.reduce_or_close(ticker, lots, price)
        col = "green" if r["pnl"] >= 0 else "red"
        ui.header(f"{'POSISI TERTUTUP' if r['lots_left'] == 0 else 'SEBAGIAN KELUAR'} — {r['ticker']}",
                  f"{r['lots_out']} lot @ {r['price']:,.0f} · sisa {r['lots_left']} lot")
        ui.info_panel("HASIL", [
            f"P&L [bold][{col}]{r['pnl']:,.0f}[/][/] rupiah ({r['pnl_pct']:+.1f}% dari harga modal, "
            f"{r['capital_pct']:+.2f}% dari total modal)",
            "Dicatat ke data/trades.jsonl — bisa diaudit ulang lewat `idx.py hist`.",
        ], col)
        return

    p = pf._load()
    if not p:
        ui.header("PORTOFOLIO", "tidak ada posisi terbuka di data/positions.json")
        ui.info_panel("CARA PAKAI", [
            "· Rekap keputusan yang Anda eksekusi di broker: [bold]python idx.py pos add PTBA --lot 17[/]",
            "· Setelah tercatat, `brief` akan memantau P&L, jarak ke stop, dan memberi sinyal keluar "
            "saat aturan sistem terpenuhi.",
            "· Ini bukan auto-order — hanya catatan agar mesin keputusan tahu apa yang Anda pegang."], "cyan")
        return
    ms = _market(cfg)
    rows = []
    with ui.wait(f"menilai {len(p)} posisi terbuka…"):
        for t, pos in p.items():
            try:
                rows.append(pf.status(t, pos, cfg, ms=ms))
            except (SystemExit, Exception) as e:  # noqa: BLE001
                print(f"  skip {t}: {e}", file=sys.stderr)
    render_positions(rows, pf.budget(rows, cfg), cfg)


def render_positions(rows, b, cfg):
    nlive = sum(1 for r in rows if r.get("px_live"))
    sub = (f"{nlive}/{len(rows)} posisi dinilai dari kutipan live (jeda ±1 menit dari bursa)"
           if nlive else "penilaian per bar harian terakhir — jalankan `idx.py cekdata` untuk "
                         "memeriksa kesegarannya")
    ui.header("PORTOFOLIO TERBUKA", f"modal {cfg['capital']:,.0f} · {sub}")
    trs = []
    for r in sorted(rows, key=lambda x: -x["pnl_pct"]):
        pc = "green" if r["pnl_pct"] >= 0 else "red"
        kini = f"{r['px']:,.0f}"
        if r.get("px_live"):
            jam = (r["live"]["time"].strftime("%H:%M") if r.get("live") and r["live"].get("time") else "?")
            kini += f" [dim]live {jam}[/]"
        trs.append((f"[bold]{r['ticker']}[/]", f"{r['lots']}", f"{r['avg']:,.0f}", kini,
                    f"[{pc}]{r['pnl_pct']:+.1f}%[/]", f"[{pc}]{r['pnl']:,.0f}[/]",
                    f"{r['bars_held']}", f"{r['eff_stop']:,.0f}",
                    f"{r['dist_stop_pct']:.1f}%", f"{r['score']:+.1f}", ui.verdict_markup(r["action"])))
    ui.table(["KODE", "lot", "avg", "kini", "P&L%", "P&L Rp", "hari", "stop aktif",
              "jarak", "skor", "aksi"], trs)
    risk_col = "green" if b["used"] <= b["cap"] else "red"
    ui.info_panel("ANGGARAN RISIKO AGREGAT", [
        f"Risiko terkunci sekarang [bold][{risk_col}]{b['used']:,.0f}[/][/] dari jatah "
        f"[bold]{b['cap']:,.0f}[/] ({b['used_pct']:.2f}% modal dari batas {b['cap_pct']}%)",
        f"Sisa daya risiko: [{risk_col}]{b['room']:,.0f}[/] → cukup untuk [bold]{b['can_add']}[/] posisi baru "
        f"lagi (jatah per posisi {b['per_position']:,.0f}); slot posisi [bold]{b['slots']}[/] tersisa "
        f"dari {cfg['max_positions']}.",
        "Risiko per posisi dihitung dari [bold]harga kini − stop aktif[/] × jumlah lembar, "
        "bukan dari nilai rupiah yang tertanam.",
    ], risk_col)
    urg = [r for r in rows if r["exit_now"]]
    if urg:
        ui.info_panel("PEMBERITAHUAN KELUAR", [
            f"· [bold white on red]{r['ticker']}[/] — {r['exit_reason']} (harga {r['px']:,.0f}, "
            f"stop {r['eff_stop']:,.0f}, skor {r['score']:+.1f})" for r in urg] +
            ["Aturan sistem terpenuhi; menunda = memperbesar risiko. Keputusan akhir tetap di Anda."],
            "red")


# --------------------------------------------------------------- brief

def _market(cfg, fresh=False):
    if not market_state:
        return None
    with ui.wait("mengambil IHSG + breadth + berita makro…"):
        return market_state.get_market_state(_universe(cfg), use_cache=not fresh)


def _costs(cfg):
    """Fee + slippage per sisi dalam pecahan (config menyimpan persen)."""
    slip = cfg["slip_pct"] / 100.0
    return (cfg["fee_buy_pct"] / 100.0 + slip, cfg["fee_sell_pct"] / 100.0 + slip)


def _live_check(code, df=None):
    """Ambil kutipan live + bandingkan dengan bar harian; return (garis_panel, kutipan)."""
    if not live:
        return [], None
    try:
        with ui.wait(f"memeriksa kesegaran data {code}…"):
            df = df if df is not None else eng.load(code)
            pr, ok, q = live.crosscheck(code, df)
        return live.check_lines(code, pr, ok, q), q
    except Exception as e:  # noqa: BLE001
        return [f"[yellow]X Perhatian:[/] pemeriksaan data gagal ({type(e).__name__}: {e})"], None


def cmd_brief(cfg, refresh=False, export=True, top=None):
    uni = _universe(cfg)
    today = dt.date.today().isoformat()
    ui.header("IDX DECISION DESK — BRIEFING", f"{dt.datetime.now():%Y-%m-%d %H:%M} · "
              f"{len(uni)} saham watchlist · bar harian Yahoo + validasi kutipan live")
    _cfg_panel(cfg)
    ms = _market(cfg, fresh=refresh)
    if ms:
        market_state.print_panel(ms)
    dl, lq = _live_check(uni[0])
    if dl:
        ui.info_panel(f"STATUS DATA (sampel {uni[0]})", dl,
                      "red" if any("X Bahaya" in x for x in dl) else "cyan")

    cached = [] if refresh else [r for r in store.read_history(last_n_days=1) if r["date"] == today]
    if len(cached) >= max(5, len(uni) // 2):
        gate_now = bool(eng.index_gate().iloc[-1])
        adj = ms["adjust"] if ms else 0.0
        by = {r["ticker"]: r for r in cached}
        res = []
        for t in by:
            sp = market_state.sector_penalty(t, ms) if ms else 0.0
            raw = float(by[t]["raw"])
            res.append({"ticker": t, "raw": raw, "final": float(np.clip(raw + adj + sp, -100, 100)),
                        "sector_adj": sp, "regime": by[t]["regime"], "close": float(by[t]["close"]),
                        "gate": gate_now, "atr_pct": float("nan"), "conf": float("nan")})
        res.sort(key=lambda r: -r["final"])
        ui.info_panel("SCANNER", [
            "[dim]skor teknis diambil dari perhitungan hari ini (hemat waktu); penyesuaian pasar & sektor "
            "tetapi dihitung ulang dengan kondisi terkini — pakai --refresh untuk menghitung ulang semuanya[/]"],
            "grey50")
    else:
        res, ms = eng.compute_scan(uni, ms, rebal=cfg["rebal"])
        store.append_history(res, today)
    eng.render_scan(res, ms, top or cfg["top"], title="SCANNER WATCHLIST")

    prev = store.previous_scores(today)
    deltas = [(r["ticker"], r["final"], r["final"] - prev[r["ticker"]], prev[r["ticker"]], r["gate"])
              for r in res if r["ticker"] in prev]
    if deltas:
        drows = []
        for t, fin, dl, old, gt in sorted(deltas, key=lambda x: -abs(x[2]))[:8]:
            col = "green" if dl > 0 else "red"
            drows.append((f"[bold]{t}[/]", f"{old:+.1f}", f"{fin:+.1f}", f"[{col}]{dl:+.1f}[/]",
                          ui.verdict_markup(eng.verdict(fin, gt))))
        ui.table(["KODE", "skor sebelumnya", "kini", "Δ", "verdict"], drows,
                 caption="pergeseran terbesar sejak perhitungan terakhir yang tersimpan")

    p = pf._load()
    rows, budg = [], pf.budget([], cfg)
    if p:
        rows = []
        for t, pos in p.items():
            try:
                rows.append(pf.status(t, pos, cfg, ms=ms))
            except Exception as e:  # noqa: BLE001
                print(f"  skip {t}: {e}", file=sys.stderr)
        rows = [r for r in rows if r]
        budg = pf.budget(rows, cfg)
        render_positions(rows, budg, cfg)
        ev = kal.brief_events([r["ticker"] for r in rows], cfg)
        if ev:
            ui.info_panel("JADWAL YANG MENYERET POSISI INI", ev, "yellow")

    cands = [r for r in res if r["final"] >= cfg["entry"]]
    plan = []
    room_left = budg["room"]
    slots_left = budg["slots"]
    with ui.wait(f"menghitung ukuran posisi {len(cands[:5])} kandidat + validasi datanya…"):
        for r in cands[:5]:
            try:
                d = eng.decide(r["ticker"], cfg["capital"], cfg["risk_pct"], entry=cfg["entry"],
                               ts_mult=cfg["ts_mult"], ms=ms, rebal=cfg["rebal"], with_live=True,
                               flat=cfg["flat"])
            except Exception as e:  # noqa: BLE001
                plan.append(f"· {r['ticker']}: gagal hitung ({e})")
                continue
            already = d["ticker"] in p
            # kandidat boleh muncul dari cache skor kemarin; ukuran hanya ditawarkan kalau
            # verdict hasil hitung ulang masih benar-benar layak beli
            buyish = d["act"].startswith("BELI") or d["act"].startswith("SIAGA")
            shares = d["qty"] if buyish else 0   # decide() sudah memangkas ke 1/3 saat gate tertutup
            room_shares = int(min(room_left, budg["per_position"]) // max(d["risk_per_share"], 1)) // LOT * LOT
            shares = min(shares, room_shares)
            if not already and slots_left <= 0:
                shares = 0
            lots = shares // LOT
            if lots > 0:
                room_left -= lots * LOT * d["risk_per_share"]
                if not already:
                    slots_left -= 1
            note = []
            if not buyish:
                note.append(f"verdik hasil hitung ulang: {d['act']} → tidak ditawarkan")
            if cfg["gate"] and not d["gate"] and lots > 0:
                note.append("gate IHSG tertutup → porsi 1/3")
            if already:
                note.append("sudah di portofolio → ini rata-rata harga, bukan posisi baru; "
                            "jaga stop yang tercatat")
            if buyish and shares <= 0:
                note.append("sisa anggaran risiko habis" + ("" if already else " atau slot posisi penuh")
                            + " → tidak menambah posisi baru")
            col = "green" if lots > 0 else "grey62"
            act_txt = f"MASUKKAN {lots} LOT" if lots > 0 else "TIDAK TAMBAH POSISI BARU"
            plan.append(f"· [bold]{d['ticker']}[/] skor {d['final']:+.1f} {d['act']} → [{col}]{act_txt}[/] "
                        f"@ ≤ {d['close']:,.0f} · stop [red]{d['stop']:,.0f}[/] · T1 [green]{d['t1']:,.0f}[/] "
                        f"· risiko [bold]{shares*d['risk_per_share']:,.0f}[/]"
                        + (" · " + "; ".join(note) if note else ""))
    if not plan:
        plan = [f"[bold]Tidak ada kandidat di atas ambang [green]{cfg['entry']:+.0f}[/].[/]",
                "· Itu hasil yang normal: sistem ini memang FLAT sebagian besar waktu. "
                "Menunggu bukan kesalahan; memaksa masuk di skor lemah yang salah.",
                "· Cek ulang besok, atau turunkan ambang sementara dengan "
                f"[bold]idx.py cfg set entry 15[/] untuk memperlebar pengamatan (risiko naik)."]
    ui.info_panel("RENCANA AKSI HARI INI", plan + [
        f"· Batas risiko total {cfg['max_total_risk_pct']}% modal; terpakai sekarang "
        f"{budg['used_pct']:.2f}% — posisi baru hanya jika tidak menembus batas itu."], "green")

    ui.info_panel("CARA BACA BRIEF INI", [
        "· Urutan panel = urutan kerja: kondisi pasar (boleh agresif?) → scanner (siapa yang layak?) → "
        "portofolio (apa yang sudah dipegang & mendekati stop?) → rencana aksi (berapa lot?).",
        "· Angka rupiah di rencana aksi adalah [bold]ukuran maksimal[/] menurut risiko 1% per posisi dan "
        "sisa anggaran agregat, bukan perintah beli.",
        "· Sistem ini [bold]pengendali risiko[/], bukan peramal: edge-nya di drawdown & disiplin keluar, "
        "bukan di akurasi arah. Validasi sendiri lewat [bold]idx.py backtest <KODE>[/] (1 saham, "
        "transaksi + biaya), [bold]backtest --semua[/] (modal bersama), [bold]peka <KODE>[/] (grid "
        "kepekaan).",
        "· Riwayat skor tersimpan di history.csv, laporan di reports/, posisi di data/positions.json — "
        "semuanya text plain dan bisa diedit manual.",
        "· Panel STATUS DATA membandingkan bar harian dengan kutipan live (jeda ±1 menit). Kalau ada "
        "[bold]X Bahaya[/], jangan pakai angka halaman ini untuk order — jalankan `idx.py cekdata`.",
        "· Butuh jawaban dalam bentuk pertanyaan: [bold]idx.py minggu[/] — 'dengan modal ini, saham apa "
        "yang layak ditahan 1 minggu, apa faktornya, ada berita apa'."], "cyan")

    if export:
        path = export_report(cfg, res, ms, rows, budg, plan, today)
        ui.info_panel("LAPORAN", [f"Markdown: [bold]{path}[/]",
                                  f"Riwayat skor: [bold]{store.HIST_FILE}[/]"], "blue")
    return res, rows, budg


LOT = 100


def export_report(cfg, res, ms, rows, budg, plan, date):
    plain = ui._strip
    L = [f"# IDX Decision Desk — {date}", "",
         f"- Modal {cfg['capital']:,.0f} · risiko/posisi {cfg['risk_pct']}% · "
         f"batas risiko total {cfg['max_total_risk_pct']}% · maks {cfg['max_positions']} posisi",
         f"- Ambang: entry ≥ {cfg['entry']:+.0f} · keluar ≤ {cfg['flat']:+.0f} · "
         f"trailing {cfg['ts_mult']}×ATR · gate IHSG>EMA200 {'aktif' if cfg['gate'] else 'nonaktif'}"]
    if ms:
        b = ms.get("breadth") or {}
        L += [f"- Pasar: mood {ms['mood']}, penyesuaian skor {ms['adjust']:+.1f}, "
              f"breadth >EMA50 {b.get('pct>EMA50', float('nan'))*100:.0f}%"]
    L += ["", "## Kandidat (skor akhir)", "", "| # | kode | mentah | akhir | rezim | harga |", "|---|---|---|---|---|---|"]
    for i, r in enumerate(res[:cfg["top"]], 1):
        L.append(f"| {i} | {r['ticker']} | {r['raw']:+.1f} | {r['final']:+.1f} | {r['regime']} | {r['close']:,.0f} |")
    if rows:
        L += ["", "## Posisi terbuka", "", "| kode | lot | avg | kini | P&L% | stop aktif | skor | aksi |",
              "|---|---|---|---|---|---|---|---|"]
        for r in rows:
            act = r["action"]
            L.append(f"| {r['ticker']} | {r['lots']} | {r['avg']:,.0f} | {r['px']:,.0f} | "
                     f"{r['pnl_pct']:+.1f}% | {r['eff_stop']:,.0f} | {r['score']:+.1f} | {act} |")
        L += ["", f"Risiko terkunci {budg['used']:,.0f} dari jatah {budg['cap']:,.0f} "
                  f"({budg['used_pct']:.2f}% modal)."]
    L += ["", "## Rencana aksi", ""] + [f"- {plain(x).lstrip('· ').strip()}" for x in plan]
    L += ["", "_Bukan rekomendasi keuangan. Sistem berbasis data harian Yahoo Finance, konfluensi "
          "terbobot akurasi luar-sampel, dan penanda tekanan berita berbasis kata kunci._"]
    return store.write_report("\n".join(L), date)


# --------------------------------------------------------------- minggu

def _exit_date(df, days):
    """Perkiraan tanggal jual: `days` hari bursa setelah bar penutupan terakhir."""
    last = np.datetime64(pd.Timestamp(df.index[-1]).date())
    return str(np.busday_offset(last, days, roll="forward"))


def pick_weekly(rows, cfg, min_n=30):
    """Urutkan kandidat mingguan.

    Tidak hanya melihat skor: tiap saham juga harus konfluensinya sudah positif (skor akhir ≥ +5)
    DAN punya rekam jejak sinyal 5 hari yang lebih baik dari peluang acak pada datanya sendiri
    (n ≥ `min_n`). Ini yang membedakan 'skor tinggi kebetulan' dari 'pola yang terbukti di saham itu'.
    """
    H = cfg["week_horizon"]
    qualified = []
    for r in rows:
        rec = r.get("rec") or {}
        n = rec.get("n", 0) or 0
        if n < min_n or r["final"] < 5 or np.isnan(rec.get("prob_up", np.nan)):
            continue
        edge = (rec["prob_up"] - rec["base_up"]) * 100
        se = np.sqrt(max(rec["prob_up"], 1e-9) * (1 - rec["prob_up"]) / n) * 100
        if edge <= 0:
            continue
        qualified.append({**r, "edge_pp": edge, "se_pp": se, "n_sig": n,
                          "pick": r["final"] + 2 * edge - 0.5 * max(H - 5, 0)})
    qualified.sort(key=lambda r: -r["pick"])
    return qualified


def cmd_minggu(cfg, ticker=None, top=8, refresh=False, kartu=False):
    """Peringkat mingguan (universe) atau kartu satu saham (ticker diisi)."""
    H = cfg["week_horizon"]
    uni = [pf.normalize(ticker)] if ticker else _universe(cfg)
    ui.header(f"PILIHAN MINGGUAN — {H} HARI BURSA",
              f"modal [bold]{cfg['capital']:,.0f}[/] · risiko maks per trade "
              f"[bold]{cfg['week_risk_pct']}%[/] = {cfg['capital']*cfg['week_risk_pct']/100:,.0f} · "
              f"universe {len(uni)} saham · horison scoring {H} hari")
    ms = _market(cfg, refresh)
    rows, ms = eng.compute_scan(uni, ms, horizon=H, rebal=cfg["rebal"], thresh=cfg["entry"])
    for r in rows:
        rec = r["rec"]
        ok = rec["n"] and not np.isnan(rec["prob_up"])
        r["edge_pp"] = (rec["prob_up"] - rec["base_up"]) * 100 if ok else float("nan")
        r["se_pp"] = np.sqrt(max(rec["prob_up"], 1e-9) * (1 - rec["prob_up"]) / rec["n"]) * 100 if ok else float("nan")
        r["n_sig"] = rec["n"]

    def rencana(k):
        with ui.wait(f"menghitung ulang {k['ticker']} untuk horison {H} hari + validasi datanya…"):
            d = eng.decide(k["ticker"], cfg["capital"], cfg["week_risk_pct"], ms=ms, entry=cfg["entry"],
                           ts_mult=cfg["ts_mult"], rebal=cfg["rebal"], horizon=H, flat=cfg["flat"],
                           max_pos_pct=cfg["week_max_pos_pct"], with_live=True)
        with ui.wait(f"mengambil berita {k['ticker']} (Google News RSS)…"):
            news = stocknews.headlines(d["ticker"], days=cfg["week_news_days"], limit=7, force=refresh)
        return d, news, stocknews.summarize(news)

    if ticker:
        # satu saham: tidak disaring dulu — apa adanya, termasuk kalau verdiknya JUAL
        d, news, ns = rencana(rows[0])
        render_kartu(d, rows[0], ns, news, cfg, H)
        ui.info_panel(f"KONTEKS FUNDAMENTAL — {co.code_of(d['ticker'])}",
                      co.peek(d["ticker"], cfg), "blue")
        return d

    ranked = pick_weekly(rows, cfg)
    show = ranked[:top] if ranked else sorted(rows, key=lambda r: -r["final"])[:top]
    trs = []
    for i, r in enumerate(show, 1):
        rec = r["rec"]
        ok = rec["n"] and not np.isnan(rec["prob_up"])
        ec = "green" if (r["edge_pp"] == r["edge_pp"] and r["edge_pp"] > 0) else "red"
        trs.append((str(i), f"[bold]{r['ticker']}[/]",
                    f"[{('green' if r['final'] >= cfg['entry'] else 'red')}]{r['final']:+.1f}[/]",
                    ui.verdict_markup(eng.verdict(r["final"], r["gate"])), r["regime"],
                    f"{rec['prob_up']*100:.0f}%" if ok else "n/a",
                    f"{rec['base_up']*100:.0f}%",
                    f"[{ec}]{r['edge_pp']:+.1f}pp[/]" if ok else "n/a",
                    f"{rec['avg_net']*100:+.2f}%" if ok else "n/a",
                    str(rec["n"]), f"{r['close']:,.0f}"))
    ui.table(["#", "KODE", "SKOR", "verdik", "rezim", "prob 5h", "acak", "edge", "rerata", "n", "harga"], trs,
             caption=f"prob 5h = dari n sinyal skor≥{cfg['entry']:.0f} di saham ini sendiri, berapa % harga 5 hari "
                     f"kemudian lebih tinggi (sudah potong fee {eng.FEE_ROUNDTRIP*100:.2f}%) · "
                     f"kolom SKOR adalah skor yang sama persis dengan yang dipakai rencana di bawah "
                     f"(basis bar harian {rows[0].get('date', '?')})")
    if not ranked:
        ui.info_panel("JAWABAN JUJUR", [
            f"[bold]Tidak ada saham di daftar ini yang lolos tiga syarat sekaligus[/]: skor akhir "
            f"≥ +5, rekam jejak sinyal 5 hari (n ≥ 30) lebih baik dari peluang acak, dan edge > 0.",
            "· Artinya alat ini menolak memberi jawaban, bukan memaksakan satu nama. Dengan modal "
            f"{cfg['capital']:,.0f} yang ditahan 1 minggu, pilihan risikonya SBN/redepo/return.",
            "· Kalau tetap ingin masuk, pakai nama teratas di tabel di atas sebagai [bold]pantau[/] dan "
            f"cek ulang dengan [bold]idx.py check {show[0]['ticker']}[/]."], "red")
        return None
    best = ranked[0]
    d, news, ns = rencana(best)
    if kartu:
        render_kartu(d, best, ns, news, cfg, H)
    else:
        render_minggu(d, best, ranked, ns, news, cfg, H)
    return d


def _faktor_lines(d, ns):
    """Alasan teknis + pasar + berita — dipakai panel FAKTOR dan kartu keputusan."""
    out = []
    tot = d["w_total"] or 1
    for name, wt in sorted(d["weights"].items(), key=lambda x: -x[1])[:4]:
        v = float(np.nan_to_num(d["sig"][name][1].iloc[-1]))
        if v == 0:
            continue
        out.append(f"· [bold]{name}[/] memberi sinyal {ui.signal_markup(v)} dengan bobot "
                   f"{wt/tot*100:.0f}% (kontribusi ≈ {v*wt/tot*100:+.1f} poin skor)")
    out += [
        f"· Konfluensi searah: [bold]{d['conf']:.0f}%[/] bobot metode bergerak ke arah yang sama.",
        f"· Rezim saham ini [bold]{d['regime']}[/] (ADX {d['adx']:.0f}, CHOP {d['chop']:.0f}, "
        f"ATR {d['atr_pct']:.1f}%/hari).",
        f"· Kondisi pasar [{('green' if d['mkt_adj']>=0 else 'red')}]{d['mkt_adj']:+.1f}[/] "
        f"(mood {ui.mood_markup(d['mood'])})" +
        (f", penalti sektor [{('yellow' if d['sector_adj'] else 'grey62')}]{d['sector_adj']:+.1f}[/]"
         if d["sector_adj"] else ", tanpa penalti sektor"),
        f"· Berita 14 hari: {ns['note']}, skor netto "
        f"[{('green' if ns['net']>=0 else 'red')}]{ns['net']:+.1f}[/] (penanda perhatian, BUKAN peramal arah).",
    ]
    return out


def _keyakinan_lines(r, edge_pp, se_pp, ticker, H):
    ky = [
        f"Dari [bold]{r['n']}[/] kemunculan sinyal sekuat ini di {ticker} (horison {H} hari, "
        f"2022→sekarang): harga naik [bold][green]{r['prob_up']*100:.0f}%[/][/] dari waktu, "
        f"peluang acak {r['base_up']*100:.0f}% → edge [{('green' if edge_pp>0 else 'red')}]"
        f"{edge_pp:+.1f}pp[/] ± {se_pp:.1f}pp (standar error).",
        f"Rata-rata return bersih setelah fee [bold]{r['avg_net']*100:+.2f}%[/], median "
        f"{r['median_net']*100:+.2f}%, terburuk {r['worst']*100:+.1f}%.",
    ]
    if r["avg_net"] < 0:
        ky.append(f"[yellow]Perhatikan:[/] probabilitasnya di atas acak tapi [bold]rata-rata bersihnya "
                  f"negatif[/] — lebih sering naik sedikit, tapi kalahannya lebih besar. Sinyal begini "
                  "bagus untuk [bold]menonton[/], buruk untuk menaruh uang.")
    ky += [
        "[yellow]Cara membacanya:[/] probabilitas di bawah ~55% atau edge yang lebih kecil dari "
        "standar error = belum bukti, cuma kebetulan. Alat ini tidak bisa membuat Anda 'yakin profit'; "
        "ia hanya menolak berpura-pura bisa.",
        "Dan ingat: ini statistik horison 5 hari, bukan jaminan satu kejadian — satu minggu bisa "
        "berbeda total karena satu berita atau satu aksi asing.",
    ]
    return ky


def _data_notes(d):
    """Baris status/validasi data untuk panel catatan (kosong kalau modul live tidak ada)."""
    if not live or not d.get("live"):
        return ["· Status data live tidak diperiksa pada run ini (modul `live` gagal dimuat)."]
    return [x.replace("ok:", "[green]ok:[/]") for x in eng.data_status_lines(d)][1:]


def _rencana(d, cfg, H):
    """Ukuran lot + stop + target + tanggal jual; baris pertama jadi peringatan kalau verdik belum BELI."""
    pxl, R, qty, act = d["close"], d["risk_per_share"], d["qty"], d["act"]
    if qty // LOT <= 0:
        return ([f"[bold]0 lot[/] — dengan modal {cfg['capital']:,.0f} dan batas risiko "
                 f"{cfg['week_risk_pct']}% satu lot pun tidak memenuhi syarat.",
                 f"Rugi per lot kalau kena stop: {LOT*R:,.0f} rupiah vs jatah risiko "
                 f"{cfg['capital']*cfg['week_risk_pct']/100:,.0f} rupiah."], "red")
    pos_val = qty * pxl
    sz = [f"Beli [bold cyan]{qty//LOT} lot ({qty:,} lembar)[/] ≈ [bold]{pos_val:,.0f}[/] "
          f"= {pos_val/cfg['capital']*100:.0f}% dari modal {cfg['capital']:,.0f}",
          f"Stop [red]{d['stop']:,.0f}[/] ({cfg['ts_mult']}×ATR) → rugi maksimum "
          f"[bold][red]{qty*R:,.0f}[/][/] rupiah ({qty*R/cfg['capital']*100:.2f}% modal)",
          f"Target +1.5R [green]{d['t1']:,.0f}[/] ≈ {qty*(d['t1']-pxl):,.0f} rupiah · "
          f"trailing {cfg['ts_mult']}×ATR dari puncak setelah hari ke-5",
          f"Jual paling lambat [bold]{_exit_date(d['df'], H)}[/] ({H} hari bursa dari "
          f"{d['date']}) — ini trade mingguan, bukan investasi"]
    if act.startswith("SIAGA"):
        sz.insert(0, f"[yellow]Gate IHSG>EMA200 tertutup → ukuran dipangkas 1/3 dari "
                     f"{d['qty_full']//LOT} lot.[/] Di rezim ini sinyal breakout lebih sering gagal.")
    elif not act.startswith("BELI"):
        sz.insert(0, f"[yellow]Verdik masih [bold]{act}[/], belum sinyal beli (syarat lulus skor "
                     f"≥ {cfg['entry']:.0f}).[/] Angka di bawah cuma rencana kalau Anda tetap memaksa "
                     f"masuk; lebih baik tunggu skor naik atau harga pullback ke {d['zone_lo']:,.0f}.")
    return sz, "green"


# kata kunci berita → kalimat kesimpulan (mesin ini tidak membaca artikel, hanya judulnya)
TAG_KESIMPULAN = {
    "kinerja": "judulnya menyebut laba/menguat/tertinggi",
    "dividen": "ada dividen — biasanya jadi alasan akumulasi sebelum cum date",
    "analis": "ada target harga atau rekomendasi beli dari analis",
    "ekspansi": "ada ekspansi, kontrak, atau pesanan baru",
    "buyback": "ada pembelian kembali saham",
    "sentimen +": "bahasanya optimis tapi tanpa angka",
    "arus modal": "arus dana asing disebut (memborong = masuk, jual bersih = keluar)",
    "valas": "isu kurs rupiah / cadangan devisa",
    "moneter": "isu suku bunga / inflasi",
    "geopolitik": "isu geopolitik yang menekan pasar",
    "keamanan": "isu keamanan",
    "politik domestik": "isu politik dalam negeri",
    "sentimen pasar": "isu sentimen pasar (crash/halt/auto reject)",
    "reputasi pasar": "isu reputasi pasar (MSCI/FTSE/downgrade/manipulasi)",
    "harga turun": "judulnya justru membicarakan harga yang turun",
    "negatif": "ada berita negatif (sanksi/gugatan/pailit/delisting)",
    "peringatan": "ada peringatan risiko/volatilitas",
}


def _badge(b):
    return "[bold green]BULLISH[/]" if b > 0 else ("[bold red]BEARISH[/]" if b < 0 else "[bold grey74]NETRAL[/]")


def _item(icon, i, text):
    """Baris daftar bernomor dengan ikon — '📰 01 …' lebih enak dipindai mata daripada 'berita[01] …'."""
    mk = ui.ic(icon)
    return f"{mk + ' ' if mk else ''}[bold]{i:02d}[/]  {text}"


def render_kartu(d, best, ns, news, cfg, H):
    """Kartu satu saham: BERITA → HASIL ANALISA → ANALISA PENDUKUNG → KEPUTUSAN."""
    st = live.mark(d["live"]) if live and d.get("live") else "STATUS DATA: live tidak diperiksa"
    ui.header(f"ANALISA {d['ticker']}", f"{d['date']} · harga [bold]{d['close']:,.0f}[/] · horison "
              f"{H} hari bursa · modal {cfg['capital']:,.0f} · risiko maks "
              f"{cfg['capital']*cfg['week_risk_pct']/100:,.0f}\n{st}")
    top = news[:3]
    br = []
    for i, x in enumerate(top, 1):
        t = ui.lit(x["title"])
        br.append(_item("news", i, t[:78] + ("…" if len(t) > 78 else "")))
        ring = ", ".join(x["tags"][:2]) or "tidak ada kata kunci khas"
        umur = stocknews.umur_txt(x.get("age_h"))
        stl = "bold red" if x.get("age_h") is None or x["age_h"] > 72 else "grey74"
        tgl = x["date"].strftime("%d-%m-%Y") if x["date"] else "-"
        kk = (f"[grey62]kata kunci:[/] {ring}" if x["tags"] else "[grey62]tanpa kata kunci khas[/]")
        br.append(f"      [grey62]└[/] {ui.lit(x['source'] or 'sumber?')} · [grey62]{tgl}[/] "
                  f"· [{stl}]{umur}[/] · {kk}")
    if not br:
        br = ["[grey62]tidak ada headline dalam jendela waktu[/]"]
    if ns.get("basi") and ns.get("n"):
        br.append(f"[bold yellow]PERHATIAN:[/] headline terbaru berumur "
                  f"[bold]{stocknews.umur_txt(ns.get('segar_h'))}[/] — ini bukan berita reaksi hari ini, "
                  "jadi jangan pakai baris BERITA sebagai alasan masuk/keluar sekarang.")
    ui.info_panel(f"BERITA · {len(top)} sumber teratas (Google News RSS)", br, "cyan")
    an = []
    for i, x in enumerate(top, 1):
        kes = "; ".join(TAG_KESIMPULAN.get(t, t) for t in x["tags"][:2]) or "judulnya netral, tidak memberi informasi arah"
        an.append(_item("scale", i, f"{kes} → bias {x['bias']:+.1f} {_badge(x['bias'])}"))
        an.append(f"      [grey62]dasar[/] berita {i:02d} · {ui.lit(x['source'] or 'sumber?')} · "
                  f"[grey62]kata kunci[/] {', '.join(x['tags'][:2]) or '-'}")
    ui.info_panel("HASIL ANALISA · kesimpulan dari berita", an, "yellow")
    pd_ = [_item("chart", j, l[2:]) for j, l in enumerate(_faktor_lines(d, ns)[:4], 1)]
    r = best["rec"]
    pd_ += [_item("chart", len(pd_) + 1,
                  f"rekam jejak: {r['n']} sinyal sekuat ini di "
                  f"{d['ticker']} → naik {r['prob_up']*100:.0f}% vs acak {r['base_up']*100:.0f}% "
                  f"(edge {best['edge_pp']:+.1f}pp ± {best['se_pp']:.1f}pp)")]
    ui.info_panel("ANALISA PENDUKUNG · data untuk memperkuat keputusan", pd_, "green")
    act, qty = d["act"], d["qty"]
    lots = qty // LOT
    aksi = ("BUY" if act.startswith("BELI KUAT") else "BUY" if act.startswith("BELI")
            else "BUY 1/3" if act.startswith("SIAGA") else "SELL" if act.startswith(("KURANGI", "JUAL"))
            else "TUNGGU")
    nplus = sum(1 for x in top if x["bias"] > 0)
    nminus = sum(1 for x in top if x["bias"] < 0)
    garis = "─" * max(6, ui.avail() - 8)
    kp = [garis,
          f"  AKSI   [bold]{aksi}[/]   harga [bold]{d['close']:,.0f}[/]"
          + (f"   lot {lots}" if aksi.startswith("BUY")
             else f"   lot {lots} (kalau dipaksa masuk)" if aksi == "TUNGGU" and lots
             else "   lot 0 — sistem tidak membuka posisi baru"),
          garis,
          f"  alasan: {d['why']}; skor akhir {d['final']:+.1f} (ambang BELI ≥ {cfg['entry']:.0f}), "
          f"berita 01–03 {nplus} bullish / {nminus} bearish / {len(top)-nplus-nminus} netral, "
          f"gate IHSG {'terbuka' if d['gate'] else 'TERTUTUP'}.",
          f"  {'berita 01–03 tidak boleh menimpa aturan ini: ' if aksi.startswith('BUY') else 'sistem menolak posisi baru: '}"
          f"stop {d['stop']:,.0f} · rugi maks {qty*d['risk_per_share']:,.0f} rupiah · "
          f"jual ≤ {_exit_date(d['df'], H)}",
          "  basis: " + eng.basis_note(d)]
    if best["edge_pp"] <= 0:
        kp.append(f"  [red]perhatian:[/] rekam jejak {r['n']} sinyal sekuat ini di {d['ticker']} justru "
                  f"{best['edge_pp']:+.1f}pp terhadap peluang acak — pola ini belum terbukti di datanya "
                  "sendiri, jadi jangan perbesar posisi hanya karena skor tinggi.")
    ui.info_panel("KEPUTUSAN BUY / SELL · dalam harga", kp, "red" if act.startswith("JUAL") else "magenta")
    ui.info_panel("CATATAN", [
        "· Judul berita tidak dibaca isinya — hanya kata kunci di judul. Jangan pakai baris ini sebagai "
        "alasan masuk kalau beritanya sendiri tidak Anda baca.",
        "· 'yakin profit' tidak bisa diproduksi oleh data harian + kata kunci. Yang bisa: probabilitas "
        "terukur, ukuran posisi, dan rugi maksimum yang diketahui lebih dulu."] + _data_notes(d), "blue")


def render_minggu(d, best, ranked, ns, news, cfg, H):
    pxl, R, qty = d["close"], d["risk_per_share"], d["qty"]
    act, final = d["act"], d["final"]
    ui.decision_banner(act, d["why"], final)
    if live and d.get("cek"):
        pr, ok, q = d["cek"]
        ui.info_panel("VALIDASI DATA", live.check_lines(d["ticker"], pr, ok, q),
                      "red" if any(x[0] == "Bahaya" for x in pr) else "cyan")
    ui.info_panel(f"FAKTOR PENDUKUNG — {d['ticker']}", _faktor_lines(d, ns), "cyan")
    r = best["rec"]
    ui.info_panel("KEYAKINAN — HITUNG-HITUNGAN, BUKAN PERASAAN",
                  _keyakinan_lines(r, best["edge_pp"], best["se_pp"], d["ticker"], H), "yellow")
    risk_idr = cfg["capital"] * cfg["week_risk_pct"] / 100
    sz, col = _rencana(d, cfg, H)
    ui.info_panel("RENCANA KONKRET", sz, col)
    nrows = []
    for x in news:
        b = x["bias"]
        col = "green" if b > 0 else "red" if b < 0 else "grey62"
        t = ui.lit(x["title"])
        nrows.append(((x["date"].strftime("%d-%m-%Y") if x["date"] else "-"),
                      f"[{col}]{b:+.1f}[/]", (t[:52] + ("…" if len(t) > 52 else "")),
                      (",".join(x["tags"])[:12] or "-"), stocknews.umur_txt(x.get("age_h")),
                      x["source"][:14]))
    if nrows:
        ui.header("BERITA TERKAIT SAHAM INI", "Google News RSS · "
                  f"jendela {cfg['week_news_days']} hari · skor kata kunci, bukan sentimen terlatih")
        ui.table(["tanggal", "bias", "judul", "tema", "umur", "sumber"], nrows, first_w=10)
    ui.info_panel("CARA BACA & BATASAN", [
        f"· Skor di mode ini dikalibrasi ulang untuk horison {H} hari, jadi angkanya [bold]berbeda[/] dari "
        "`idx.py check`/`brief` yang memakai horison 20 hari — keduanya benar untuk jangka masing-masing.",
        "· Urutan pilihan = skor horison 5 hari [bold]+[/] rekam jejak sinyal itu di saham yang sama. "
        "Saham dengan skor tinggi tapi histori 5 harinya tidak lebih baik dari acak sengaja disingkirkan.",
        "· Yang dijawab alat ini: [bold]mana yang secara statistik paling layak ditonton minggu ini, "
        "dan berapa rugi maksimumnya[/]. Yang TIDAK bisa dijawab: saham apa yang pasti naik.",
        "· Modal kecil membuat satu lot sudah jadi posisi besar — batas 20% modal dilonggarkan ke "
        f"{cfg['week_max_pos_pct']*100:.0f}% di mode ini; yang mengikat Anda tetap jarak stop, bukan modal.",
        "· Basis angka tabel & rencana: " + eng.basis_note(d) + ".",
        f"· Biaya dipotong per sisi: beli {cfg['fee_buy_pct']:.2f}% + jual {cfg['fee_sell_pct']:.2f}% + "
        f"slippage {cfg['slip_pct']:.2f}% ≈ [bold]{(cfg['fee_buy_pct']+cfg['fee_sell_pct']+2*cfg['slip_pct']):.2f}%[/] "
        "per putaran (bukan 0,4% bulat). Pada saham harga Rp1.000-an, satu lot belum termasuk biaya "
        "broker efektif — jadi lebih besar lagi.",
        "· Bar harian dari sumber publik (Yahoo) bisa tertinggal/revisi; panel VALIDASI DATA di atas "
        "membandingkannya dengan kutipan live berjeda ±1 menit. Volume IHSG memang tidak tersedia, dan "
        "berita adalah kata kunci — [bold]jadwal rilis laporan & tanggal ex-dividen minggu ini dicek "
        "terpisah lewat `idx.py kalender`[/]; skor tetap hanya membaca bar harian.",
    ], "cyan")


# --------------------------------------------------------------- menu

MENU = [
    ("1", "minggu — saham apa yang layak ditahan 1 minggu", ["minggu"]),
    ("2", "minggu <KODE> — kartu 1 saham: berita → analisa → keputusan", ["minggu", "{T}"]),
    ("3", "minggu --modal <Rp> --top <N>", ["minggu", "--modal", "{M}", "--top", "{N}"]),
    ("4", "brief — dashboard harian (pasar → scanner → portofolio → aksi)", ["brief"]),
    ("5", "check <KODE> — keputusan 1 saham horison 20 hari", ["check", "{T}"]),
    ("6", "pos — posisi terbuka + P&L + jarak ke stop", ["pos"]),
    ("7", "cfg show — konfigurasi aktif", ["cfg", "show"]),
    ("8", "hist — riwayat skor + transaksi + rekap realisasi", ["hist"]),
    ("9", "cekdata — dari mana angka ini datang, segaram apa, cocok tidak dengan live", ["cekdata"]),
    ("0", "ikon — uji tampil ikon & rata kolom di terminal ini", ["ikon"]),
    ("b", "backtest <KODE> — aturan yang sama atas sejarah 1 saham: transaksi + biaya dua sisi",
     ["backtest", "{T}"]),
    ("p", "backtest --semua — portofolio: satu modal bersama, rebutan slot & cap likuiditas",
     ["backtest", "--semua"]),
    ("s", "peka <KODE> — grid ambang × trailing stop × kalibrasi + vonis overfit", ["peka", "{T}"]),
    ("f", "profil <KODE> — fundamental: keuangan, rasio, dividen, aksi korporasi, kepemilikan, proyek",
     ["profil", "{T}"]),
    ("k", "kalender — jadwal rilis laporan & tanggal ex-dividen sebatas watchlist (+ posisi yang dipegang)",
     ["kalender"]),
]


def _fill(argv, cfg):
    """Ganti placeholder menu ({T}/{M}/{N}) dengan jawaban user."""
    out = []
    for tok in argv:
        if tok == "{T}":
            out.append(_ask("kode saham (mis. BBRI)", "BBCA"))
        elif tok == "{M}":
            out.append(_ask("modal rupiah", str(int(cfg["capital"]))).replace(".", "").replace(",", ""))
        elif tok == "{N}":
            out.append(_ask("jumlah baris ranking", "8"))
        else:
            out.append(tok)
    return out


def _ask(prompt, default=""):
    try:
        v = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    except (EOFError, KeyboardInterrupt):
        raise EOFError
    return v or default


def cmd_menu(cfg):
    """Peluncur menu. Semua item memanggil fungsi yang sama dengan mode CLI — bukan tampilan baru."""
    while True:
        ui.header("ANALISA SAHAM — sesi interaktif",
                  f"modal {cfg['capital']:,.0f} · risiko mingguan {cfg['week_risk_pct']}% = "
                  f"{cfg['capital']*cfg['week_risk_pct']/100:,.0f} · horison minggu "
                  f"{cfg['week_horizon']} hari bursa")
        ui.info_panel("MENU", [f"  [bold cyan]{k}[/]  {desc}" for k, desc, _ in MENU] +
                      ["  [bold cyan]Q[/]  keluar",
                       "", f"[grey62]{ui.legend()}[/]" if ui.legend() else "",
                       f"[yellow]{ui.icon_hint()}[/]" if ui.icon_hint() else "",
                       "", "[grey62]Boleh juga ketik perintah langsung, mis. `minggu AMRT` atau "
                       "`check BBRI --modal 3000000`.[/]"], "blue")
        try:
            pick = input("\nanalisa@pasar ~ $ ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if pick in ("q", "keluar", "exit"):
            return
        argv = None
        if pick == "":
            continue
        if pick == "1":
            argv = ["minggu"]
        elif pick == "2":
            argv = ["minggu", _ask("kode saham (mis. AMRT)", "AMRT")]
        elif pick == "3":
            m = _ask("modal rupiah", str(int(cfg["capital"]))).replace(".", "").replace(",", "")
            argv = ["minggu", "--modal", m or str(cfg["capital"]),
                    "--top", _ask("jumlah baris ranking", "8")]
        elif pick in dict((k, a) for k, _, a in MENU):
            argv = _fill(dict((k, a) for k, _, a in MENU)[pick], cfg)
        elif pick.split()[0] in ("minggu", "week", "brief", "check", "decide", "pos", "cfg", "hist",
                                 "scan", "analyze", "score", "market", "backtest", "cekdata", "ikon",
                                 "peka", "profil", "kalender"):
            argv = pick.split()
        else:
            ui.info_panel("MENU", ["Pilih 1–9, 0, b, p, s, f, k, atau Q — atau ketik perintah lengkap."], "red")
            continue
        try:
            main(argv)
        except SystemExit as e:
            if e.code not in (0, None):
                print(f"perintah berhenti: {e.code}")
        except (EOFError, KeyboardInterrupt):
            return
        try:
            back = input("\n↩ kembali ke menu (ENTER) · q keluar: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if back in ("q", "keluar"):
            return


# --------------------------------------------------------------- cfg / hist

def cmd_cfg(cfg, argv):
    if not argv or argv[0] == "show":
        print(json.dumps(cfg, indent=2, ensure_ascii=False))
        return
    if argv[0] == "set" and len(argv) >= 3:
        try:
            v = store.set_config(cfg, argv[1], " ".join(argv[2:]))
        except KeyError:
            raise SystemExit(f"Kunci tidak dikenal: {argv[1]} (pilih: {', '.join(store.DEFAULTS)})")
        except (ValueError, TypeError):
            raise SystemExit(f"Nilai untuk {argv[1]} tidak valid.")
        store.save_config(cfg)
        print(f"{argv[1]} = {v}  (disimpan ke config.json)")
        return
    raise SystemExit("Pakai: idx.py cfg show | idx.py cfg set KUNCI NILAI")


def cmd_hist(cfg, ticker=None, days=20):
    t = pf.normalize(ticker) if ticker else None
    rows = store.read_history(t, days)
    trades = [x for x in pf.recent_trades(30) if not t or x.get("ticker") == t]
    if not rows and not trades:
        raise SystemExit("riwayat kosong — jalankan `idx.py brief` atau `scan` dulu.")
    if rows:
        trs = [(r["date"], r["ticker"], f"{float(r['raw']):+.1f}", f"{float(r['final']):+.1f}",
                r["regime"], f"{float(r['close']):,.0f}") for r in rows[-60:]]
        ui.header("RIWAYAT SKOR", f"{store.HIST_FILE} · {len(rows)} baris"
                  + (f" · filter {t}" if t else ""))
        ui.table(["tanggal", "kode", "mentah", "akhir", "rezim", "harga"], trs,
                 caption="satu baris per (tanggal, saham) — hitung ulang di hari yang sama menggantikan yang lama")
    if trades:
        trows = []
        for x in trades:
            pnl = x.get("pnl")
            col = "green" if (pnl or 0) >= 0 else "red"
            trows.append((x["ts"][:16], {"add": "BELI", "reduce": "JUAL SEBAGIAN",
                                         "close": "JUAL SEMUA"}.get(x["event"], x["event"]),
                          x.get("ticker", ""), f"{x.get('lots', 0)}", f"{x.get('price', 0):,.0f}",
                          "" if pnl is None else f"[{col}]{pnl:,.0f}[/]",
                          "" if x.get("pnl_pct") is None else f"[{col}]{x['pnl_pct']:+.1f}%[/]"))
        ui.header("TRANSAKSI TERCATAT", f"{pf.TRADES_FILE} · {len(trades)} baris terakhir")
        ui.table(["waktu", "aksi", "kode", "lot", "harga", "P&L Rp", "P&L%"], trows)
        closed = [x for x in trades if x["event"] in ("close", "reduce") and x.get("pnl") is not None]
        if closed:
            tot = sum(x["pnl"] for x in closed)
            win = sum(1 for x in closed if x["pnl"] > 0)
            col = "green" if tot >= 0 else "red"
            ui.info_panel("REKAP REALISASI", [
                f"Total P&L dari {len(closed)} penjualan: [bold][{col}]{tot:,.0f}[/][/] rupiah · "
                f"{win}/{len(closed)} keluar di atas harga modal ({win/len(closed)*100:.0f}%).",
                "Angka ini dari catatan Anda sendiri, bukan simulasi — bandingkan dengan hasil "
                "[bold]idx.py backtest[/] untuk melihat apakah disiplin keluar benar-benar dijalankan."], "cyan")


# --------------------------------------------------------------- validasi data

def cmd_cekdata(cfg, tickers=None, all_universe=False):
    """Panel 'angka ini datang dari mana, segaram apa, dan cocok tidak dengan yang live'."""
    uni = _universe(cfg)
    sel = [pf.normalize(t).upper() for t in (tickers or [])] or ([x.upper() for x in uni]
                                                                 if all_universe else uni[:4])
    ui.header("VALIDASI SUMBER DATA", f"mesin analisis memakai bar harian; sumber live dipakai untuk "
              f"memeriksa. Sekarang {dt.datetime.now(live.WIB):%H:%M} WIB.")
    with ui.wait("memeriksa hidup/matinya sumber data (5 endpoint)…"):
        probes = live.probe_all()
    rows = [(f"[bold]{n}[/]", f"[{'green' if s == 'HDUP' else 'red'}]{s}[/]", k) for n, s, k in probes]
    ui.table(["SUMBER", "status", "keterangan"], rows,
             caption="satu-satunya yang bisa dipakai script adalah Yahoo; IDX resmi menolak (Cloudflare)")
    with ui.wait("mengambil kutipan live IHSG + kalender sesi…"):
        q = live.quote("^JKSE")
    kal = (f"· kalender sesi dari sumber: {live._at(q['open_epoch'], q['tz_off']):%H:%M}–"
           f"{live._at(q['close_epoch'], q['tz_off']):%H:%M} WIB"
           if q.get("open_epoch") and q.get("close_epoch") else "· kalender sesi tidak dikirim sumber")
    ui.info_panel("STATUS IHSG", [
        live.mark(q), kal,
        "· jam kasar modul: 09:00–11:59 & 13:30–15:20 WIB (pascaperdagangan sampai ~16:15)"], "cyan")
    bah = 0
    for t in sel:
        dl, qq = _live_check(t)
        ui.info_panel(f"CEK {t}", dl, "red" if any("X Bahaya" in x for x in dl) else "green")
        bah += sum(1 for x in dl if "X Bahaya" in x)
    ui.info_panel("CARA BACA HASIL CEK", [
        "· [bold]STATUS DATA[/] baris pertama = harga terakhir yang sebenarnya tercetak di bursa "
        "beserta jamnya. Umur > 10 menit saat pasar buka = ada yang macet (trading halt / sumber terlambat).",
        "· [bold]X Bahaya[/] = bar harian yang dipakai indikator TIDAK SAMA dengan trade live, atau engine "
        "ketinggalan hari. Angkanya jangan dipakai untuk eksekusi — jalankan ulang dengan [--refresh].",
        "· [bold]X Perhatian[/] = aman dibaca tapi perlu sadar konteks: candle masih separuh jalan, "
        "ada hari bursa tanpa bar, atau pergerakan >25% yang layak dicek beritanya.",
        "· [bold]ok[/] = tanggal bar harian = tanggal trade terakhir, dan OHLC-nya cocok dengan agregasi "
        "bar 1 menit (toleransi 1 tick / 1%).",
        f"· Sesi: kalau tertulis [yellow]candle hari ini masih berjalan[/], skor dihitung dari data yang "
        "belum final — penutupan resmi bisa memutar verdict. Paling aman dijalankan **setelah 16:15 WIB**.",
        "· Yang TIDAK bisa dijamin modul ini: tick-by-tick, antrean bid/offer, dan data IDX yang <1 menit — "
        "itu butuh langganan resmi (IDX/OVOP). Yang dijamin: tahu apakah datanya basi atau tidak."], "blue")
    if bah:
        ui.info_panel("KEPUTUSAN", [f"[bold white on red]{bah} temuan BAHAYA[/] — "
                      "jangan pakai hasil scan/check hari ini untuk order nyata."], "red")


def cmd_ikon(cfg):
    """Uji mata sendiri: ikon mana yang tergambar dan apakah kolom tetap rata di terminal ini."""
    mode = ui.icon_mode()
    ui.header("UJI IKON & RATA TAMPILAN",
              f"terminal {ui.avail()} kolom · mode ikon aktif [bold]{mode}[/] — "
              "kalau ada simbol tampil sebagai kotak ▯ atau karakter rusak, paksa bentuk aman: "
              "[bold]IDX_ICONS=ascii[/] (Windows) / [bold]export IDX_ICONS=ascii[/] (Termux), "
              "atau none untuk tanpa ikon")
    hint = ui.icon_hint()
    if hint:
        ui.info_panel("KENAPA BUKAN EMOJI", [
            hint,
            "",
            "[grey62]Tiga tier yang tersedia — coba satu-satu untuk melihat mana yang paling bersih "
            "di terminal kamu:[/] `IDX_ICONS=emoji` · `symbol` · `ascii` · `none`."], "yellow")
    ui.info_panel("LEGENDA", [ui.legend()] if ui.legend()
                  else ["[grey62]ikon dimatikan (IDX_ICONS=none)[/]"], "blue")
    ui.table(["VERDIK", "SKOR", "REZIM", "HARGA"],
             [[ui.verdict_markup(v), f"{s:+.1f}", r, f"{h:,.0f}"] for v, s, r, h in
              (("BELI KUAT", 38.2, "trending", 2320), ("SIAGA", 26.4, "netral", 456),
               ("WATCHLIST", 12.0, "choppy", 1315), ("PEGANG", 1.5, "netral", 28),
               ("KURANGI", -6.7, "trending", 825), ("JUAL KUAT", -31.9, "choppy", 6125))])
    for t in ("KONDISI PASAR GLOBAL", "BERITA TERKINI", "KEPUTUSAN BUY / SELL", "UKURAN POSISI",
              "ANGGARAN RISIKO", "CARA BACA"):
        ui.info_panel(t, ["(contoh isi panel)"], "cyan")


# --------------------------------------------------------------- CLI

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", nargs="?", default=None,
                   choices=["menu", "brief", "scan", "analyze", "score", "market", "backtest", "check",
                            "decide", "pos", "cfg", "hist", "minggu", "week", "cekdata", "ikon", "peka",
                            "profil", "kalender"])
    p.add_argument("rest", nargs="*", help="ticker / arg tambahan (lihat docstring)")
    p.add_argument("--semua", action="store_true",
                   help="cekdata: seluruh watchlist bukan 4 sampel · backtest: portofolio gabungan "
                        "(modal bersama), bukan 1 saham")
    p.add_argument("--top", type=int)
    p.add_argument("--modal", type=float, help="override modal untuk sesi ini (tidak disimpan)")
    p.add_argument("--risiko", type=float, help="override %% risiko per posisi untuk sesi ini")
    p.add_argument("--lot", type=int)
    p.add_argument("--harga", type=float)
    p.add_argument("--cat", default="")
    p.add_argument("--days", type=int, help="hist: berapa hari riwayat (default 20) · kalender: "
                                           "jendela hari bursa ke depan (default config kal_days)")
    p.add_argument("--refresh", action="store_true", help="paksa hitung ulang (abaikan cache skor & berita)")
    p.add_argument("--no-export", action="store_true", help="jangan tulis laporan/riwayat")
    p.add_argument("--entry", type=float)
    p.add_argument("--flat", type=float)
    p.add_argument("--ts", type=float)
    p.add_argument("--rebal", type=int, help="override jarak rekalibrasi bobot (bar) untuk sesi ini")
    p.add_argument("--slip", type=float, help="override slippage %% per sisi untuk sesi ini")
    p.add_argument("--kartu", action="store_true", help="tampilan kartu: berita → analisa → pendukung → keputusan")
    p.add_argument("--nogate", action="store_true")
    a = p.parse_args(argv)

    cfg = store.load_config()
    for k in ("entry", "flat", "ts"):
        v = getattr(a, k)
        if v is not None:
            cfg["ts_mult" if k == "ts" else k] = v
    if a.top:
        cfg["top"] = a.top
    if a.modal:
        cfg["capital"] = a.modal
    if a.risiko:
        cfg["risk_pct"] = a.risiko
    if a.rebal:
        cfg["rebal"] = a.rebal
    if a.slip is not None:
        cfg["slip_pct"] = a.slip

    m = a.mode or "menu"
    if m == "menu":
        cmd_menu(cfg)
    elif m == "brief":
        cmd_brief(cfg, a.refresh, not a.no_export, a.top)
    elif m == "pos":
        act = a.rest[0] if a.rest else None
        tk = a.rest[1] if len(a.rest) > 1 else None
        if act == "add" and not tk:
            raise SystemExit("Pakai: idx.py pos add <TICKER> [--lot N] [--harga X]")
        if act in ("cut",) and not tk:
            raise SystemExit("Pakai: idx.py pos cut <TICKER> [--lot N] [--harga X]")
        cmd_pos(cfg, act, tk, a.lot, a.harga, a.cat)
    elif m == "cfg":
        cmd_cfg(cfg, a.rest)
    elif m == "hist":
        cmd_hist(cfg, a.rest[0] if a.rest else None, a.days or 20)
    elif m == "ikon":
        cmd_ikon(cfg)
    elif m == "cekdata":
        cmd_cekdata(cfg, a.rest, a.semua)
    elif m in ("check", "decide"):
        if not a.rest:
            raise SystemExit("Pakai: idx.py check <TICKER>")
        with ui.wait(f"memuat data + walk-forward {pf.normalize(a.rest[0]).upper()}…"):
            d = eng.decide(pf.normalize(a.rest[0]), cfg["capital"], cfg["risk_pct"],
                           entry=cfg["entry"], ts_mult=cfg["ts_mult"], ms=_market(cfg, a.refresh),
                           rebal=cfg["rebal"], with_live=True, flat=cfg["flat"])
        eng.render_decision(d)
        ui.info_panel(f"KONTEKS FUNDAMENTAL — {co.code_of(d['ticker'])}",
                      co.peek(d["ticker"], cfg), "blue")
        ui.info_panel(f"PERISTIWA TERDEKAT — {co.code_of(d['ticker'])}",
                      kal.event_note(d["ticker"], cfg), "yellow")
    elif m == "scan":
        ms = _market(cfg, a.refresh)
        if ms:
            market_state.print_panel(ms)
        res, ms = eng.compute_scan(_universe(cfg), ms, rebal=cfg["rebal"])
        if not a.no_export:
            store.append_history(res, dt.date.today().isoformat())
        eng.render_scan(res, ms, cfg["top"])
    elif m == "market":
        ms = _market(cfg, fresh=True)
        market_state.print_panel(ms)
        ui.info_panel("LANGKAH BERIKUT", [
            "· [bold]idx.py brief[/] — satu dashboard: pasar → scanner → portofolio → rencana aksi.",
            "· Panel ini cached 30 menit; [bold]--refresh[/] memaksa ambil ulang berita & makro."], "cyan")
    elif m == "backtest":
        if a.semua:
            uni = _universe(cfg)
            tick = [pf.normalize(t) for t in (a.rest or uni[:cfg["top"]])]
            with ui.wait(f"menghitung skor walk-forward {len(tick)} saham…"):
                res = eng.portfolio_backtest(
                    tick, cfg["capital"], cfg["risk_pct"], cfg["max_positions"], cfg["entry"],
                    cfg["flat"], cfg["ts_mult"], cfg["rebal"], cfg["gate"] and not a.nogate,
                    cfg["max_pos_pct"] / 100.0, cfg["max_volume_pct"] / 100.0, *_costs(cfg),
                    progress=lambda msg, i, n: ui.stage(f"{msg} ({i}/{n})") if msg else None)
            eng.cmd_backtest_portfolio(res, cfg)
            return
        sym = pf.normalize(a.rest[0]) if a.rest else "^JKSE"
        ui.stage(f"menjalankan backtest {sym} (banyak replikasi historis)…")
        eng.cmd_backtest(sym, cfg["entry"], cfg["flat"], cfg["rebal"], cfg["ts_mult"],
                         cfg["gate"] and not a.nogate, *_costs(cfg), slip=cfg["slip_pct"] / 100.0)
    elif m == "peka":
        sym = pf.normalize(a.rest[0]) if a.rest else "^JKSE"
        ui.stage(f"menguji kepekaan {sym} (grid ambang × stop × kalibrasi)…")
        eng.cmd_peka(sym, cfg)
    elif m == "profil":
        if not a.rest:
            raise SystemExit("Pakai: idx.py profil <TICKER>  (contoh: idx.py profil BBCA)")
        co.cmd_profil(a.rest[0], cfg, force=a.refresh)
        ui.info_panel(f"PERISTIWA TERDEKAT — {co.code_of(a.rest[0])}",
                      kal.event_note(a.rest[0], cfg), "yellow")
    elif m == "kalender":
        kal.cmd_kalender(cfg, a.rest or None, a.semua, a.days, a.refresh)
    elif m in ("score", "analyze"):
        sym = pf.normalize(a.rest[0]) if a.rest else "^JKSE"
        ui.stage({"score": f"menghitung skor walk-forward {sym}…",
                  "analyze": f"memuat 5 tahun bar harian + 20 metode untuk {sym}…"}[m])
        if m == "score":
            eng.cmd_score(sym)
        else:
            eng.cmd_analyze(sym, cfg["rebal"], cfg["entry"], cfg["ts_mult"])
    elif m in ("minggu", "week"):
        if a.risiko:
            cfg["week_risk_pct"] = a.risiko
        cmd_minggu(cfg, pf.normalize(a.rest[0]) if a.rest else None, a.top or 8, a.refresh, a.kartu)


if __name__ == "__main__":
    main()
