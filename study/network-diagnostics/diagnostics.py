"""Bounded, user-triggered DNS and TCP connection checks without application data.

Standard-library contracts: https://docs.python.org/3/library/socket.html and
https://docs.python.org/3/library/subprocess.html#subprocess.Popen.communicate .
Connection timing includes address fallback, not just a network round trip.
"""

from dataclasses import asdict, dataclass
from datetime import datetime
import errno
import ipaddress
import json
import math
import re
import selectors
import socket
import statistics
import subprocess
import sys
import threading
import time
import unicodedata


def normalize_host(host):
    """Accept one DNS hostname or numeric IP, never a URL or host:port pair."""
    if not isinstance(host, str) or not host or len(host) > 1024:
        raise ValueError("호스트 이름이나 IP 주소를 입력하세요.")
    if any(character.isspace() or unicodedata.category(character) in {"Cc", "Cf", "Cs"}
           for character in host):
        raise ValueError("호스트에 공백이나 제어 문자를 넣을 수 없습니다.")
    bracketed = host.startswith("[") and host.endswith("]")
    if bracketed:
        host = host[1:-1]
    if any(character in host for character in "/\\@?#[]") or host.startswith("-"):
        raise ValueError("URL 대신 호스트 이름이나 IP 주소만 입력하세요.")
    address, separator, zone = host.partition("%")
    if separator and not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", zone):
        raise ValueError("IPv6 인터페이스 이름이 올바르지 않습니다.")
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        if bracketed or separator or ":" in host or re.fullmatch(r"[0-9.]+", host):
            raise ValueError("IP 주소가 올바르지 않습니다. 포트는 별도 입력하세요.") from None
    else:
        if (bracketed or separator) and parsed.version != 6:
            raise ValueError("대괄호와 인터페이스 이름은 IPv6 주소에만 사용할 수 있습니다.")
        return str(parsed) + ("%" + zone if separator else "")
    try:
        hostname = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ValueError("호스트 이름이 올바르지 않습니다.") from None
    # Avoid platform-specific abbreviated, octal or hexadecimal IPv4 parsing.
    if re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*\.?", hostname):
        raise ValueError("숫자 주소는 표준 IPv4 또는 IPv6 형식으로 입력하세요.")
    labels = hostname.removesuffix(".").split(".")
    if (len(hostname.removesuffix(".")) > 253
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                   for label in labels)):
        raise ValueError("호스트 이름이 올바르지 않습니다.")
    return hostname


@dataclass(frozen=True)
class DiagnosticConfig:
    host: str
    port: int = 443
    attempts: int = 5
    timeout: float = 2.0
    interval: float = 0.25

    def __post_init__(self):
        object.__setattr__(self, "host", normalize_host(self.host))
        for name, low, high in (("port", 1, 65535), ("attempts", 1, 20)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{name} 값은 {low}~{high} 범위의 정수여야 합니다.")
        for name, low, high in (("timeout", 0.2, 10.0), ("interval", 0.0, 2.0)):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not low <= value <= high or not math.isfinite(value)):
                raise ValueError(f"{name} 값은 {low}~{high} 범위의 숫자여야 합니다.")
            object.__setattr__(self, name, float(value))


class _Cancelled(Exception):
    pass


_RESOLVER = """
import json, socket, sys
try:
    addresses, seen = [], set()
    for family, _, _, _, sockaddr in socket.getaddrinfo(
            sys.argv[1], int(sys.argv[2]), socket.AF_UNSPEC,
            socket.SOCK_STREAM, socket.IPPROTO_TCP):
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        scope_id = sockaddr[3] if family == socket.AF_INET6 else 0
        key = (family, sockaddr[0], scope_id)
        if key in seen:
            continue
        seen.add(key)
        item = {'family': 'IPv6' if family == socket.AF_INET6 else 'IPv4',
                'address': sockaddr[0]}
        if scope_id:
            item['scope_id'] = scope_id
        addresses.append(item)
    print(json.dumps({'addresses': addresses}))
except OSError as error:
    print(json.dumps({'error': str(error)}))
"""


