#!/bin/zsh
set -e

cd "$(dirname "$0")"

echo "Starting AI Value Investor local console..."
echo "Project: $(pwd)"
echo

if command -v poetry >/dev/null 2>&1; then
  poetry run python -m src.web.local_console --open
elif command -v python3 >/dev/null 2>&1; then
  python3 -m src.web.local_console --open
else
  echo "Python 3 was not found. Please install Python or run this project through Poetry."
  read "?Press Enter to close..."
fi
