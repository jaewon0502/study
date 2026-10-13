# Network Diagnostics

현재 컴퓨터의 CPU·메모리, 네트워크 송수신 속도, TCP 연결 수를 실시간 그래프로 보여 주는 Python 프로그램입니다.
기본 화면은 **실시간 상태 · 대상 연결 진단 · TCP 내부 실측 · 패킷 흐름 · 패킷 헤더**로 구성하며 실제 측정값만 표시합니다.
Matplotlib 창에서 사용하거나 CLI로 JSON·CSV·PNG를 저장할 수 있습니다. TCP 학습용 시뮬레이션은 `--simulate`로 따로 실행합니다.

## 설치와 실행

Python 3.10 이상이 필요합니다. 의존성은 `matplotlib`, `psutil` 두 가지입니다.
이 폴더에서 다음 명령을 실행하세요.

**macOS / Linux**

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

**Windows PowerShell**

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

설치 후 macOS에서는 `macOS_실행.command` 또는 `내_컴퓨터_상태.command`를 더블클릭하면 실시간 상태 화면이 열립니다.
실행 파일은 같은 폴더의 `.venv/bin/python`을 우선 사용하고, 없으면 `python3`를 사용합니다.
`네트워크_진단.command`는 호스트를 지정해 DNS·TCP 연결을 확인하는 진단 탭으로 엽니다.
`TCP_실측.command`는 실제 기기 내부 TCP 전송 실험 탭을 엽니다. 창을 여는 것만으로 실험이나 캡처를 시작하지 않습니다.

그래픽 창에는 운영체제에서 지원하는 Matplotlib GUI backend가 필요합니다. GUI가 없는 환경에서는 CLI를 사용하세요.
한글이 깨지면 NanumGothic 또는 Noto Sans CJK KR 글꼴을 설치하세요.

## 현재 컴퓨터 상태

기본으로 열리는 **실시간 상태** 탭에서 CPU·메모리, 인터페이스별 송수신 속도, TCP ESTABLISHED·LISTEN 연결 수의 시간 변화를 봅니다.
IP 주소와 조회 가능한 개별 TCP 연결도 함께 표시합니다.

```sh
python main.py            # 실시간 컴퓨터 상태 화면으로 실행
python main.py --live     # 기본 실행과 동일
python main.py --status   # 그래픽 창 없이 실제 상태를 JSON으로 출력
```

- 메모리 사용량은 `전체 - 사용 가능` 기준입니다. 송수신 속도는 인터페이스별 바이트 증가량을 실제 경과 시간으로 나눠 Mbps로 계산합니다. 첫 표본은 측정 대기입니다.
- **다음 인터페이스**로 유선·무선·VPN·루프백 대상을 바꿉니다. 인터페이스를 합산하지 않으므로 VPN 트래픽을 이중 집계하지 않습니다.
- 링크 속도는 운영체제가 제공하는 정보이며 현재 전송률이나 인터넷 최대 속도가 아닙니다.
- 약 1초 간격으로 조회하며 최근 60개 표본을 메모리에 유지합니다. 느린 운영체제 응답은 조회 간격을 늘릴 수 있습니다.
- **수집 중지 / 수집 재개**로 관측을 멈추거나 다시 시작합니다. 중지할 때 진행 중이던 표본은 버립니다. 재개하면 그래프와 측정 기준을 새로 시작하므로 중지 기간을 CPU·송수신 속도에 섞지 않습니다.
- macOS는 `netstat`, 필요하면 `lsof`로 현재 계정에서 조회 가능한 연결을 읽습니다. `lsof`에는 TIME_WAIT 같은 커널 전용 상태가 없으므로 확인 불가로 표시합니다. OS와 권한에 따라 연결 목록이 제한될 수 있습니다.
- 권한을 올리거나 패킷 내용을 캡처하지 않습니다. 관측값을 외부 서버로 보내지 않습니다.
- **현재 상태 저장**은 `computer_status.png`, `computer_status.json`, `tcp_connections.csv`를 로컬 `exports/`에 만듭니다.

