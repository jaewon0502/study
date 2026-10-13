"""Measure one real, bounded TCP connection owned by this program on loopback.

Darwin ABI: Apple's XNU bsd/netinet/tcp.h, ``tcp_connection_info`` and
TCP_CONNECTION_INFO (0x106); verified against the installed macOS SDK header.
https://github.com/apple-oss-distributions/xnu/blob/main/bsd/netinet/tcp.h
Socket contract: https://docs.python.org/3/library/socket.html

The receiver counts application bytes read, not packets or TCP acknowledgments.
The one-byte application reply times a full application exchange, not kernel RTT.
"""

from dataclasses import asdict, dataclass
from datetime import datetime
import math
import socket
import struct
import sys
import threading
import time


SOURCE = "local_loopback_tcp_measurement"
MAX_TRANSFER_BYTES = 20 * 1024 * 1024
_IO_TIMEOUT = 0.1
_METRIC_FIELDS = (
    "cwnd_bytes", "ssthresh_bytes", "snd_wnd_bytes", "rcv_wnd_bytes",
    "send_buffer_bytes", "rtt_ms", "srtt_ms", "rto_ms", "retransmit_packets",
)


@dataclass(frozen=True)
class SocketTestConfig:
    duration: float = 10.0
    interval: float = 0.25
    chunk_bytes: int = 16384

    def __post_init__(self):
        for name, low, high in (("duration", 1.0, 30.0), ("interval", 0.1, 1.0)):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not low <= value <= high or not math.isfinite(value)):
                raise ValueError(f"{name} 값은 {low}~{high} 범위의 숫자여야 합니다.")
            object.__setattr__(self, name, float(value))
        if (isinstance(self.chunk_bytes, bool) or not isinstance(self.chunk_bytes, int)
                or not 1024 <= self.chunk_bytes <= 65536):
            raise ValueError("chunk_bytes 값은 1024~65536 범위의 정수여야 합니다.")


def _decode_darwin_tcp_info(data):
    """Decode byte-valued windows and millisecond RTTs, preserving real zeroes."""
    if len(data) < 56:
        raise ValueError("TCP_CONNECTION_INFO 응답이 너무 짧습니다.")
    values = struct.unpack_from("=13I", data, 4)
    return {
        "cwnd_bytes": values[5], "ssthresh_bytes": values[4],
        "snd_wnd_bytes": values[6], "rcv_wnd_bytes": values[8],
        "send_buffer_bytes": values[7], "rtt_ms": values[9],
        "srtt_ms": values[10], "rto_ms": values[2],
        "retransmit_packets": (struct.unpack_from("=Q", data, 104)[0]
                               if len(data) >= 112 else None),
    }


def read_tcp_info(connection):
    """Return missing capabilities as None, never synthetic or inferred values."""
    result = dict.fromkeys(_METRIC_FIELDS)
    result.update(platform=sys.platform, source=SOURCE, info_available=False,
                  info_error=None)
    if sys.platform != "darwin":
        result["info_error"] = "이 운영체제의 커널 TCP 상세 지표는 지원하지 않습니다."
        return result
    try:
        raw = connection.getsockopt(socket.IPPROTO_TCP, 0x106, 112)
        result.update(_decode_darwin_tcp_info(raw), info_available=True)
    except (OSError, ValueError, struct.error):
        result["info_error"] = "운영체제에서 커널 TCP 상세 지표를 읽지 못했습니다."
    return result


