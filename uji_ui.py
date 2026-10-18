"""Loop uji §7 docs/DESAIN-UI.md untuk layar yang memakai zona dua kolom (`ui.zone`).

Prinsip: tier ikon, lebar terminal, dan tata letak (sejajar vs bertumpuk) hanya boleh mengubah
TAMPILAN. Multiset digit satu layar harus identik di semua kombinasi — termasuk fallback tanpa
`rich`. Kalau ada selisih, digit yang berbeda ikut dicetak supaya bisa dinilai apakah itu
perubahan data (harga/skor yang memang bergeser antar menit) atau isi yang hilang karena layout;
hanya yang kedua yang merupakan bug UI.

`norm.py` dipakai, bukan pipeline sed: di layar sempit satu angka bisa terbelah ke dua baris
(`live 16:00 WIB 50` | `jam`), dan filter per-baris lalu meninggalkan '50' palsu di kolom digit.

Pakai: python uji_ui.py            # semua layar di DAFTAR
       python uji_ui.py beranda    # satu layar saja
"""
import os
import subprocess
import sys
from collections import Counter

import norm

# layar yang sudah memakai ui.zone(); tambah nama baru di sini saat P2 melebar
DAFTAR = {"beranda": ["beranda"], "brief": ["brief", "--no-export"], "check": ["check", "BBCA"]}

TIER = ["emoji", "symbol", "ascii", "none"]
# 110 = ambang ui.zone: 100 masih bertumpuk, 110/118/140 sudah sejajar; 40/60 ikut tabel mode
# rekaman. brief/check menghitung ulang engine (mahal) jadi hanya ujung-ujung ambang.
LEBAR = {"beranda": [40, 60, 100, 110, 140], "brief": [100, 140], "check": [100, 140]}


def jalan(argv, env):
    r = subprocess.run([sys.executable, "idx.py"] + argv, env=dict(os.environ, **env),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise SystemExit(f"idx.py {' '.join(argv)} {env} gagal (rc={r.returncode}):\n"
                         + r.stderr[-700:])
    return r.stdout


def utama(pilih):
    os.makedirs("tmp-uji", exist_ok=True)
    for nama, argv in DAFTAR.items():
        if pilih and nama not in pilih:
            continue
        hasil = {}
        for w in LEBAR[nama]:
            for t in TIER:
                out = jalan(argv, {"IDX_UI_WIDTH": str(w), "IDX_ICONS": t})
                open(f"tmp-uji/{nama}_{w}_{t}.txt", "w", encoding="utf-8").write(out)
                hasil[f"w{w} {t}"] = Counter(norm.digits(out))
                print(f"{nama:8s} w={w:3d} {t:6s} {sum(hasil[f'w{w} {t}'].values()):5d} digit",
                      flush=True)
        for w in (100, 140):
            out = subprocess.run(
                [sys.executable, "-c", "import ui; ui.HAS_RICH=False; import idx; idx.main(%r)"
                 % argv], env=dict(os.environ, IDX_UI_WIDTH=str(w)), capture_output=True,
                text=True, encoding="utf-8", errors="replace").stdout
            open(f"tmp-uji/{nama}_{w}_norich.txt", "w", encoding="utf-8").write(out)
            hasil[f"w{w} tanpa-rich"] = Counter(norm.digits(out))
            print(f"{nama:8s} w={w:3d} tanpa rich {sum(hasil[f'w{w} tanpa-rich'].values()):5d} "
                  "digit", flush=True)
        acuan = hasil[f"w{LEBAR[nama][0]} emoji"]
        beda = [k for k, v in hasil.items() if v != acuan]
        print(f"  -> {nama}: {len(hasil)} kombinasi, "
              f"{'SEMUA IDENTIK' if not beda else 'BEDA di ' + str(beda)}", flush=True)
        for k in beda:
            print(f"     {k}: hilang {dict(acuan - hasil[k])} / lebih {dict(hasil[k] - acuan)}",
                  flush=True)


if __name__ == "__main__":
    utama(sys.argv[1:])