def _resolve_host(config, cancel_event):
    started = time.monotonic()
    result = {"status": "skipped", "duration_ms": None, "addresses": [], "error": None}
    if cancel_event.is_set():
        return result
    try:
        numeric = ipaddress.ip_address(config.host.split("%", 1)[0])
    except ValueError:
        numeric = None
    if numeric is not None:
        item = {"family": f"IPv{numeric.version}", "address": str(numeric)}
        if "%" in config.host:
            zone = config.host.split("%", 1)[1]
            try:
                scope_id = int(zone) if zone.isdecimal() else socket.if_nametoindex(zone)
                if not 0 <= scope_id <= 4294967295:
                    raise ValueError("scope out of range")
                item["scope_id"] = scope_id
                item["address"] = config.host
            except (OSError, ValueError):
                result.update(status="error", error="IPv6 인터페이스를 찾을 수 없습니다.")
                return result
        result["addresses"] = [item]
        return result
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", "-c", _RESOLVER, config.host, str(config.port)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", shell=False)
        deadline = started + config.timeout
        while True:
            if cancel_event.is_set():
                raise _Cancelled
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result.update(status="timeout", error="이름 조회 제한 시간을 넘었습니다.")
                break
            try:
                output, _ = process.communicate(timeout=min(0.05, remaining))
                data = json.loads(output)
                if process.returncode != 0 or data.get("error") or not data.get("addresses"):
                    result.update(status="error", error=data.get("error") or "주소를 찾지 못했습니다.")
                else:
                    result.update(status="ok", addresses=data["addresses"])
                break
            except subprocess.TimeoutExpired:
                continue
    except _Cancelled:
        result.update(status="skipped", error="사용자가 이름 조회를 중단했습니다.")
    except (OSError, ValueError, TypeError):
        result.update(status="error", error="이름 조회 프로세스를 실행하거나 결과를 읽지 못했습니다.")
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate()
        result["duration_ms"] = max(0.0, (time.monotonic() - started) * 1000)
    return result


def _error_status(code):
    if code in {errno.ETIMEDOUT, 10060}:
        return "timeout"
    if code in {errno.ECONNREFUSED, 10061}:
        return "refused"
    if code in {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN,
                errno.EADDRNOTAVAIL, getattr(errno, "EHOSTDOWN", -1), 10051, 10065}:
        return "unreachable"
    return "error"


def _connect_address(address, port, deadline, cancel_event):
    family = socket.AF_INET6 if address["family"] == "IPv6" else socket.AF_INET
    host = address["address"].split("%", 1)[0]
    endpoint = ((host, port, 0, address.get("scope_id", 0))
                if family == socket.AF_INET6 else (host, port))
    pending = {errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, errno.EINTR,
               10035, 10036, 10037}
    try:
        with socket.socket(family, socket.SOCK_STREAM) as connection:
            connection.setblocking(False)
            code = connection.connect_ex(endpoint)
            if code in {0, errno.EISCONN}:
                return "ok", None
            if code not in pending:
                return _error_status(code), f"연결 오류 코드: {code}"
            with selectors.DefaultSelector() as selector:
                selector.register(connection, selectors.EVENT_WRITE)
                while True:
                    if cancel_event.is_set():
                        raise _Cancelled
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return "timeout", "연결 제한 시간을 넘었습니다."
                    if selector.select(min(0.05, remaining)):
                        code = connection.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                        return ("ok", None) if code == 0 else (
                            _error_status(code), f"연결 오류 코드: {code}")
    except OSError as error:
        status = "timeout" if isinstance(error, TimeoutError) else _error_status(error.errno)
        return status, f"연결 오류: {error.strerror or type(error).__name__}"


def _address_order(addresses):
    """Alternate address families while keeping the resolver's first preference."""
    first = addresses[0]["family"]
    preferred = [address for address in addresses if address["family"] == first]
    alternate = [address for address in addresses if address["family"] != first]
    ordered = []
    for index in range(max(len(preferred), len(alternate))):
        if index < len(preferred):
            ordered.append(preferred[index])
        if index < len(alternate):
            ordered.append(alternate[index])
    return ordered


def _probe(config, addresses, attempt, cancel_event):
    started = time.monotonic()
    deadline = started + config.timeout
    outcomes = []
    for index, address in enumerate(addresses):
        if cancel_event.is_set():
            raise _Cancelled
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        # Leave half of the remaining budget for the next address family.
        address_deadline = (min(deadline, time.monotonic() + remaining / 2)
                            if index < len(addresses) - 1 else deadline)
        address_started = time.monotonic()
        status, error = _connect_address(address, config.port, address_deadline, cancel_event)
        outcomes.append({"address": address["address"], "family": address["family"],
                         "status": status, "error": error,
                         "duration_ms": max(0.0, (time.monotonic() - address_started) * 1000)})
        if status == "ok":
            break
    last = outcomes[-1] if outcomes else {
        "status": "timeout", "address": None, "family": None,
        "error": "연결 제한 시간을 넘었습니다."}
    return {"attempt": attempt, "status": last["status"],
            "duration_ms": max(0.0, (time.monotonic() - started) * 1000),
            "address": last["address"], "family": last["family"],
            "error": last["error"], "address_attempts": outcomes}