def run_socket_test(config, cancel_event=None, on_sample=None):
    """Run a paced local transfer and return JSON-safe actual measurements.

``sent_bytes`` counts client send() bytes; ``received_bytes`` counts server
recv() bytes for that same direction. Interval rates use their monotonic deltas.
The application reply itself is excluded from these payload byte counts.
Callbacks receive independent shallow copies (all sample values are scalars).
"""
    if not isinstance(config, SocketTestConfig):
        raise TypeError("config는 SocketTestConfig여야 합니다.")
    cancel = cancel_event if cancel_event is not None else threading.Event()
    stop = threading.Event()
    counter_lock = threading.Lock()
    receiver = {"received": 0, "error": None}
    samples = []
    result = {
        "source": SOURCE, "started_at": datetime.now().astimezone().isoformat(),
        "config": asdict(config), "samples": samples, "summary": {},
        "status": "completed", "cancelled": False, "error": None,
        "limitations": [
            "127.0.0.1에서 이 프로그램이 만든 실제 TCP 연결만 측정합니다. 인터넷 회선 측정값이 아닙니다.",
            "송수신량은 같은 방향의 송신 앱과 수신 앱이 처리한 바이트이며, 설정 간격으로 전송하므로 최대 대역폭이 아닙니다.",
            "app_echo_ms는 앱 응답 시간입니다. 커널 RTT와 다르며 커널의 밀리초 단위 값은 0일 수 있습니다.",
            "TCP SEQ·ACK 번호와 패킷 파형은 패킷 캡처 없이는 제공하지 않습니다.",
        ],
    }
    listener = client = worker = None
    started = time.monotonic()
    deadline = started + config.duration
    sent_bytes = 0
    last_elapsed = last_sent = last_received = 0
    last_echo = None

    def emit_sample():
        nonlocal last_elapsed, last_sent, last_received
        elapsed = max(0.0, time.monotonic() - started)
        with counter_lock:
            received = receiver["received"]
        delta = elapsed - last_elapsed
        sample = {
            "elapsed": elapsed, "sent_bytes": sent_bytes, "received_bytes": received,
            "tx_mbps": (sent_bytes - last_sent) * 8 / delta / 1e6 if delta > 0 else 0.0,
            "rx_mbps": (received - last_received) * 8 / delta / 1e6 if delta > 0 else 0.0,
            "app_echo_ms": last_echo,
        }
        sample.update(read_tcp_info(client))
        samples.append(sample)
        last_elapsed, last_sent, last_received = elapsed, sent_bytes, received
        if on_sample is not None:
            on_sample(dict(sample))

    def receive_payload():
        try:
            while not stop.is_set():
                try:
                    connection, _ = listener.accept()
                    break
                except socket.timeout:
                    continue
            else:
                return
            with connection:
                connection.settimeout(_IO_TIMEOUT)
                connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                pending = 0
                while not stop.is_set():
                    try:
                        data = connection.recv(config.chunk_bytes - pending)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    with counter_lock:
                        receiver["received"] += len(data)
                    pending += len(data)
                    if pending == config.chunk_bytes:
                        connection.sendall(b"\x01")
                        pending = 0
        except OSError:
            if not stop.is_set():
                with counter_lock:
                    receiver["error"] = "로컬 수신 소켓에서 통신 오류가 발생했습니다."

    try:
        if not cancel.is_set():
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.settimeout(_IO_TIMEOUT)
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            worker = threading.Thread(target=receive_payload, name="tcp-loopback-receiver",
                                      daemon=True)
            worker.start()
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.settimeout(_IO_TIMEOUT)
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client.connect(listener.getsockname())
            emit_sample()
            payload = bytes(config.chunk_bytes)
            next_sample = started + config.interval
            while (not cancel.is_set() and time.monotonic() < deadline
                   and sent_bytes + config.chunk_bytes <= MAX_TRANSFER_BYTES):
                echo_started = time.monotonic()
                offset = 0
                while offset < len(payload) and not cancel.is_set() and time.monotonic() < deadline:
                    try:
                        written = client.send(payload[offset:])
                    except socket.timeout:
                        continue
                    if written == 0:
                        raise ConnectionError("로컬 송신 소켓이 닫혔습니다.")
                    sent_bytes += written
                    offset += written
                replied = False
                while offset == len(payload) and not cancel.is_set() and time.monotonic() < deadline:
                    try:
                        reply = client.recv(1)
                    except socket.timeout:
                        continue
                    if reply != b"\x01":
                        raise ConnectionError("로컬 수신 앱의 응답을 받지 못했습니다.")
                    last_echo = (time.monotonic() - echo_started) * 1000
                    replied = True
                    break
                if not replied:
                    last_echo = None
                cancel.wait(max(0.0, min(next_sample, deadline) - time.monotonic()))
                emit_sample()
                next_sample = max(next_sample + config.interval, time.monotonic())
            with counter_lock:
                receiver_error = receiver["error"]
            if receiver_error and not cancel.is_set():
                result.update(status="error", error=receiver_error)
    except OSError as error:
        if not cancel.is_set():
            result.update(status="error", error=f"로컬 TCP 측정에 실패했습니다: {error}")
    finally:
        stop.set()
        if client is not None:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client.close()
        if listener is not None:
            listener.close()
        if worker is not None:
            worker.join(timeout=1.0)
        result["cancelled"] = cancel.is_set()
        if result["cancelled"]:
            result["status"] = "cancelled"
        elapsed = max(0.0, time.monotonic() - started)
        with counter_lock:
            received_bytes = receiver["received"]
        result["summary"] = {
            "elapsed_seconds": elapsed, "sent_bytes": sent_bytes,
            "received_bytes": received_bytes,
            "avg_tx_mbps": sent_bytes * 8 / elapsed / 1e6 if elapsed else 0.0,
            "avg_rx_mbps": received_bytes * 8 / elapsed / 1e6 if elapsed else 0.0,
            "sample_count": len(samples),
            "info_available": any(sample["info_available"] for sample in samples),
            "byte_limit_reached": sent_bytes + config.chunk_bytes > MAX_TRANSFER_BYTES,
        }
        unavailable = next((sample["info_error"] for sample in samples if sample["info_error"]), None)
        if unavailable:
            result["limitations"].append(unavailable)
    return result
