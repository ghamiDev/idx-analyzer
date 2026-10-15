@echo off
rem Buka IDX Decision Desk di Windows Terminal -> ikon emoji penuh + warna penuh.
rem Konsol PowerShell/cmd lama (conhost) tidak bisa menggambar emoji, jadi program
rem otomatis turun ke simbol BMP. Pakai file ini kalau mau versi emoji.
rem   buka-terminal.cmd                  -> menu interaktif
rem   buka-terminal.cmd brief            -> dashboard harian
rem   buka-terminal.cmd check BBRI       -> keputusan 1 saham
setlocal
set "AUMID=Microsoft.WindowsTerminal_8wekyb3d8bbwe!App"
set "DIR=%~dp0."
set "ARGS=menu"
if not "%~1"=="" set "ARGS=%*"

wt --startingDirectory "%DIR%" python idx.py %ARGS%
if not errorlevel 9009 goto :end

rem perintah `wt` tidak ada (alias mati: Settings > Apps > App execution aliases),
rem tapi aplikasinya terpasang -> buka lewat shell:AppsFolder
start "" "shell:AppsFolder\%AUMID%" new-tab --startingDirectory "%DIR%" python idx.py %ARGS%
if errorlevel 1 echo Kalau jendela Terminal tidak muncul, nyalakan alias "Terminal" di Settings,
if errorlevel 1 echo atau tetap di konsol ini dengan simbol BMP:  set IDX_ICONS=symbol

:end
endlocal
