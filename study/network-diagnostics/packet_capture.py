"""Bounded TCP header metadata collection, with no saved packet/application bytes.

Command and output grammar: https://www.tcpdump.org/manpages/tcpdump.1.html
Only tcpdump's numeric TCP header fields are retained. Raw receive-window values
are not cwnd; packet loss and retransmission are not inferred from these records.
Live capture requires an OS-granted capture permission. This module never elevates.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time


MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_LINE_BYTES = 8192
FILE_TIMEOUT = 20.0
_FLAGS = re.compile(r"[FSRPUWE.N]{0,10}\Z")
_LINE = re.compile(
    r"^\s*(\d+(?:\.\d+)?) IP(?:6)? (\S+) > (\S+): Flags \[([FSRPUWE.N]*)\], (.*)$"
)
_LENGTH = re.compile(r"(?:^|, )length (\d+)(?=\s|:|$)")


def _limits(duration, max_packets):
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(duration) or not 1 <= duration <= 120):
        raise ValueError("수집 시간은 1~120초여야 합니다.")
    if type(max_packets) is not int or not 1 <= max_packets <= 10000:
        raise ValueError("패킷 제한은 1~10000개여야 합니다.")


@dataclass(frozen=True)
class CaptureConfig:
    interface: str = "en0"
    duration: float = 30
    max_packets: int = 2000

    def __post_init__(self):
        _limits(self.duration, self.max_packets)
        if (not isinstance(self.interface, str)
                or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", self.interface)):
            raise ValueError("올바른 네트워크 인터페이스 이름을 지정하세요.")


def _ip(value):
    if not isinstance(value, str) or len(value) > 100:
        raise ValueError("invalid IP")
    address, separator, zone = value.partition("%")
    parsed = ipaddress.ip_address(address)
    if separator and (parsed.version != 6 or not re.fullmatch(r"[\w.:-]{1,32}", zone)):
        raise ValueError("invalid IP zone")
    return parsed.compressed + ("%" + zone if separator else "")


def _endpoint(value):
    host, separator, port = value.rpartition(".")
    if not separator or not port.isdecimal() or not 0 <= int(port) <= 65535:
        raise ValueError("invalid endpoint")
    return _ip(host), int(port)


def _number(value, maximum, *, optional=False):
    if value is None and optional:
        return None
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("invalid numeric field")
    return value


def validate_metadata(record):
    """Return only validated header fields; recompute flow identity, drop extras."""
    if not isinstance(record, dict):
        raise ValueError("invalid record")
    timestamp = record.get("time")
    if (isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
            or not math.isfinite(timestamp) or timestamp < 0):
        raise ValueError("invalid timestamp")
    flags = record.get("flags")
    if not isinstance(flags, str) or not _FLAGS.fullmatch(flags):
        raise ValueError("invalid TCP flags")
    clean = {"time": float(timestamp)}
    for side in ("src", "dst"):
        clean[f"{side}_ip"] = _ip(record.get(f"{side}_ip"))
        clean[f"{side}_port"] = _number(record.get(f"{side}_port"), 65535)
    clean["flags"] = flags
    for field in ("seq", "seq_end", "ack"):
        clean[field] = _number(record.get(field), 2 ** 32 - 1, optional=True)
    clean["window"] = _number(record.get("window"), 65535, optional=True)
    clean["payload_length"] = _number(record.get("payload_length"), 2 ** 32 - 1)
    scale = _number(record.get("window_scale"), 14, optional=True)
    clean["window_scale"] = scale if "S" in flags else None
    endpoints = []
    for side in ("src", "dst"):
        host = clean[f"{side}_ip"]
        host = f"[{host}]" if ":" in host else host
        endpoints.append(f"{host}:{clean[f'{side}_port']}")
    clean["flow_id"] = " <-> ".join(sorted(endpoints))
    return clean


def parse_tcpdump_line(text):
    """Parse one numeric tcpdump line; discard all text after TCP `length N`."""
    if not isinstance(text, str) or len(text) > MAX_LINE_BYTES:
        return None
    match = _LINE.match(text.rstrip("\r\n"))
    if match is None:
        return None
    timestamp, source, destination, flags, remainder = match.groups()
    length = _LENGTH.search(remainder)
    if length is None:
        return None
    # Application dissector output starts after the first length field.
    header = remainder[:length.start()]
    try:
        source_ip, source_port = _endpoint(source)
        destination_ip, destination_port = _endpoint(destination)
        record = dict(time=float(timestamp), src_ip=source_ip, src_port=source_port,
                      dst_ip=destination_ip, dst_port=destination_port, flags=flags,
                      payload_length=int(length.group(1)))
        sequence = re.search(r"(?:^|, )seq (\d+)(?::(\d+))?(?=,|$)", header)
        if sequence is None and re.search(r"(?:^|, )seq\b", header):
            return None
        record["seq"] = int(sequence.group(1)) if sequence else None
        record["seq_end"] = int(sequence.group(2)) if sequence and sequence.group(2) else None
        for output, token in (("ack", "ack"), ("window", "win")):
            field = re.search(rf"(?:^|, ){token} (\d+)(?=,|$)", header)
            if field is None and re.search(rf"(?:^|, ){token}\b", header):
                return None
            record[output] = int(field.group(1)) if field else None
        scale = re.search(r"(?:\[|,)wscale (\d+)(?=,|\])", header)
        if scale is None and re.search(r"(?:\[|,)wscale\b", header):
            return None
        record["window_scale"] = int(scale.group(1)) if scale else None
        return validate_metadata(record)
    except (ValueError, OverflowError):
        return None


class _Lines:
    """Incremental bounded line framing, including overlong partial records."""

    def __init__(self):
        self.buffer = bytearray()
        self.discarding = False
        self.rejected = 0

    def feed(self, chunk, *, final=False):
        lines = []
        for piece in chunk.splitlines(keepends=True):
            ended = piece.endswith(b"\n")
            if not self.discarding:
                if len(self.buffer) + len(piece) > MAX_LINE_BYTES:
                    self.buffer.clear()
                    self.discarding = True
                    self.rejected += 1
                else:
                    self.buffer.extend(piece)
            if ended:
                if not self.discarding:
                    lines.append(bytes(self.buffer))
                self.buffer.clear()
                self.discarding = False
        if final and self.buffer:
            lines.append(bytes(self.buffer))
            self.buffer.clear()
        return lines


def _result(source):
    return {"source": source, "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "complete", "stop_reason": None, "error": None,
            "packets": [], "warnings": [],
            "duration_seconds": 0.0, "dropped_packets": None}


def _error(result, message):
    result["status"] = "error"
    result["stop_reason"] = "error"
    result["error"] = message[:500]
    return result


def _tcpdump():
    found = shutil.which("tcpdump")
    if found:
        return found
    candidate = Path("/usr/sbin/tcpdump")
    return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None


def _input_file(path):
    resolved = Path(path).expanduser().resolve(strict=True)
    details = resolved.stat()
    if not stat.S_ISREG(details.st_mode):
        raise ValueError("일반 파일만 읽을 수 있습니다.")
    if details.st_size > MAX_FILE_BYTES:
        raise ValueError("파일은 100 MiB 이하여야 합니다.")
    return resolved


def _append(result, packet, on_packet):
    result["packets"].append(packet)
    if on_packet is not None:
        on_packet(dict(packet))


def _command_error(stderr, returncode):
    lowered = stderr.lower()
    if any(word in lowered for word in ("permission denied", "operation not permitted",
                                        "you don't have permission", "access is denied")):
        return "패킷 캡처 권한이 없습니다. OS에서 캡처 권한을 설정하거나 기존 PCAP/PCAPNG 파일을 여세요."
    if any(word in lowered for word in ("no such device", "device not configured")):
        return "선택한 네트워크 인터페이스를 사용할 수 없습니다."
    if any(word in lowered for word in ("unknown file format", "truncated dump file",
                                        "not a pcap", "bad dump file format")):
        return "지원되지 않거나 손상된 캡처 파일입니다. PCAP/PCAPNG 파일을 확인하세요."
    return f"tcpdump가 정상적으로 완료되지 않았습니다 (종료 코드 {returncode})."


def _collect(command, result, timeout, max_packets, cancel_event, on_packet, *, live):
    started = time.monotonic()
    process = None
    selector = selectors.DefaultSelector()
    framed = _Lines()
    stderr = bytearray()
    rejected = 0
    stopping = None
    killed = False

    def consume(chunk, final=False):
        nonlocal rejected
        for raw in framed.feed(chunk, final=final):
            if len(result["packets"]) >= max_packets:
                break
            packet = parse_tcpdump_line(raw.decode("utf-8", errors="replace"))
            if packet is None:
                rejected += 1
            else:
                _append(result, packet, on_packet)

    try:
        if cancel_event is not None and cancel_event.is_set():
            result["status"] = "cancelled"
            result["stop_reason"] = "cancelled"
            return result
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   stdin=subprocess.DEVNULL, bufsize=0,
                                   env={**os.environ, "LC_ALL": "C", "LANG": "C"})
        for stream in (process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map() or process.poll() is None:
            now = time.monotonic()
            cancelled = cancel_event is not None and cancel_event.is_set()
            limit = len(result["packets"]) >= max_packets
            timed_out = now - started >= timeout
            if stopping is None and (cancelled or limit or timed_out):
                stopping = now
                if cancelled:
                    result["status"] = "cancelled"
                    result["stop_reason"] = "cancelled"
                elif limit:
                    result["stop_reason"] = "packet_limit"
                elif timed_out and not live:
                    _error(result, "캡처 파일 처리 시간이 20초를 초과했습니다. 일부 메타데이터만 표시합니다.")
                else:
                    result["stop_reason"] = "duration_limit"
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
            if stopping is not None and now - stopping >= 1.0 and process.poll() is None:
                process.kill()
                killed = True
            if stopping is not None and now - stopping >= 1.5:
                break
            for key, _ in selector.select(timeout=0.05):
                try:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                elif key.fileobj is process.stdout:
                    consume(chunk)
                else:
                    stderr.extend(chunk)
                    del stderr[:-16384]
        consume(b"", final=True)
        returncode = process.wait(timeout=0.5)
        error_text = stderr.decode("utf-8", errors="replace")
        dropped = re.search(r"(?m)^\s*(\d+) packets? dropped by kernel\s*$", error_text)
        if dropped:
            result["dropped_packets"] = int(dropped.group(1))
        if returncode != 0 and stopping is None:
            _error(result, _command_error(error_text, returncode))
        if result["stop_reason"] is None:
            captured = re.search(r"(?m)^\s*(\d+) packets? captured\s*$", error_text)
            # tcpdump -c can exit before the loop observes the count limit; its
            # footer also includes TCP packets whose metadata could not be parsed.
            reached_limit = (len(result["packets"]) >= max_packets
                             or captured is not None and int(captured.group(1)) >= max_packets)
            result["stop_reason"] = "packet_limit" if reached_limit else "end_of_file"
        if killed:
            result["warnings"].append("tcpdump 응답이 늦어 프로세스를 종료했습니다.")
    except (OSError, ValueError, subprocess.SubprocessError):
        _error(result, "tcpdump를 실행하거나 결과를 읽을 수 없습니다. 설치 및 파일 접근 권한을 확인하세요.")
    except Exception:
        _error(result, "패킷 메타데이터를 전달하는 중 오류가 발생해 수집을 중단했습니다.")
    finally:
        selector.close()
        if process is not None:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                result["warnings"].append("tcpdump 종료 확인 시간이 초과되었습니다.")
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        result["duration_seconds"] = time.monotonic() - started
        rejected += framed.rejected
        if rejected:
            result["warnings"].append(f"헤더가 없거나 잘렸거나 형식이 맞지 않는 출력 {rejected}줄을 제외했습니다.")
        if len(result["packets"]) >= max_packets:
            result["warnings"].append(f"최대 {max_packets}개의 TCP 메타데이터만 표시합니다.")
    return result


def run_capture(config, cancel_event=None, on_packet=None):
    """Collect live headers using existing permission, never sudo/promiscuous mode."""
    if not isinstance(config, CaptureConfig):
        raise TypeError("config must be CaptureConfig")
    result = _result("live_tcp_packet_metadata")
    try:
        interfaces = {name for _, name in socket.if_nameindex()}
    except OSError:
        return _error(result, "네트워크 인터페이스 목록을 읽을 수 없습니다.")
    if config.interface not in interfaces:
        return _error(result, "존재하는 네트워크 인터페이스를 선택하세요.")
    binary = _tcpdump()
    if binary is None:
        return _error(result, "tcpdump를 찾을 수 없습니다. 설치된 tcpdump가 필요합니다.")
    command = [binary, "-l", "-nn", "-tt", "-S", "-K", "-p", "--immediate-mode",
               "-s", "160", "-i", config.interface, "-c", str(config.max_packets), "tcp"]
    return _collect(command, result, config.duration, config.max_packets, cancel_event,
                    on_packet, live=True)


def read_capture(path, max_packets=2000, cancel_event=None, on_packet=None):
    """Import numeric TCP headers from a bounded regular PCAP/PCAPNG file."""
    _limits(1, max_packets)
    result = _result("capture_file_tcp_metadata")
    try:
        resolved = _input_file(path)
    except (OSError, TypeError, ValueError):
        return _error(result, "캡처 파일을 읽을 수 없습니다. 100 MiB 이하의 일반 파일인지 확인하세요.")
    binary = _tcpdump()
    if binary is None:
        return _error(result, "tcpdump를 찾을 수 없습니다. 설치된 tcpdump가 필요합니다.")
    command = [binary, "-l", "-nn", "-tt", "-S", "-K", "-r", str(resolved),
               "-c", str(max_packets), "tcp"]
    return _collect(command, result, FILE_TIMEOUT, max_packets, cancel_event, on_packet,
                    live=False)


def follow_metadata(path, duration=60, max_packets=2000, cancel_event=None, on_packet=None):
    """Tail validated JSONL header metadata from an explicitly selected local file."""
    _limits(duration, max_packets)
    result = _result("live_tcp_metadata_file")
    started = time.monotonic()
    framed = _Lines()
    rejected = 0
    read_bytes = 0
    try:
        resolved = _input_file(path)
        with resolved.open("rb") as handle:
            initial = os.fstat(handle.fileno())
            while time.monotonic() - started < duration:
                if cancel_event is not None and cancel_event.is_set():
                    result["status"] = "cancelled"
                    result["stop_reason"] = "cancelled"
                    break
                details = os.fstat(handle.fileno())
                current_path = resolved.stat()
                if (details.st_size < handle.tell() or details.st_size > MAX_FILE_BYTES
                        or (current_path.st_dev, current_path.st_ino) != (initial.st_dev, initial.st_ino)):
                    return _error(result, "메타데이터 파일이 교체·축소되었거나 100 MiB 제한을 초과해 읽기를 중단했습니다.")
                chunk = handle.read(min(65536, MAX_FILE_BYTES - read_bytes + 1))
                read_bytes += len(chunk)
                if read_bytes > MAX_FILE_BYTES:
                    return _error(result, "메타데이터 파일이 100 MiB 제한을 초과했습니다.")
                for raw in framed.feed(chunk):
                    try:
                        packet = validate_metadata(json.loads(raw.decode("utf-8")))
                    except (ValueError, TypeError, UnicodeError, OverflowError):
                        rejected += 1
                        continue
                    _append(result, packet, on_packet)
                    if len(result["packets"]) >= max_packets:
                        result["stop_reason"] = "packet_limit"
                        result["warnings"].append(f"최대 {max_packets}개의 TCP 메타데이터만 표시합니다.")
                        return result
                if not chunk:
                    if cancel_event is not None:
                        cancel_event.wait(0.05)
                    else:
                        time.sleep(0.05)
            if result["stop_reason"] is None:
                result["stop_reason"] = "duration_limit"
    except (OSError, TypeError, ValueError):
        _error(result, "메타데이터 파일을 읽을 수 없습니다. 100 MiB 이하의 일반 JSONL 파일인지 확인하세요.")
    except Exception:
        _error(result, "패킷 메타데이터를 전달하는 중 오류가 발생해 읽기를 중단했습니다.")
    finally:
        result["duration_seconds"] = time.monotonic() - started
        rejected += framed.rejected
        if rejected:
            result["warnings"].append(f"형식이 맞지 않는 메타데이터 {rejected}줄을 제외했습니다.")
        if framed.buffer:
            result["warnings"].append("마지막 JSONL 줄이 아직 완성되지 않아 제외했습니다.")
    return result


def sanitize_stream(input_stream, output_stream):
    """Convert binary tcpdump input to UTF-8 JSONL; emit no raw/dissector text."""
    framed = _Lines()
    while True:
        chunk = input_stream.read1(65536) if hasattr(input_stream, "read1") else input_stream.read(65536)
        for raw in framed.feed(chunk, final=not chunk):
            packet = parse_tcpdump_line(raw.decode("utf-8", errors="replace"))
            if packet is not None:
                output_stream.write(json.dumps(packet, ensure_ascii=True, allow_nan=False) + "\n")
                output_stream.flush()
        if not chunk:
            return


def main():
    parser = argparse.ArgumentParser(description="tcpdump TCP 헤더를 JSONL 메타데이터로 변환합니다.")
    parser.add_argument("--sanitize", action="store_true", required=True)
    parser.parse_args()
    try:
        sanitize_stream(sys.stdin.buffer, sys.stdout)
    except (BrokenPipeError, KeyboardInterrupt):
        return


if __name__ == "__main__":
    main()
