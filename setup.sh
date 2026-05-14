#!/bin/bash
# setup.sh — ローカルで一発実行: 依存ライブラリ install → 価格取得 → ブラウザで開く
# Usage: ./setup.sh
# Requirements: Python 3.10+

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 色付きログ用
green() { printf "\033[32m%s\033[0m\n" "$1"; }
yellow() { printf "\033[33m%s\033[0m\n" "$1"; }
red() { printf "\033[31m%s\033[0m\n" "$1"; }

# Python 確認
if ! command -v python3 >/dev/null 2>&1; then
  red "Error: python3 が見つかりません。Homebrew で 'brew install python' してください。"
  exit 1
fi
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
green "✓ Python $PYVER detected"

# 仮想環境 (推奨) - 既存があれば再利用
VENV_DIR=".venv"
if [ ! -d "$VENV_DIR" ]; then
  yellow "Creating virtual environment in $VENV_DIR ..."
  python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
green "✓ venv activated"

# 依存インストール (静かに)
yellow "Installing yfinance ..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
green "✓ dependencies installed"

# 価格取得 + HTML 生成
yellow "Fetching prices for 93 tickers (this takes ~30-60 seconds)..."
python fetch_prices.py
green "✓ index.html generated"

# ブラウザで開く (macOS / Linux 両対応)
if command -v open >/dev/null 2>&1; then
  open index.html
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open index.html
else
  yellow "Open index.html manually in your browser."
fi

green "🎉 Done! index.html should be open in your browser."
echo ""
echo "To refresh anytime, just run:    ./setup.sh"
echo "To deploy to GitHub Pages, see:  README.md"
