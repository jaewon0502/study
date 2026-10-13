#!/bin/zsh
cd "$(dirname "$0")" || exit 1
if [[ -x .venv/bin/python ]]; then
  .venv/bin/python main.py --diagnostic-ui
else
  python3 main.py --diagnostic-ui
fi
result=$?
if [[ $result -ne 0 ]]; then
  echo '실행에 실패했습니다. README.md의 설치 방법을 확인해 주세요.'
  read -r '?Enter를 누르면 닫힙니다. '
fi
exit "$result"
