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
        print(f"\n┌{'─' * 60}")
        print(f"│ {_strip(title)}")
        if sub:
            print(f"│   {_strip(sub)}")
        print(f"└{'─' * 60}")


def table(headers, rows, caption=None, first_w=None):
    if HAS_RICH:
        t = Table(header_style="bold cyan", caption=caption, box=box.SIMPLE_HEAVY, pad_edge=False,
                  expand=False)
        for i, h in enumerate(headers):
            kw = {"no_wrap": True, "justify": "left" if i == 0 else "right"}
            if i == 0 and first_w:
                kw["width"] = first_w
            t.add_column(h, **kw)
        for r in rows:
            t.add_row(*[str(x) for x in r])
        _c.print(t)
    else:
        clean = [[_strip(x) for x in r] for r in rows]
        hc = [_strip(h) for h in headers]
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
        print(f"\n── {_strip(title)} " + "─" * max(0, 50 - len(_strip(title))))
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
    """Bar progres satu baris yang ditimpa; label None untuk menutup baris."""
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
    n = int(frac * 22)
    el = time.time() - _prog_t0[0]
    eta = el * (total - done) / max(done, 1) if done else 0.0
    bar = "█" * n + "░" * (22 - n)
    sisa = f" [dim]≈{eta:.0f}s lagi[/]" if 0 < done < total else ""
    txt = (f"[cyan]{bar}[/] [bold]{done}/{total}[/] [dim]{el:.0f}s[/]{sisa} "
           f"[bold]{label}[/] [dim]{frac*100:.0f}%[/]")
    if HAS_RICH and _c.is_terminal:
        _c.print(txt, end="\r")
        _prog[0] = True
    elif not HAS_RICH and sys.stdout.isatty():
        plain = f"{done}/{total} {label} {el:.0f}s {frac*100:.0f}%"
        sys.stdout.write("\r" + plain.ljust(max(60, len(plain)))[:80])
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