def summarize_probes(probes):
    """Summarize completed probe records, usable for live partial results too."""
    successful = [probe["duration_ms"] for probe in probes if probe["status"] == "ok"]
    # Only adjacent successful attempts form a jitter pair; failures break a pair.
    differences = [abs(right["duration_ms"] - left["duration_ms"])
                   for left, right in zip(probes, probes[1:])
                   if left["status"] == right["status"] == "ok"]
    return {"attempted": len(probes), "succeeded": len(successful),
            "failed": len(probes) - len(successful),
            "success_pct": 100 * len(successful) / len(probes) if probes else None,
            "mean_ms": statistics.mean(successful) if successful else None,
            "min_ms": min(successful) if successful else None,
            "max_ms": max(successful) if successful else None,
            "mean_jitter_ms": statistics.mean(differences) if differences else None}


def run_diagnostics(config, cancel_event=None, on_progress=None):
    """Return JSON-safe results; callbacks run synchronously on the calling thread.

    Only completed attempts enter statistics. Cancellation closes the active
    socket or kills/reaps the resolver subprocess; an unfinished attempt is omitted.
    A successful attempt may include failed address fallbacks in its duration.
    """
    if not isinstance(config, DiagnosticConfig):
        raise TypeError("config는 DiagnosticConfig여야 합니다.")
    cancel_event = cancel_event if cancel_event is not None else threading.Event()
    result = {"source": "active_tcp_connection_probe",
              "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "config": asdict(config), "dns": None, "probes": [], "summary": {},
              "cancelled": False, "findings": [], "limitations": [
                  "TCP 연결을 맺고 즉시 닫습니다. TLS·HTTP 응답이나 인터넷 전체의 상태를 검사하지 않습니다.",
                  "연결 시간은 주소 재시도와 운영체제 처리를 포함하며, 패킷 RTT나 회선 속도가 아닙니다.",
                  "실패 비율은 TCP 연결 시도의 결과이며 패킷 손실률이 아닙니다.",
                  "이름 조회 시간은 조회 프로세스 시작과 시스템 캐시 영향을 포함합니다.",
                  "지연 통계는 성공한 시도만, 변동폭은 연속해서 성공한 두 시도의 시간 차만 사용합니다.",
                  "중단된 진행 중 시도는 통계에서 제외합니다."]}
    result["dns"] = _resolve_host(config, cancel_event)
    addresses = result["dns"]["addresses"]
    if addresses:
        addresses = _address_order(addresses)
        for attempt in range(1, config.attempts + 1):
            if cancel_event.is_set():
                break
            try:
                probe = _probe(config, addresses, attempt, cancel_event)
            except _Cancelled:
                break
            result["probes"].append(probe)
            if on_progress is not None:
                # The callback cannot mutate results held by the diagnostic engine.
                on_progress({**probe, "address_attempts": [
                    dict(item) for item in probe["address_attempts"]]})
            if attempt < config.attempts and cancel_event.wait(config.interval):
                break
    result["cancelled"] = cancel_event.is_set()
    result["summary"] = summarize_probes(result["probes"])
    summary = result["summary"]
    if result["cancelled"]:
        result["findings"].append("사용자가 진단을 중단했습니다. 완료된 시도만 표시합니다.")
    if result["dns"]["status"] in {"error", "timeout"}:
        result["findings"].append("호스트 주소를 확인하지 못해 TCP 연결 검사를 진행하지 못했습니다.")
    elif summary["attempted"]:
        result["findings"].append(
            f"{summary['attempted']}회 중 {summary['succeeded']}회 TCP 연결에 성공했습니다.")
        statuses = {probe["status"] for probe in result["probes"]}
        if "refused" in statuses:
            result["findings"].append("연결 거부가 관측됐습니다. 대상 포트의 서비스나 방화벽 설정을 확인하세요.")
        if "timeout" in statuses:
            result["findings"].append("제한 시간 안에 연결하지 못했습니다. 경로·방화벽·서버 상태를 확인하세요.")
        if "unreachable" in statuses:
            result["findings"].append("운영체제가 대상 주소로 연결할 수 없다고 보고했습니다.")
        if "error" in statuses:
            result["findings"].append("일부 연결에서 오류가 발생했습니다. 시도별 오류 내용을 확인하세요.")
    return result
