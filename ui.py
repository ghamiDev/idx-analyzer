"""Lapisan UI terminal: warna & tabel via rich, fallback teks polos."""

import os
import re
import sys
import threading
import time

try:
    from rich import box
    from rich.cells import cell_len
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    HAS_RICH = True
    _c = Console(highlight=False)
    if not _c.is_terminal:
        # dipipe/capture: beri ruang lebar agar tabel tidak terpotong
        _c = Console(highlight=False, width=int(os.environ.get("IDX_UI_WIDTH", "104")))
except ImportError:
    HAS_RICH = False
    _c = None

C_VERDICT = {
    "BELI KUAT": "bold white on green", "BELI": "green", "SIAGA": "yellow",
    "WATCHLIST": "cyan", "HOLD": "yellow", "TUNGGU": "grey", "NETRAL": "grey",
    "KURANGI": "dark_orange", "JUAL": "orange_red1", "JUAL KUAT": "bold white on red",
    "JUAL / HINDARI": "bold white on red", "KELUAR": "bold white on red",
    "PEGANG / TAMBAH": "green", "PEGANG": "yellow",
}
C_GRADE = {"A": "bold green", "B": "yellow", "C": "grey70", "D": "red", "D*": "grey50"}
C_MOOD = {"MENGIJINKAN": "green", "NETRAL": "yellow", "BERISIKO": "orange_red1", "SANGAT BERISIKO": "bold white on red"}

# ------------------------------------------------------------------ ikon
# Tiga tier, dipilih otomatis sesuai terminal:
#   emoji  = Termux / Windows Terminal / VS Code / terminal modern
#   symbol = konsol Windows lama: simbol BMP (▲ ↑ ¤ ▮) yang tetap tergambar di font Consolas
#   ascii  = kalau codepage UTF-8 pun tidak bisa: V, (!), ++, --
# Paksa dengan env: IDX_ICONS=emoji | symbol | ascii | none
_EMOJI = {
    # U+FE0F (emoji presentation selector) dipasang pada glyph yang punya dua bentuk: tanpa itu
    # sebagian terminal menggambar versi teks 1 kolom, sebagian lagi emoji 2 kolom -> border miring.
    # Semua dipilih dari blok emoji yang pasti ada di font Termux/Windows Terminal.
    "shield": "🛡️", "radar": "📡", "book": "📖", "scale": "⚖️", "plan": "📅", "coin": "💰",
    "risk": "⚠️", "news": "📰", "globe": "🌐", "chart": "📊", "scan": "🔎",
    "target": "🎯", "compass": "🧭", "gear": "⚙️", "lab": "🧪",
    "spark": "⚡", "list": "▸",
    "up": "▲", "down": "▼",
    "BELI KUAT": "🚀", "BELI": "🟢", "SIAGA": "🚨", "WATCHLIST": "👀", "HOLD": "🟡",
    "PEGANG": "🟡", "PEGANG / TAMBAH": "🟢", "TUNGGU": "⏳", "NETRAL": "⚪", "KURANGI": "🟠",
    "JUAL": "🔴", "JUAL KUAT": "⛔", "JUAL / HINDARI": "⛔", "KELUAR": "⛔",
}
# Tier tengah untuk konsol Windows lama (cmd/PowerShell tanpa Windows Terminal): emoji tidak
# tergambar di sana, tapi blok Arrows / Geometric Shapes / Math masih aman di font Consolas.
# Semua glyph di bawah punya lebar 1 kolom, jadi rata kolom tetap aman.
_SYMBOL = {
    "shield": "√", "radar": "(•)", "book": "≡", "scale": "≈", "plan": "□", "coin": "¤",
    "risk": "△", "news": "▪", "globe": "○", "chart": "▌", "scan": "⊙",
    "target": "×", "compass": "→", "gear": "#", "lab": "∴",
    "spark": "*", "list": "►",
    "up": "▲", "down": "▼",
    "BELI KUAT": "↑↑", "BELI": "↑", "SIAGA": "!", "WATCHLIST": "◇", "HOLD": "—", "PEGANG": "—",
    "PEGANG / TAMBAH": "↑", "TUNGGU": "…", "NETRAL": "±", "KURANGI": "↓", "JUAL": "▽",
    "JUAL KUAT": "↓↓", "JUAL / HINDARI": "↓↓", "KELUAR": "↓↓",
}
_ASCII = {                       # tanpa kurung siku: rich akan menafsirkannya sebagai markup tag
    "shield": "V", "radar": "(~)", "book": "i", "scale": "<!>", "plan": "*", "coin": "$",
    "risk": "!!", "news": "@", "globe": "o", "chart": "%", "scan": "?",
    "target": "o>", "compass": ">", "gear": "#", "lab": "x",
    "spark": "*", "list": ">",
    "up": "^", "down": "v",
    "BELI KUAT": "++", "BELI": "+", "SIAGA": "!", "WATCHLIST": "~", "HOLD": "=", "PEGANG": "=",
    "PEGANG / TAMBAH": "+", "TUNGGU": ".", "NETRAL": "0", "KURANGI": "-", "JUAL": "--",
    "JUAL KUAT": "XX", "JUAL / HINDARI": "XX", "KELUAR": "XX",
}
# kata kunci judul panel/header -> nama ikon
_TITLE_ICON = [
    (("BRIEFING", "DECISION DESK"), "chart"),
    (("VALIDASI", "CEK ", "EVIDEN"), "shield"), (("STATUS",), "radar"),
    (("CARA BACA", "CARA PAKAI", "CATATAN", "JUJUR", "BATASAN", "LEGENDA"), "book"),
    (("KEPUTUSAN", "AKSI "), "scale"), (("RENCANA", "MINGGUAN", "KALENDER", "PERISTIWA"), "plan"),
    (("UKURAN POSISI", "REKAP", "PORTOFOLIO", "LAPORAN", "KEYAKINAN", "DICATAT", "TRANSAKSI",
      "SKOR HARI INI"), "coin"),
    (("ANGGARAN RISIKO", "RISIKO"), "risk"), (("BERITA", "PEMBERITAHUAN"), "news"),
    (("KONDISI PASAR", "TEKANAN PASAR", "PASAR"), "globe"),
    (("SCANNER", "SKOR", "ANALISA", "HASIL", "KOMPOSISI", "FAKTOR", "PENDUKUNG", "SAHAM"), "chart"),
    (("LEVEL", "TARGET"), "target"), (("LANGKAH", "MENU"), "compass"),
    (("KONFIGURASI", "CFG"), "gear"), (("BACKTEST", "SIMULASI", "UJI IKON"), "lab"),
]


