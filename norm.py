"""Normaliser output terminal untuk uji determinisme (docs/DESAIN-UI.md §7).

Digit harus tetap sama walau tier ikon, lebar layar, atau tata letak (zona dua kolom vs
tumpuk) berubah. Yang dibuang hanya angka yang memang berubah tiap detik/menit: jam, umur
kutipan ('50 jam', '±3 mnt'), umur berita ('2 hari lalu'). Garis kotak dibuang lebih dulu:
di layar sempit angka umur bisa terbelah ke baris berikutnya ('50' | 'jam') sehingga pola
apa pun gagal mengenalinya kalau baris dipegang satu per satu.

BACA FILE, BUKAN STDIN. Pipe git-bash datang sebagai CP1252 sementara filenya UTF-8, jadi
lewat stdin karakter kotak tidak ikut terbuang dan hash-nya palsu. `python idx.py x > f.txt`
menyimpan UTF-8, jadi `python norm.py f.txt` benar.

Pakai: python norm.py <file> [<file> ...]  -> satu baris 'jumlah hash' per file.
"""
import hashlib
import re
import sys

BUANG = [r"\d{1,2}:\d{2}", r"\d+ *(mnt|menit|jam|detik)", r"\d+ *hari lalu"]
KOTAK = r"[│┌┐└┘├┤┬┴┼╭╮╯╰╱╲─═▓░●▏▕→←↑↓]"
ANGKA = re.compile(r"\d[\d.,]*")


def digits(teks):
    sat = " ".join(b for b in re.sub(KOTAK, " ", teks).splitlines() if "hari lalu" not in b)
    for pola in BUANG:
        sat = re.sub(pola, " ", sat)
    return sorted(ANGKA.findall(sat))


def ringkas(teks):
    d = digits(teks)
    return len(d), hashlib.md5(str(d).encode()).hexdigest()


if __name__ == "__main__":
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8", errors="replace") as fh:
            n, h = ringkas(fh.read())
        print(f"{n:5d} {h}  {path}")
