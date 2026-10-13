#!/data/data/com.termux/files/usr/bin/sh
# setup-termux.sh — pasang idx-analyzer di Termux (Android).
# Jalankan dari folder project:  sh setup-termux.sh
#
# Kenapa tidak langsung `pip install numpy pandas`?
# Di Termux, numpy & pandas harus dikompilasi untuk ARM; kalau lewat pip
# sering gagal (butuh clang/gfortran/berjam-jam). Cara benar: pakai paket
# bawaan Termux yang sudah terkompilasi (`pkg install python-numpy python-pandas`),
# sisanya (yfinance, rich) murni Python jadi aman lewat pip.

set -e

if [ ! -d /data/data/com.termux ]; then
    echo "PERINGATAN: script ini untuk Termux di Android, bukan mesin biasa."
    echo "            Di PC, cukup: pip install -r requirements.txt"
fi

echo "==> 1/4  Update daftar paket"
pkg update -y

echo "==> 2/4  Pasang Python + numpy + pandas (versi siap-pakai Termux)"
# pkg install -y agar non-interaktif
pkg install -y python python-numpy python-pandas

echo "==> 3/4  Pasang yfinance + rich lewat pip (murni Python)"
pip install --upgrade pip
pip install yfinance rich

echo "==> 4/4  Cek impor & versi"
python - <<'PY'
import sys
print("python:", sys.version.split()[0])
for m in ("numpy", "pandas", "yfinance", "rich"):
    try:
        mod = __import__(m)
        v = getattr(mod, "__version__", "?")
        print(f"  OK  {m:8s} {v}")
    except Exception as e:
        print(f"  GAGAL {m:8s} {e}")
        sys.exit(1)
PY

echo
echo "Selesai. Smoke test (butuh internet):"
echo "  python idx.py cekdata"
echo "  python idx.py minggu AMRT"
echo "  python idx.py brief"
echo
echo "Tips: widths terminal Android sempit -> set  python idx.py ... lebih enak"
echo "      setelah 'pkg install termux-tools', putar HP ke landscape, atau:"
echo "      export IDX_UI_WIDTH=60"