def _console_utf8():
    """Konsol Windows membaca output dengan codepage OEM (CP437) -> simbol UTF-8 jadi karakter rusak
    (▲ tampil sebagai ^). Set output ke UTF-8 dulu sebelum simbol BMP dipakai."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        if k32.GetConsoleOutputCP() == 65001:
            return True
        return bool(k32.SetConsoleOutputCP(65001))
    except Exception:
        return False


def _modern_terminal():
    """Terminal yang pasti sanggup emoji, walau rich masih menyebutnya konsol lama."""
    env = os.environ
    return bool(env.get("WT_SESSION") or env.get("TERMINAL_EMULATOR")
                or env.get("TERM_PROGRAM") in ("vscode", "Hyper", "Terminus", "WezTerm"))


def _utf8_stream():
    """Python di Windows menulis pakai codepage konsol (cp1252/cp437): border ┌─ dan simbol BMP
    melempar UnicodeEncodeError di tengah halaman. Minta UTF-8 ke konsol dulu, baru ke stream."""
    _console_utf8()
    for s in (sys.stdout, sys.stderr):
        try:
            if (s.encoding or "").lower().replace("-", "") != "utf8":
                s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_utf8_stream()


_ICON_MODE = None
_TIERS = ("emoji", "symbol", "ascii")


def _encodable(mode):
    """Semua glyph mode ini muat tidak di stream keluaran (plain print / pipe).

    rich yang menulis ke konsol Windows memakai API konsol (UTF-16), jadi tidak bergantung
    codepage; tapi print() biasa dan output yang disedot ke file ikut encoding sys.stdout,
    dan di situ simbol di luar codepage = UnicodeEncodeError alias program mati di tengah jalan.
    """
    blob = "".join(_ICONS[mode].values())
    if HAS_RICH and _c.is_terminal:
        return True
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        blob.encode(enc, "strict")
        return True
    except (LookupError, UnicodeEncodeError):
        return False


def _pick_mode():
    """TIER otomatis — pilihan manual lewat IDX_ICONS selalu menang (lihat _icon_mode)."""
    if not HAS_RICH or not _c.is_terminal:
        # tanpa rich padding dihitung per karakter; output pipe tidak butuh gaya: pakai ASCII
        return "ascii"
    # konsol Windows lama diturunkan ke tier symbol/ascii oleh _icon_mode
    return "emoji"


def _icon_mode():
    global _ICON_MODE
    if _ICON_MODE is not None:
        return _ICON_MODE
    forced = (os.environ.get("IDX_ICONS") or "").strip().lower()
    mode = forced if forced in ("emoji", "symbol", "ascii", "none") else _pick_mode()
    if (mode == "emoji" and not forced and getattr(_c, "legacy_windows", False)
            and not _modern_terminal()):
        # konsol Windows lama: emoji tidak tergambar -> turun ke tier BMP
        mode = "symbol" if _console_utf8() else "ascii"
    while mode in ("emoji", "symbol") and not _encodable(mode):
        mode = _TIERS[_TIERS.index(mode) + 1]
    _ICON_MODE = mode
    return mode


def icon_mode():
    """Mode ikon yang sedang aktif — dipakai mode `ikon` dan untuk pelaporan."""
    return _icon_mode()


def icon_hint():
    """Satu baris saran kalau ikon belum versi penuh, supaya user tahu cara menaikkannya."""
    m = _icon_mode()
    if m in ("emoji", "none"):
        return ""
    piped = HAS_RICH and not _c.is_terminal
    if piped or not HAS_RICH:
        return ("Ikon versi ASCII dipakai karena keluaran tidak ke terminal langsung "
                + ("(rich tidak terpasang)" if not HAS_RICH else "(output disedot ke file/pipe)")
                + ".")
    if m == "symbol":
        return ("Konsol Windows lama (conhost) secara fisik tidak bisa menggambar emoji — simbol "
                "BMP (↑ ▌ √ ¤) dipakai sebagai ganti. Untuk ikon penuh: jalankan "
                "buka-terminal.cmd di folder ini (membuka Windows Terminal), atau paksa "
                'dengan $env:IDX_ICONS="emoji" — kalau jadi kotak, berarti konsolnya memang '
                "tidak punya glyph-nya.")
    return ('Terminal ini menolak simbol UTF-8 — ikon jadi ASCII (+ BELI, -- JUAL). Coba '
            '$env:IDX_ICONS="symbol", atau jalankan dari Windows Terminal untuk emoji penuh.')


_ICONS = {"emoji": _EMOJI, "symbol": _SYMBOL, "ascii": _ASCII, "none": {}}


def ic(key):
    """Ikon untuk key/verdik; pencocokan awalan untuk teks verdict panjang."""
    m = _icon_mode()
    if m == "none":
        return ""
    d = _ICONS[m]
    if key in d:
        return d[key]
    best = ""
    for k, v in d.items():                        # "BELI KUAT (…)" -> BELI KUAT
        if len(k) > len(best) and key.strip().upper().startswith(k):
            best = k
    return d.get(best, "")


def title_icon(title):
    # hanya segmen pertama sebelum "·" yang menentukan ikon: sisanya keterangan,
    # contoh "HASIL ANALISA · kesimpulan dari berita" harus 📊 bukan 📰
    t = _strip(title).upper().split("·")[0]
    for keys, name in _TITLE_ICON:
        if any(k in t for k in keys):
            return ic(name)
    return ""


def decorated_title(title):
    i = title_icon(title)
    return f"{i}  {title}" if i else title


def legend(sep=" · "):
    """Baris penjelasan ikon — jangan sampai user menebak-nebak makna simbol."""
    if _icon_mode() == "none":
        return ""
    picks = [("BELI", "BELI"), ("SIAGA", "SIAGA"), ("WATCHLIST", "WATCHLIST"),
             ("TUNGGU", "TUNGGU"), ("JUAL", "JUAL"), ("shield", "validasi data"),
             ("radar", "status data"), ("risk", "risiko"), ("book", "cara baca")]
    out = []
    for k, label in picks:
        i = _ICONS[_icon_mode()].get(k, "")
        if i:
            out.append(f"{i} {label}")
    return "LEGENDA  " + sep.join(out)



_TAG = re.compile(r"\[[^\[\]]*\]")
# hanya kurung siku yang benar-benar markup warna yang dibuang saat mode teks polos;
# "[Liputan6.com]" atau "[13-10-2026]" harus tetap terbaca
_STYLE = {"bold", "italic", "underline", "strike", "dim", "on", "not", "white", "black", "red",
          "green", "yellow", "blue", "magenta", "cyan", "grey", "gray", "bright", "dark",
          "orange", "default", "link", "rgb"}


def _is_tag(inner):
    inner = inner.strip("/")
    if not inner:
        return True
    for tok in inner.split():
        base = re.split(r"[_(;:]", tok)[0].rstrip("0123456789").lower()
        if not (tok.startswith("#") or base in _STYLE):
            return False
    return True


def _strip(s):
    return _TAG.sub(lambda m: "" if _is_tag(m.group(0)[1:-1]) else m.group(0), str(s))


def lit(s):
    """Teks mentah (judul/source berita) yang boleh mengandung kurung siku agar tidak dimakan rich."""
    return str(s).replace("[", "\\[") if HAS_RICH else str(s)


def verdict_markup(text):
    t = str(text)
    style, key = "white", ""
    for k, v in C_VERDICT.items():
        if t.startswith(k) and len(k) > len(key):
            style, key = v, k
    icon = ic(key) if key else ""
    if not HAS_RICH:
        return f"{icon} {t}" if icon and _icon_mode() != "none" else t
    return f"[{style}]{icon + ' ' if icon else ''}{t}[/]"


def grade_markup(g):
    return g if not HAS_RICH else f"[{C_GRADE.get(g, 'white')}]{g}[/]"


def mood_markup(m):
    return m if not HAS_RICH else f"[{C_MOOD.get(m, 'white')}]{m}[/]"


def signal_markup(v):
    up, dn = ic("up"), ic("down")
    if not HAS_RICH:
        return {1: f"{up} BELI", -1: f"{dn} JUAL"}.get(v, "- netral")
    if v == 1:
        return f"[green]{up} BELI[/]"
    if v == -1:
        return f"[red]{dn} JUAL[/]"
    return "[grey62]— netral[/]"


def header(title, sub=""):
    if HAS_RICH:
        t = decorated_title(title)
        inner = f"[bold]{t}[/]" + (f"\n[dim]{sub}[/]" if sub else "")
        _c.print(Panel(inner, border_style="cyan", box=box.ROUNDED, padding=(0, 1)))
    else:
        w = max(20, min(avail() - 2, 60))
        print(f"\n┌{'─' * w}")
        print(f"│ {_strip(decorated_title(title))}")
        if sub:
            print(f"│   {_strip(sub)}")
        print(f"└{'─' * w}")



_WIDE = ((0x1100, 0x115F), (0x2E80, 0x303E), (0x3041, 0x33FF), (0x3400, 0x4DBF),
         (0x4E00, 0x9FFF), (0xAC00, 0xD7A3), (0xF900, 0xFAFF), (0xFE30, 0xFE6F),
         (0xFF00, 0xFF60), (0xFFE0, 0xFFE6), (0x2600, 0x27BF), (0x1F000, 0x1FAFF))


def _cw(t):
    """Lebar kolom satu teks: 2 kolom untuk emoji/CJK, 0 untuk selector & zwj."""
    if HAS_RICH:
        return cell_len(t)
    n = 0
    for ch in t:
        o = ord(ch)
        if o in (0xFE0F, 0xFE0E, 0x200D):
            continue
        n += 2 if any(a <= o <= b for a, b in _WIDE) else 1
    return n


def _dw(s):
    """Lebar tampilan teks setelah markup dibuang — dalam KOLOM terminal, bukan jumlah karakter.

    emoji (termasuk yang pakai U+FE0F) menempati 2 kolom: kalau diukur dengan len() tiap baris
    ber-ikon bocor 1 kolom dan rich membungkusnya di tengah blok.
    """
    return _cw(_strip(s))


def _pad(s, w):
    """Rata kiri berdasarkan kolom terminal (ljust biasa salah hitung kalau ada emoji)."""
    return str(s) + " " * max(0, w - _cw(s))


def avail():
    """Lebar kolom efektif = lebar yang benar-benar dipakai rich untuk membungkus.

    Console sudah membaca ukuran terminal asli (Termux ikut) dan sudah memakai IDX_UI_WIDTH
    saat dibangun, jadi nilainya diambil dari sana supaya pembungkusan & pengukuran sepakat.
    """
    if HAS_RICH:
        try:
            return max(24, int(_c.width))
        except Exception:
            pass
    env = os.environ.get("IDX_UI_WIDTH")
    if env:
        try:
            return max(24, int(env))
        except ValueError:
            pass
    try:
        return max(24, os.get_terminal_size().columns)
    except Exception:
        return 80


def _natural(headers, rows, i, fixed=None):
    w = _dw(headers[i])
    for r in rows:
        if i < len(r):
            w = max(w, _dw(r[i]))
    return max(w, fixed) if fixed else w


def _records(headers, rows, caption):
    """Tata letak sempit: tiap baris jadi blok, kolom sisanya dipacking per baris. Tidak pernah terpotong."""
    W = avail() - 2
    labels = [_strip(h).lower() for h in headers[1:]]
    lw = min(max([len(l) for l in labels] or [0]), max(6, int(0.3 * W)))   # label sejajar, tak rakus lebar
    # penanda blok = ikon daftar, bukan ikon verdik: kolom lain (mis. rezim 'netral') bisa salah
    # dibaca sebagai verdik, sementara ikon verdik sudah tercetak di selnya sendiri.
    mark = ic("list")
    if caption:
        _c.print(f"[dim]{_strip(caption)}[/]")
    for r in rows:
        cells = [str(x) for x in r]
        _c.print(f"[bold]{(mark + ' ') if mark else ''}{cells[0] if cells else ''}[/]")
        line = ""
        for lbl, v in zip(labels, cells[1:]):
            if not _dw(v):
                continue                              # kolom kosong: jangan dicetak sebagai label menggantung
            tok = f"[grey62]{lbl.ljust(lw)}[/] {v}"
            if line and _dw(line) + _dw(tok) + 5 > W:   # "  ·  " = 5 karakter
                _c.print(f"  {line}")
                line = tok
            else:
                line = tok if not line else f"{line}  [grey58]·[/]  {tok}"
        if line:
            _c.print(f"  {line}")



def _longest_word(headers, rows, i):
    """Lebar terkecil yang masih muat untuk kolom yang boleh dibungkus (kata terpanjang)."""
    w = 1
    for t in [_strip(headers[i])] + [_strip(r[i]) for r in rows if i < len(r)]:
        for tok in str(t).split():
            w = max(w, _dw(tok))
    return w


def _render_table(headers, rows, caption, first_w, wrap):
    t = Table(header_style="bold cyan", caption=caption, box=box.SIMPLE_HEAVY, pad_edge=False,
              expand=False)
    for i, h in enumerate(headers):
        kw = {"no_wrap": i not in wrap, "justify": "left" if (i == 0 or i in wrap) else "right"}
        if i in wrap:
            kw["max_width"] = wrap[i]      # teks panjang turun baris, bukan dipotong "…"
            kw["overflow"] = "fold"
        if i == 0 and first_w:
            kw["min_width"] = first_w      # melebar kalau isi lebih panjang, jangan dipangkas
        t.add_column(h, **kw)
    for r in rows:
        t.add_row(*r)
    _c.print(t)


def table(headers, rows, caption=None, first_w=None):
    # header diseragamkan KAPITAL di tabel (mode rekaman sudahstrtolower labelnya sendiri)
    headers = [str(h).upper() for h in headers]
    rows = [[str(x) for x in r] for r in rows]
    if HAS_RICH:
        n = len(headers)
        W = avail()
        natural = [_natural(headers, rows, i, first_w if i == 0 else None) for i in range(n)]
        pad = 3 * n + 2                    # border + padding per kolom (terukur dari rich)
        if os.environ.get("IDX_UI_TABLE") == "1":
            _render_table(headers, rows, caption, first_w, {})
            return
        if sum(natural) + pad <= W:        # lapang: tabel rapi, semua sel satu baris
            _render_table(headers, rows, caption, first_w, {})
            return
        # tingkat 2: kolom teks yang kepanjangan dibungkus memakai SISA lebar tabel (bukan potongan
        # tetap), supaya teks panjang turun 2 baris rapi alih-alih tergerai 4 baris; angka tetap utuh
        cap = max(10, int(0.14 * W))
        cand = [i for i in range(n) if natural[i] > cap]
        if cand:
            rest = sum(natural[i] for i in range(n) if i not in cand)
            share = max(cap, (W - pad - rest) // len(cand))
            wrap = {i: min(natural[i], max(share, _longest_word(headers, rows, i))) for i in cand}
            if rest + sum(wrap.values()) + pad <= W:
                _render_table(headers, rows, caption, first_w, wrap)
                return
        _records(headers, rows, caption)   # terminal sempit: rekaman vertikal, tak ada yang hilang
        return

    clean = [[_strip(x) for x in r] for r in rows]
    hc = [_strip(h) for h in headers]
    W = avail()
    need = sum(max([_cw(hc[i])] + [_cw(c[i]) for c in clean]) if clean else _cw(hc[i])
               for i in range(len(hc))) + 4 * len(hc)
    if W < need:                      # terminal sempit: daftar vertikal, bukan kolom patah
        for c in clean:
            print(f"{ic('list')} {c[0]}" if icon_mode() != "none" else f"> {c[0]}")
            for i in range(1, len(hc)):
                if i < len(c) and c[i].strip():
                    print(f"   {hc[i].lower()}: {c[i]}")
            print("")
        return
    w = [max([_cw(hc[i])] + [_cw(r[i]) for r in clean]) if clean else _cw(hc[i])
         for i in range(len(hc))]
    if caption:
        print(f"— {_strip(caption)}")
    print("  ".join(_pad(hc[i], w[i]) for i in range(len(hc))))
    for r in clean:
        print("  ".join(_pad(r[i], w[i]) for i in range(len(r))))



def info_panel(title, lines, border="cyan"):
    t = decorated_title(title)
    if HAS_RICH:
        _c.print(Panel("\n".join(lines), title=f"[bold]{t}[/]", border_style=border,
                       box=box.ROUNDED, padding=(0, 1)))
    else:
        s = _strip(t)
        print(f"\n── {s} " + "─" * max(0, min(avail(), 50) - _cw(s) - 4))
        for l in lines:
            print(_strip(l))


def stage(label):
    """Penanda 'sedang mengerjakan' untuk langkah yang mencetak hasilnya sendiri di dalam blok.

    wait() tidak bisa dipakai di sini: frame spinner akan menimpa panel yang dicetak blok itu.
    """
    mark = ic("spark") or "…"
    if HAS_RICH and _c.is_terminal:
        _c.print(f"[grey62]{mark} {label}[/]")
    elif not HAS_RICH and sys.stdout.isatty():
        print(f"{mark} {_strip(label)}")


_prog = [False]
_prog_t0 = [None]
_depth = [0]          # wait() yang bersarang: hanya yang terluar yang menggambar

_SPIN = "|/-\\"


class wait:
    """Indikator pekerjaan lama (unduh data, walk-forward, RSS).

    Pakai:  with ui.wait("mengambil berita ISAT…"):

    Aturan main supaya loading tidak lagi kadang muncul kadang hilang:
      · frame pertama dicetak sinkron di __enter__ (bukan menunggu thread), jadi
        langkah secepat 0,1 s pun tetap terlihat;
      · baris penyelesaian ("ok/gagal + durasi") selalu dicetak, tanpa ambang min_show;
      · wait bersarang hanya menggambar yang terluar, supaya tidak saling hapus baris;
      · non-TTY (dipipe/di-log) tetap diam agar output bersih.
    """

    def __init__(self, label):
        self.label = label
        self.t0 = None
        self._stop = threading.Event()
        self._th = None
        tty = _c.is_terminal if HAS_RICH else sys.stdout.isatty()
        self.draw = bool(_depth[0] == 0 and tty)
        self.outer = False


    def _frame(self, i=0):
        el = time.time() - self.t0
        if HAS_RICH:
            # terminator lewat end= (rich menelan \r di dalam string); ini yang terbukti
            # benar-benar menimpa baris yang sama
            _c.print(f"[cyan]{_SPIN[i % len(_SPIN)]}[/] {self.label} [dim]{el:.0f}s[/]", end="\r")
        else:
            sys.stdout.write(f"\r{_SPIN[i % len(_SPIN)]} {_strip(self.label)} {el:.0f}s")
            sys.stdout.flush()

    def _run(self):
        i = 0
        while not self._stop.wait(0.2):
            i += 1
            self._frame(i)

    def __enter__(self):
        self.t0 = time.time()
        if self.draw:
            self.outer = True
            _depth[0] += 1
            self._frame()
            self._th = threading.Thread(target=self._run, daemon=True)
            self._th.start()
        return self

    def __exit__(self, *exc):
        el = time.time() - self.t0
        if self.outer:
            _depth[0] = max(0, _depth[0] - 1)
            self._stop.set()
            if self._th:
                self._th.join()
            tail = " " * max((_c.width if HAS_RICH else 80) - 1, 20)
            if HAS_RICH:
                _c.print(tail, end="\r")          # bersihkan sisa frame sebelum menulis baris ok
                tag = "[red]gagal[/]" if exc[0] else "[grey62]ok[/]"
                _c.print(f"{tag}  {self.label} [dim]({el:.1f}s)[/]")
            else:
                sys.stdout.write("\r" + tail + "\r")
                print(f"{'gagal' if exc[0] else 'ok'}  {_strip(self.label)} ({el:.1f}s)")
        return False



def progress(label, done, total):
    """Bar progres satu baris yang ditimpa; label None untuk menutup baris.

    Lebar bar & teks dihitung dari terminal asli agar tidak turun ke baris kedua
    (baris yang dibungkus menyisakan jelek saat ditimpa dengan \\r).
    """
    if label is None:
        if _prog[0]:
            _prog[0] = False
            _prog_t0[0] = None
            if HAS_RICH:
                _c.print("")
            else:
                sys.stdout.write("\n")
                sys.stdout.flush()
        return
    if _prog_t0[0] is None:
        _prog_t0[0] = time.time()
    frac = max(0.0, min(1.0, done / max(total, 1)))
    el = time.time() - _prog_t0[0]
    eta = el * (total - done) / max(done, 1) if done else 0.0
    W = avail()
    sisa = f" ≈{eta:.0f}s lagi" if 0 < done < total else ""
    # teks tetap: "done/total el% lbl%" -> sisakan ruang, bar yang menyesuaikan
    fixed = len(label) + len(sisa) + 22
    n_full = max(4, min(22, W - fixed))
    if len(label) + n_full + len(sisa) + 22 > W:      # masih sempit: pendekkan label
        label = label[:max(8, W - n_full - len(sisa) - 22)]
    n = int(frac * n_full)
    bar = "█" * n + "░" * (n_full - n)
    txt = (f"[cyan]{bar}[/] [bold]{done}/{total}[/] [dim]{el:.0f}s[/]"
           f"[dim]{sisa}[/] [bold]{label}[/] [dim]{frac*100:.0f}%[/]")
    if HAS_RICH and _c.is_terminal:
        _c.print(txt, end="\r")
        _prog[0] = True
    elif not HAS_RICH and sys.stdout.isatty():
        plain = f"{bar} {done}/{total} {el:.0f}s{sisa} {label} {frac*100:.0f}%"
        sys.stdout.write("\r" + plain[:W].ljust(W))
        sys.stdout.flush()
        _prog[0] = True



def decision_banner(act, why, final_score):
    big = f"{act}    (skor akhir {final_score:+.1f})"
    if HAS_RICH:
        _c.print(Panel(f"{verdict_markup(big)}\n[dim]{why}[/]", box=box.DOUBLE,
                       border_style="green" if act.startswith(("BELI",)) else
                       ("red" if "JUAL" in act or "HINDARI" in act else "yellow"), padding=(0, 1)))
    else:
        w = max(20, min(avail() - 2, 60))
        mark = ic("scale") or ">"
        print("\n" + "=" * w)
        print(f"  {mark} {act}  (skor akhir {final_score:+.1f})")
        print(f"    {why}")
        print("=" * w)

