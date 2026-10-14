"""Lapisan UI terminal: warna & tabel via rich, fallback teks polos."""

import os
import re
import sys
import threading
import time

try:
    from rich import box
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
    if not HAS_RICH:
        return _strip(text)
    style = "white"
    for k, v in C_VERDICT.items():
        if text.startswith(k):
            style = v
            break
    return f"[{style}]{text}[/]"


def grade_markup(g):
    return g if not HAS_RICH else f"[{C_GRADE.get(g, 'white')}]{g}[/]"


def mood_markup(m):
    return m if not HAS_RICH else f"[{C_MOOD.get(m, 'white')}]{m}[/]"


def signal_markup(v):
    if not HAS_RICH:
        return {1: "^ BELI", -1: "v JUAL"}.get(v, "- netral")
    if v == 1:
        return "[green]▲ BELI[/]"
    if v == -1:
        return "[red]▼ JUAL[/]"
    return "[grey62]— netral[/]"


def header(title, sub=""):
    if HAS_RICH:
        inner = f"[bold]{title}[/]" + (f"\n[dim]{sub}[/]" if sub else "")
        _c.print(Panel(inner, border_style="cyan", box=box.ROUNDED, padding=(0, 1)))
    else:
        w = max(20, min(avail() - 2, 60))
        print(f"\n┌{'─' * w}")
        print(f"│ {_strip(title)}")
        if sub:
            print(f"│   {_strip(sub)}")
        print(f"└{'─' * w}")



def _dw(s):
    """Lebar tampilan teks setelah markup dibuang (untuk memutuskan tabel vs rekaman)."""
    return len(_strip(s))


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
    if caption:
        _c.print(f"[dim]{_strip(caption)}[/]")
    for r in rows:
        cells = [str(x) for x in r]
        _c.print(f"[bold]> {cells[0] if cells else ''}[/]")
        line = ""
        for h, v in zip(headers[1:], cells[1:]):
            if not _dw(v):
                continue                              # kolom kosong: jangan dicetak sebagai label menggantung
            tok = f"[grey62]{_strip(h).lower()}[/] {v}"
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
            w = max(w, len(tok))
    return w


def _render_table(headers, rows, caption, first_w, wrap):
    t = Table(header_style="bold cyan", caption=caption, box=box.SIMPLE_HEAVY, pad_edge=False,
              expand=False)
    for i, h in enumerate(headers):
        kw = {"no_wrap": i not in wrap, "justify": "left" if i == 0 else "right"}
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
    headers = list(headers)
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
        # tingkat 2: kolom teks paling lebar dibungkus sampai muat; angka tetap utuh
        cap = max(10, int(0.14 * W))
        wrap = {i: max(cap, _longest_word(headers, rows, i)) for i in range(n) if natural[i] > cap}
        if wrap and sum(min(natural[i], wrap[i]) if i in wrap else natural[i] for i in range(n)) + pad <= W:
            _render_table(headers, rows, caption, first_w, wrap)
            return
        _records(headers, rows, caption)   # terminal sempit: rekaman vertikal, tak ada yang hilang
        return

    clean = [[_strip(x) for x in r] for r in rows]
    hc = [_strip(h) for h in headers]
    W = avail()
    need = sum(max(_dw(hc[i]), *(len(c[i]) for c in clean)) if clean else _dw(hc[i])
               for i in range(len(hc))) + 4 * len(hc)
    if W < need:                      # terminal sempit: daftar vertikal, bukan kolom patah
        for c in clean:
            print(f"> {c[0]}")
            for i in range(1, len(hc)):
                if i < len(c) and c[i].strip():
                    print(f"   {hc[i].lower()}: {c[i]}")
            print("")
        return
    w = [max(len(hc[i]), *(len(r[i]) for r in clean)) if clean else len(hc[i]) for i in range(len(hc))]
    if caption:
        print(f"— {_strip(caption)}")
    print("  ".join(hc[i].ljust(w[i]) for i in range(len(hc))))
    for r in clean:
        print("  ".join(r[i].ljust(w[i]) for i in range(len(r))))



def info_panel(title, lines, border="cyan"):
    if HAS_RICH:
        _c.print(Panel("\n".join(lines), title=f"[bold]{title}[/]", border_style=border,
                       box=box.ROUNDED, padding=(0, 1)))
    else:
        print(f"\n── {_strip(title)} " + "─" * max(0, min(avail(), 50) - len(_strip(title)) - 4))
        for l in lines:
            print(_strip(l))


_prog = [False]
_prog_t0 = [None]

_SPIN = "|/-\\"


class wait:
    """Spinner satu baris untuk pekerjaan lama (unduh data, kalkulasi walk-forward, RSS).

    Pakai:  with ui.wait("mengambil berita ISAT…"):
    Non-TTY (dipipe/di-log) tidak mencetak apa pun supaya output tetap bersih.
    """

    def __init__(self, label, min_show=0.5):
        self.label, self.min_show = label, min_show
        self.t0 = time.time()
        self._stop = threading.Event()
        self._th = None
        self.live = bool(HAS_RICH and _c.is_terminal)

    def _run(self):
        i = 0
        while not self._stop.is_set():
            el = time.time() - self.t0
            _c.print(f"[cyan]{_SPIN[i % len(_SPIN)]}[/] {self.label} [dim]{el:.0f}s[/]", end="\r")
            i += 1
            self._stop.wait(0.1)

    def __enter__(self):
        if self.live:
            self._th = threading.Thread(target=self._run, daemon=True)
            self._th.start()
        return self

    def __exit__(self, *exc):
        el = time.time() - self.t0
        if self.live:
            self._stop.set()
            if self._th:
                self._th.join()
            _c.print("\r" + " " * max(_c.width - 2, 20) + "\r", end="")
            if el >= self.min_show and not exc[0]:
                _c.print(f"[grey62]ok  {self.label} [dim]({el:.1f}s)[/][/]")
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
        print("\n" + "=" * 60)
        print(f"  ► {act}  (skor akhir {final_score:+.1f})")
        print(f"    {why}")
        print("=" * 60)
