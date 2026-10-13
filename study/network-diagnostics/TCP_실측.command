#!/bin/zsh
cd "$(dirname "$0")" || exit 1
if [[ -x .venv/bin/python ]]; then
  .venv/bin/python main.py --socket-ui
else
  python3 main.py --socket-ui
fi
