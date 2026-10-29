@echo off
cd /d %~dp0
chcp 65001 > nul
title TTS-Discord-Bot Launcher

echo ===================================================
echo  TTS-Discord-Bot 起動 (uv run)
echo ===================================================
echo.

set UV_LINK_MODE=copy

where uv >nul 2>nul
if %errorlevel% equ 0 (
    uv run python main.py %*
) else (
    if not exist ".venv\Scripts\python.exe" (
        echo [エラー] 仮想環境が見つかりません。先に install.bat を実行してください。
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" main.py %*
)

pause