저장 결과에는 IP, 호스트, 연결 주소가 포함될 수 있습니다. `exports/`는 Git 추적에서 제외하며 실제 컴퓨터 기록은 저장소에 포함하지 않습니다.
운영체제 조회에는 [psutil](https://psutil.io/)을 사용합니다.

## 네트워크 진단

**대상 연결 진단** 탭에서 호스트, 포트, 시도 횟수, 제한 시간을 입력한 뒤 **진단 시작**을 누릅니다.
각 연결 시도가 끝날 때마다 그래프가 갱신되고, 마지막에는 전체 결과를 요약합니다. **진단 중단**으로 멈추거나 완료된 결과를 저장할 수 있습니다.
기본 5회, 최대 20회의 한 묶음만 실행하며 외부 접속을 자동으로 반복하지 않습니다.
프로그램을 열기만 해서는 외부 호스트에 접속하지 않습니다. 진단 버튼을 누르거나 `--diagnose`를 지정할 때만 접속합니다.

```sh
# 진단 탭으로 열기: 실행 버튼을 누르기 전에는 접속하지 않음
python main.py --diagnostic-ui

# 지정한 호스트의 단일 포트에 연결을 5회 시도하고 JSON 출력
python main.py --diagnose example.com --port 443 --attempts 5 --timeout 2

# 같은 진단의 그래프·원자료를 로컬 폴더에 저장
python main.py --diagnose example.com --port 443 --attempts 5 --timeout 2 --export exports/check
```

- 호스트는 도메인 또는 IP 주소이며 URL 전체가 아닙니다. 포트 범위는 1~65535, 횟수는 1~20회, 제한 시간은 0.2~10초입니다.
- DNS 조회 후 **한 호스트의 한 TCP 포트에 연결하고 즉시 닫습니다.** HTTP 요청, TLS 검사, 파일 전송, 포트 스캔은 하지 않습니다.
- DNS 시간에는 이름 조회 프로세스 시작과 시스템 캐시의 영향도 포함됩니다. DNS 서버 자체의 RTT가 아닙니다.
- DNS 주소가 여러 개면 한 회차의 제한 시간 안에서 다른 주소도 시도합니다. 연결 시간에는 해당 회차의 이전 주소 시도 시간이 포함됩니다. 요약 지연 통계에는 성공한 회차만 포함합니다.
- 연결 시간은 TCP 연결 함수의 경과 시간입니다. ICMP ping의 RTT나 애플리케이션 응답 시간이 아닙니다.
- 실패 비율은 해당 TCP 연결 시도 중 실패한 비율이며 패킷 손실률이 아닙니다. 연결 거부, 시간 초과, DNS 오류는 서로 다른 결과입니다.
- 특정 서버·포트의 연결 결과만으로 인터넷 전체의 정상 여부, 회선 최대 속도, 서비스 정상 동작을 판정할 수 없습니다.

저장 파일은 `diagnostics.png`, `diagnostics.json`, `probes.csv`입니다.
CLI 종료 코드는 모든 요청 회차 성공 시 `0`, 진단 실패·일부 실패 시 `1`, 잘못된 입력·필수 의존성 오류 시 `2`입니다.
JSON 결과는 표준 출력, 저장 경로 등의 안내는 표준 오류로 출력합니다.

## 실제 TCP 내부 측정

**TCP 내부 실측** 탭의 **측정 시작**을 누르면 이 컴퓨터의 `127.0.0.1`에 임시 TCP 서버와 클라이언트를 만들고 시험용 바이트를 전송합니다.
약 0.25초마다 실제 운영체제 값과 송수신 결과가 그래프에 반영됩니다. 사용자 파일은 읽지 않으며 외부 서버로 전송하지 않습니다.

```sh
python main.py --socket-ui
python main.py --tcp-test --duration 10 --chunk-kib 16 --export exports/socket
```

| 그래프 | 실제 측정 출처와 해석 |
|---|---|
| cwnd · ssthresh | 이 프로그램의 TCP 소켓에서 읽은 커널 혼잡 제어 값. 바이트를 KiB로 변환하며 큰 초기 ssthresh를 함께 보기 위해 로그 구간이 있는 축 사용 |
| RTT · SRTT · RTO | macOS `TCP_CONNECTION_INFO`가 보고한 ms 값. 앱 응답 왕복 시간은 별도로 직접 측정 |
| 송신 측 전송률 · 수신 측 처리율 | 같은 방향의 시험 데이터를 보낸 바이트와 서버가 실제 읽은 바이트를 각각 경과 시간으로 나눈 값 |
| 송신 · 수신 윈도우 | 운영체제가 제공한 바이트 값 |
| 송신 버퍼 | 커널 송신 버퍼에 남은 바이트. 전송 후 ACK를 기다리는 bytes in flight와 같지 않음 |
| 재전송 | 이 소켓의 커널 재전송 패킷 누계. 임의의 손실률을 넣지 않음 |

- 루프백은 **현재 기기의 TCP 동작**을 측정합니다. 실제 Wi-Fi·인터넷 경로를 통과하지 않으며, 일정 간격으로 전송하므로 회선 최대 속도 측정이 아닙니다.
- 기본 10초, 최대 30초이며 표본당 1~64 KiB, 전체 최대 20 MiB로 제한합니다. 중단 시 연결을 정리하고 완료된 표본을 남깁니다.
- 커널 상세 값은 macOS에서 지원합니다. Linux·Windows에서도 실제 로컬 전송량과 앱 응답 시간은 측정하지만 지원하지 않는 커널 값은 `확인 불가`로 표시합니다.
- 다른 앱의 소켓에 대한 cwnd는 조회하지 않습니다. GBN/SR 동작이나 Slow Start 구간을 실제 기기 값처럼 만들어 넣지 않습니다.
- 저장: `tcp_socket.png`, `tcp_socket.json`, `tcp_samples.csv`.

커널 구조체 기준: [Apple XNU tcp.h](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/netinet/tcp.h),
[Apple XNU tcp_usrreq.c](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/netinet/tcp_usrreq.c).

## 실제 TCP 패킷 흐름과 헤더

**패킷 흐름 / 패킷 헤더** 탭은 실제 캡처에서 읽은 SEQ·ACK·플래그·원시 윈도우·페이로드 길이를 연결별로 표시합니다.
**다음 TCP 연결**로 연결을 바꾸면 양쪽 탭이 같은 선택을 유지합니다. 수집된 패킷이 없으면 빈 화면과 이유를 보여 줍니다.
인터페이스 이름은 실시간 상태에서 확인할 수 있습니다. macOS의 일반적인 Wi-Fi 이름은 `en0`, 루프백은 `lo0`이며 기기마다 다를 수 있습니다.

### 권한이 이미 있는 경우

GUI에서 인터페이스와 수집 시간을 입력한 뒤 **수집 시작**을 누르세요. 시작한 뒤 약 0.5초 간격으로 새 기록을 반영합니다.
`tcpdump`가 설치되어 있어야 하며, 프로그램은 자동으로 설치하거나 권한을 변경하지 않습니다.

```sh
python main.py --packet-ui
python main.py --capture en0 --duration 30 --max-packets 2000 --export exports/capture
```

수집 시간은 최대 120초, CLI 패킷 수는 최대 10,000개이며 GUI는 2,000개입니다.
일반 계정에서 권한이 없으면 오류를 표시하고 끝납니다.

### macOS에서 캡처 권한 오류가 나는 경우

1. `macOS_패킷_수집.command`를 직접 실행하세요. 기본 인터페이스는 `en0`입니다. 루프백은 터미널에서 `./macOS_패킷_수집.command lo0`로 지정합니다.
2. 터미널의 `sudo` 프롬프트에 Mac 암호를 직접 입력하세요. **Apple의 tcpdump만** 관리자 권한으로 실행되고 GUI·Python은 일반 계정으로 실행됩니다.
3. GUI의 **패킷 흐름**에서 파일을 `captures/live_tcp.jsonl`로 두고 **파일 읽기 / 따라가기**를 누르세요. 파일에 추가되는 실제 패킷이 그래프에 나타납니다.
4. 수집을 끝내려면 **수집 터미널에서 Control+C**를 누르세요. 2,000개를 받으면 자동 종료합니다. 조용한 인터페이스에서는 시간 제한 없이 기다릴 수 있습니다. **GUI의 수집 중단은 파일 읽기만 중단하며 터미널의 수집 프로세스는 중단하지 않습니다.**

기존 `live_tcp.jsonl`은 `captures/previous.*`로 보관합니다. 새 파일은 현재 사용자만 읽을 수 있는 권한으로 만듭니다.
GUI의 파일 따라가기는 시작부터 읽으며 기본 30초, 최대 120초입니다. 필요하면 다시 눌러 누적 파일을 읽습니다.
루프백 패킷을 관찰하려면 `lo0`로 수집을 시작한 뒤 **TCP 내부 실측**을 실행하세요.

### 기존 캡처 파일 읽기

GUI의 파일 경로에 PCAP·PCAPNG를 입력하고 **파일 읽기 / 따라가기**를 누르거나 CLI를 사용하세요.

```sh
python main.py --read-capture sample.pcapng --export exports/imported
python main.py --read-capture captures/live_tcp.jsonl --duration 30
```

- PCAP·PCAPNG 입력은 최대 100 MiB, 해석 시간은 최대 20초입니다. JSONL도 크기·레코드 수·시간 제한을 적용합니다.
- 캡처 순간에는 `tcpdump -s 160`으로 패킷 앞부분을 읽으므로 일부 페이로드가 메모리에 들어올 수 있습니다. 저장·GUI에는 검증한 TCP 메타데이터만 남기고 애플리케이션 문자열·원시 바이트는 버립니다. PCAP 원본을 새로 저장하지 않습니다.
- 캡처된 주소·포트·타이밍 자체도 기기 기록입니다. `captures/`, `exports/`, PCAP·PCAPNG·JSONL은 Git 추적에서 제외합니다. 원자료는 외부로 업로드하지 않습니다.
- 윈도우는 **헤더 원시 16비트 값**입니다. SYN의 배율 옵션은 원자료에 따로 남기며, 중간부터 캡처한 연결에 배율을 추정하지 않습니다.
- SEQ·ACK 그래프는 처음 관측한 바이트 번호를 기준으로 표시합니다. 출력에 없는 SEQ는 확인 불가로 남깁니다. 캡처 페이로드 합계는 재전송을 포함할 수 있어 유효 전송량과 다릅니다.
- 패킷 흐름의 화살표는 관측한 방향만 나타냅니다. 도착·손실·재전송 여부나 cwnd를 패킷 헤더만으로 단정하지 않습니다. 일부 플래그가 안 보일 수 있으며 암호화된 내용은 해독하지 않습니다.
- 저장: `tcp_capture_flow.png`, `tcp_capture_packets.png`, `tcp_capture.json`, `tcp_packets.csv`.

캡처 명령 기준: [tcpdump 문서](https://www.tcpdump.org/manpages/tcpdump.1.html).

## TCP 학습용 시뮬레이션

실측 기본 화면에는 모의 데이터가 없습니다. 강의의 GBN/SR·손실 조건 비교가 필요할 때만 이 모드를 실행하세요.

```sh
python main.py --simulate
```

| 탭 | 내용 |
|---|---|
| TCP 파형 | cwnd, seq/ACK, 유효 전송률, RTT/SRTT, 미확인 데이터와 rwnd, 손실·재전송 |
| 연결 · 패킷 | SYN → SYN,ACK → ACK와 FIN 종료 흐름, flags·seq·ack·length·window |
| GBN · SR 비교 | 같은 손실 상황에서 Stop-and-Wait / Go-Back-N / Selective Repeat의 재전송·버퍼 비교 |

TCP 설정을 바꾼 뒤 **다시 실행**을 누르세요. 같은 설정과 시드는 같은 결과를 만듭니다.
**손실 0% / 손실 8%** 버튼으로 손실 전후를 비교할 수 있습니다. 슬라이더만 움직이면 기존 결과는 바뀌지 않습니다.
GBN/SR은 별도의 고정 손실 실험이며 TCP 설정 중 RTT만 공유합니다.

```sh
# 창 없이 모의 실험 결과 저장
python main.py --no-gui --export exports/default
python main.py --no-gui --loss 0 --export exports/no_loss
python main.py --no-gui --loss 5 --seed 21 --export exports/recovery
python main.py --no-gui --rtt 200 --bandwidth 0.5 --window 4000 --export exports/slow
```

시뮬레이션은 `01_tcp_graphs.png`, `02_connection.png`, `03_gbn_sr.png`와 다음 원자료를 출력합니다.

| 파일 | 내용 |
|---|---|
| `packets.csv` | flags·seq·ack·length·window, 송수신 시간, 손실·재전송 |
| `samples.csv` | 이벤트별 cwnd·ssthresh·inflight·rwnd·전달량·RTO·알고리즘 구간 |
| `rtt.csv` | RTT·SRTT·RTTVAR·RTO |
| `events.csv` | 상태 변화, 손실, 중복 ACK, 빠른 재전송, 타임아웃 |
| `rdt_comparison.csv`, `rdt_*.csv` | 프로토콜별 요약과 송수신 기록 |
| `summary.json` | 설정, 완료 여부, 요약 수치, 모델의 가정 |

### 그래프 해석

- 파형은 모의 패킷과 프로토콜 상태의 시간 변화입니다. 케이블의 전압, Wi-Fi 전파, 실제 패킷 캡처를 측정하지 않습니다. 실제 Wireshark·PCAP·TLS 분석 실습을 대신하지 않습니다.
- cwnd·ssthresh는 MSS 단위, seq/ACK·rwnd·inflight는 바이트 단위입니다. 데이터량의 kB는 1,000바이트입니다.
- cwnd 가로축은 `경과 시간 / 설정한 왕복 전파 지연`입니다. 직렬화·대기로 ACK 간격이 달라지므로 정확한 ACK 라운드 번호는 아닙니다.
- Slow Start는 새 ACK마다 윈도우를 키우며 혼잡 회피는 더 완만하게 증가합니다. 붉은 점선은 RTO 또는 중복 ACK 3개로 손실을 감지한 시점입니다.
- seq는 세그먼트의 첫 바이트, ACK는 다음에 기대하는 바이트 번호입니다. SYN·FIN은 1씩 소비하며 데이터 없는 ACK는 소비하지 않습니다.
- 유효 전송률은 순서대로 새로 전달된 고유 payload를 기준으로 합니다. 중복 재전송·헤더는 제외하고 평균은 연결 설정·종료를 포함한 경과 시간으로 나눕니다. 버퍼의 데이터가 한꺼번에 전달되면 구간별 전송률은 링크 속도보다 높게 보일 수 있습니다.
- `packets.csv`의 `time`은 송신 큐에 넣은 시각, `wire_time`은 링크 송신 시작 시각입니다. 빈 `arrival`은 손실 또는 시간 제한에 따른 미도착이므로 `lost`·`received`를 함께 보세요.
- `completed`는 전체 payload의 ACK 수신, `connection_closed`는 종료 절차 완료입니다. 시간 제한에 걸린 실행은 완료로 취급하지 않습니다.

### 모델의 범위

TCP는 단일 연결·단방향 데이터·즉시 누적 ACK·고정 MSS·고정 rwnd의 Reno 학습 모델입니다.
방향별 직렬화·전파 지연을 반영하며 DATA만 설정 확률로 손실되고 SYN·FIN·ACK는 손실되지 않습니다.
초기 cwnd=1 MSS, ssthresh=16 MSS, MSS=1000B는 교육용 가정이며 운영체제 기본값을 뜻하지 않습니다.
수신 애플리케이션은 순서대로 전달된 데이터를 즉시 읽습니다. Zero Window, SACK, ECN, CUBIC, BBR, 지연 ACK, 비트 오류, seq 순환은 구현하지 않았습니다.
TIME_WAIT를 기록하되 실제 2×MSL 대기는 생략합니다. RTO는 1초부터 시작하고 최소 1초를 적용합니다. 재전송이 섞인 ACK는 RTT 추정에서 제외합니다.

GBN/SR은 12개 패킷, window=4, 3번 DATA의 첫 전송만 손실, ACK 무손실, timeout=1초, 패킷당 8ms 직렬화의 독립 실험입니다.
버퍼는 순서 밖 수신 패킷의 최대 개수입니다. 실제 SR에 필요한 시퀀스 번호 순환 대신 순환하지 않는 패킷 번호를 씁니다.
다른 조건은 Python API로 지정할 수 있습니다.

```python
from rdt_sim import compare_rdt

for result in compare_rdt(packet_count=12, window=6, lost_packet=5, rtt_ms=100):
    print(result['name'], result['retransmissions'], result['peak_buffer_packets'])
```

개념·알고리즘 기준: [RFC 9293](https://www.rfc-editor.org/rfc/rfc9293.html),
[RFC 5681](https://www.rfc-editor.org/rfc/rfc5681.html), [RFC 6298](https://www.rfc-editor.org/rfc/rfc6298.html).
전체 TCP 표준 준수 구현을 표방하지 않습니다.

## 코드와 검사

| 파일 | 역할 |
|---|---|
| `main.py` | GUI, 입력, CLI, 파일 출력 |
| `diagnostics.py` | DNS 조회와 TCP 연결 진단 |
| `system_monitor.py`, `live_plots.py` | 읽기 전용 컴퓨터 상태 조회와 그래프 |
| `socket_metrics.py` | 실제 루프백 TCP 전송과 macOS 커널 측정 |
| `packet_capture.py` | TCP 메타데이터 캡처, PCAP·PCAPNG 해석, JSONL 실시간 읽기 |
| `observed_plots.py` | 실제 소켓과 패킷의 그래프·표 |
| `tcp_sim.py`, `rdt_sim.py` | TCP 및 신뢰적 전송 프로토콜 모의 실험 |
| `plots.py` | 시뮬레이션 그래프·표·흐름도 |
| `tests/` | 모델, 진단, 상태 조회, 그래프 검사 |

```sh
# 가상환경을 활성화한 상태에서
python -m unittest discover -s tests -v

# macOS / Linux: 검사 스크립트, 필요하면 Python 경로 지정
./verify.sh
PYTHON=.venv/bin/python ./verify.sh
```
