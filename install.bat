@echo off
cd /d %~dp0
chcp 65001 > nul
title Bot Installer Setup (uv)

echo ========================================================
echo  TTS-Discord-Bot 環境構築インストーラー (uv ネイティブ)
echo ========================================================
echo.

REM --- Step 1: uv の確認・導入 ---
echo [1/3] uv の確認中...
where uv >nul 2>nul
if %errorlevel% equ 0 (
    echo  - uv は既にインストールされています。
    goto :CHECK_RUST
)

python -m uv --version >nul 2>nul
if %errorlevel% equ 0 (
    echo  - python -m uv を検出しました。
    goto :CHECK_RUST
)

echo  - uv をインストールしています...
pip install uv
if %errorlevel% neq 0 (
    echo [エラー] uv のインストールに失敗しました。
    pause
    exit /b 1
)

REM --- Step 2: Rust (cargo) の確認 ---
:CHECK_RUST
echo.
echo [2/3] Rust コンパイラ (cargo) の確認中...
where cargo >nul 2>nul
if %errorlevel% neq 0 (
    echo [エラー] Rust ^(cargo^) が見つかりません。rustup をインストールしてください。
    pause
    exit /b 1
)
echo  - Rust (cargo) を確認しました。

REM --- Step 3: uv sync (Python 3.11 + 依存ライブラリ + rust_core 自動ビルド) ---
echo.
echo [3/3] uv sync により仮想環境と Rust 拡張モジュール (rust_core) を構築しています...
set UV_LINK_MODE=copy
uv sync
if %errorlevel% neq 0 (
    python -m uv sync
    if %errorlevel% neq 0 (
        echo [エラー] 環境構築に失敗しました。
        pause
        exit /b 1
    )
)

echo.
echo ========================================================
echo  セットアップ完了！
echo  start.bat または `uv run main.py` で起動できます。
echo ========================================================
echo.
pause
