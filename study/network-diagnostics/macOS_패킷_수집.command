#!/bin/zsh
# User-initiated privilege boundary: only Apple's tcpdump runs as administrator.
set -o pipefail
set -o noclobber
umask 077
cd "$(dirname "$0")" || exit 1
python_bin=python3
if [[ -x .venv/bin/python ]]; then python_bin=.venv/bin/python; fi
"$python_bin" -c 'import packet_capture' || exit 1
interface=${1:-en0}
echo '현재 기기의 TCP 패킷 헤더를 수집합니다.'
echo "인터페이스: $interface · 최대 2,000개 · 종료: Control+C"
echo 'Mac 암호는 아래 sudo 프롬프트에 직접 입력하세요. 입력 내용은 표시되지 않습니다.'
echo '캡처 순간에는 패킷 앞부분을 읽지만, 저장 파일에는 검증된 TCP 메타데이터만 남깁니다.'
mkdir -p captures || exit 1
if [[ -L captures || ! -d captures || -L captures/live_tcp.jsonl ]]; then
  echo 'captures 경로가 심볼릭 링크입니다. 일반 폴더를 사용하세요.'
  exit 1
fi
if [[ -e captures/live_tcp.jsonl ]]; then
  if [[ ! -f captures/live_tcp.jsonl ]]; then exit 1; fi
  archive_dir=$(mktemp -d "$PWD/captures/previous.XXXXXX") || exit 1
  mv captures/live_tcp.jsonl "$archive_dir/live_tcp.jsonl" || exit 1
fi
echo 'GUI의 [패킷 흐름] → [파일 읽기 / 따라가기]를 누르면 실시간으로 표시됩니다.'
echo '파일: captures/live_tcp.jsonl · GUI 수집 시간 기본 30초, 최대 120초'
sudo /usr/sbin/tcpdump -l -nn -tt -S -K -p --immediate-mode -s 160 -i "$interface" -c 2000 tcp |
  "$python_bin" packet_capture.py --sanitize > captures/live_tcp.jsonl
result=$?
echo "수집 종료. 저장 위치: $PWD/captures/live_tcp.jsonl"
read -r '?Enter를 누르면 닫힙니다. '
exit "$result"
