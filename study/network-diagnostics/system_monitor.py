"""Read-only local OS measurements; never captures payloads or sends requests.

psutil semantics: https://psutil.readthedocs.io/ (cpu_times, virtual_memory,
net_io_counters, net_if_addrs/stats, net_connections). CPU excludes Linux
guest double-counting and iowait; memory used is total minus available.
macOS TCP rows come from numeric `netstat -an -p tcp`, without elevation;
structured lsof output provides a current-account fallback when unavailable.
"""

from collections import Counter
from datetime import datetime
import ipaddress
import math
import socket
import subprocess
import sys
import time

try:
    import psutil
except ImportError:
    psutil = None


_TCP_STATES = {
    "CLOSED", "CLOSE", "LISTEN", "SYN_SENT", "SYN_RECEIVED", "SYN_RECV",
    "ESTABLISHED", "CLOSE_WAIT", "FIN_WAIT_1", "FIN_WAIT_2", "FIN_WAIT1",
    "FIN_WAIT2", "CLOSING", "LAST_ACK", "TIME_WAIT",
}


def _netstat_endpoint(value):
    """Convert Darwin's address.port representation, retaining IPv6 scope."""
    host, separator, port = value.rpartition(".")
    if not separator or not (port == "*" or (port.isdecimal() and 0 <= int(port) <= 65535)):
        return None
    if host != "*":
        try:
            ipaddress.ip_address(host.split("%", 1)[0])
        except ValueError:
            return None
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def parse_netstat_tcp(text):
    """Parse only numeric macOS kernel TCP rows; ignore headers/malformed rows."""
    connections = []
    for line in text.splitlines():
        fields = line.split()
        if (len(fields) < 6 or fields[0] not in {"tcp", "tcp4", "tcp6", "tcp46"}
                or not fields[1].isdecimal() or not fields[2].isdecimal()
                or fields[5] not in _TCP_STATES):
            continue
        local, remote = _netstat_endpoint(fields[3]), _netstat_endpoint(fields[4])
        if local is not None and remote is not None:
            connections.append({"protocol": fields[0].upper(), "local": local,
                                "remote": remote, "state": fields[5]})
    return connections


def _lsof_endpoint(value):
    host, separator, port = value.rpartition(":")
    if not separator or not port.isdecimal() or not 0 <= int(port) <= 65535:
        return None
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if host != "*":
        try:
            ipaddress.ip_address(host.split("%", 1)[0])
        except ValueError:
            return None
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def parse_lsof_tcp(text):
    """Read lsof -FftPnT socket fields; discard process fields and duplicate FDs."""
    rows, seen = [], set()
    record = {}

    def append_record():
        if record.get("P") != "TCP" or record.get("state") not in _TCP_STATES:
            return
        endpoints = record.get("n", "").split("->")
        if not 1 <= len(endpoints) <= 2:
            return
        local = _lsof_endpoint(endpoints[0])
        remote = _lsof_endpoint(endpoints[1]) if len(endpoints) == 2 else "*:*"
        if local is None or remote is None:
            return
        family = record.get("t")
        protocol = "TCP6" if family == "IPv6" or local.startswith("[") else "TCP4"
        key = (protocol, local, remote, record["state"])
        if key not in seen:
            seen.add(key)
            rows.append(dict(zip(("protocol", "local", "remote", "state"), key)))

    for line in text.splitlines():
        if not line:
            continue
        if line[0] in {"p", "f"}:
            append_record()
            record = {}
            if line[0] == "f":
                record["has_fd"] = True
        elif record.get("has_fd"):
            if line[0] in {"t", "P", "n"}:
                record[line[0]] = line[1:]
            elif line.startswith("TST="):
                record["state"] = line[4:]
    append_record()
    return rows


def counter_rates(previous, current, elapsed):
    """Return TX/RX Mbps; a missing baseline or either counter reset invalidates both."""
    if previous is None or not math.isfinite(elapsed) or elapsed <= 0:
        return None, None
    if any(new < old for old, new in zip(previous, current)):
        return None, None
    rates = tuple((new - old) * 8 / elapsed / 1_000_000 for old, new in zip(previous, current))
    return rates if all(math.isfinite(rate) and rate >= 0 for rate in rates) else (None, None)


def cpu_usage(previous, current):
    """Percentage from cumulative CPU time fields, or None without a valid delta."""
    if previous is None or current.keys() != previous.keys():
        return None
    deltas = {key: current[key] - previous[key] for key in current}
    if any(not math.isfinite(value) or value < 0 for value in deltas.values()):
        return None
    total = sum(deltas.values()) - deltas.get("guest", 0) - deltas.get("guest_nice", 0)
    idle = deltas.get("idle", 0) + deltas.get("iowait", 0)
    if total <= 0:
        return None
    return min(100.0, max(0.0, (total - idle) / total * 100))


def _psutil_endpoint(address):
    if not address:
        return "*:*"
    host, port = address[0], address[1]
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


class SystemMonitor:
    """Sample local state; reuse one instance to obtain meaningful interval rates.

    Constructor dependencies are injectable for offline tests. Missing data is
    None (rates/CPU/memory) or an empty collection plus warnings. Interface byte
    counters use 0 with counters_available=False if the OS cannot supply them.
    No metrics are aggregated across interfaces, avoiding VPN double-counting.
    """

    def __init__(self, *, backend=None, clock=None, wall_time=None,
                 platform_name=None, runner=None):
        self.backend = backend if backend is not None else psutil
        self.clock = clock or time.monotonic
        self.wall_time = wall_time or time.time
        self.platform_name = platform_name or sys.platform
        self.runner = runner or subprocess.run
        self._previous_cpu = None
        self._previous_counters = {}
        self._previous_time = None

    def _read(self, method, warnings, default=None, **kwargs):
        if self.backend is None:
            return default
        try:
            return getattr(self.backend, method)(**kwargs)
        except Exception as error:
            # Each independent OS query can fail without discarding other metrics.
            warnings.append(f"{method} 조회 불가 ({type(error).__name__})")
            return default

    def _connections(self, warnings):
        if self.platform_name == "darwin":
            try:
                process = self.runner(["/usr/sbin/netstat", "-an", "-p", "tcp"],
                                      capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", timeout=2, check=False)
                if process.returncode:
                    warnings.append("netstat TCP 조회 실패: 현재 계정의 연결 목록을 추가 조회합니다.")
                elif not process.stdout.strip():
                    warnings.append("netstat TCP 목록이 비어 있어 현재 계정의 연결 목록을 추가 조회합니다.")
                else:
                    rows = parse_netstat_tcp(process.stdout)
                    warnings.append("macOS TCP 연결: 관리자 권한이 필요한 psutil 대신 netstat으로 조회합니다.")
                    if process.stderr.strip():
                        warnings.append("netstat이 일부 조회 제한을 알렸습니다. 연결 목록이 일부일 수 있습니다.")
                    return rows, "netstat"
            except (OSError, subprocess.SubprocessError):
                warnings.append("netstat TCP 조회 불가 또는 2초 제한 초과: 현재 계정의 연결 목록을 추가 조회합니다.")
            return self._lsof_connections(warnings)
        connections = self._read("net_connections", warnings, kind="tcp")
        if connections is None:
            return [], "unavailable"
        if self.platform_name.startswith("linux"):
            warnings.append("Linux TCP 목록은 현재 계정의 조회 권한에 따라 일부 연결이 생략될 수 있습니다.")
        return [{"protocol": "TCP6" if item.family == socket.AF_INET6 else "TCP4",
                 "local": _psutil_endpoint(item.laddr), "remote": _psutil_endpoint(item.raddr),
                 "state": item.status} for item in connections], "psutil"

    def _lsof_connections(self, warnings):
        try:
            process = self.runner(["/usr/sbin/lsof", "-nP", "-iTCP", "-FftPnT"],
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=2, check=False)
            rows = parse_lsof_tcp(process.stdout)
            if process.returncode == 0 and rows:
                warnings.append("조회 가능한 TCP 항목만 표시하며 TIME_WAIT 등 커널 전용 상태는 이 목록에서 확인할 수 없습니다.")
                if process.stderr.strip():
                    warnings.append("lsof에서 일부 조회 제한이 감지되었습니다.")
                return rows, "lsof (현재 계정 조회)"
            warnings.append("lsof에서 유효한 TCP 목록을 받지 못해 현재 연결 수를 확인할 수 없습니다.")
        except (OSError, subprocess.SubprocessError):
            warnings.append("lsof TCP 조회 불가 또는 2초 제한 초과")
        return [], "unavailable"

    def sample(self):
        now, wall = self.clock(), self.wall_time()
        warnings = []
        if self.backend is None:
            warnings.append("psutil을 설치해야 CPU·메모리·인터페이스 정보를 조회할 수 있습니다.")
        snapshot = {
            "timestamp": datetime.fromtimestamp(wall).astimezone().isoformat(timespec="seconds"),
            "monotonic": now, "cpu_percent": None, "memory_percent": None,
            "memory_used_bytes": None, "memory_total_bytes": None, "uptime_seconds": None,
            "interfaces": [], "connections": [], "connection_states": {},
            "connection_source": "unavailable", "warnings": warnings,
        }
        cpu = self._read("cpu_times", warnings)
        current_cpu = cpu._asdict() if cpu is not None else None
        if current_cpu is not None:
            snapshot["cpu_percent"] = cpu_usage(self._previous_cpu, current_cpu)
        self._previous_cpu = current_cpu
        memory = self._read("virtual_memory", warnings)
        if memory is not None and memory.total > 0:
            total = int(memory.total)
            used = min(total, max(0, total - int(memory.available)))
            snapshot.update(memory_used_bytes=used, memory_total_bytes=total,
                            memory_percent=used / total * 100)
        boot = self._read("boot_time", warnings)
        if boot is not None:
            snapshot["uptime_seconds"] = max(0.0, wall - boot)
        addresses = self._read("net_if_addrs", warnings, default={})
        stats = self._read("net_if_stats", warnings, default={})
        counters = self._read("net_io_counters", warnings, default={}, pernic=True, nowrap=False)
        elapsed = now - self._previous_time if self._previous_time is not None else 0
        current_counters = {}
        for name in sorted(set(addresses) | set(stats) | set(counters)):
            info, count = stats.get(name), counters.get(name)
            tx, rx = (int(count.bytes_sent), int(count.bytes_recv)) if count else (0, 0)
            tx_rate, rx_rate = None, None
            if count is not None:
                current_counters[name] = (tx, rx)
                tx_rate, rx_rate = counter_rates(self._previous_counters.get(name), (tx, rx), elapsed)
            ip_addresses = sorted({item.address for item in addresses.get(name, [])
                                   if item.family in (socket.AF_INET, socket.AF_INET6)})
            speed = info.speed if info is not None else 0
            snapshot["interfaces"].append({
                "name": name, "addresses": ip_addresses, "is_up": bool(info and info.isup),
                "link_mbps": speed if speed and speed > 0 else None,
                "tx_bytes": tx, "rx_bytes": rx, "tx_mbps": tx_rate, "rx_mbps": rx_rate,
                "counters_available": count is not None,
            })
        self._previous_counters = current_counters
        self._previous_time = now
        rows, source = self._connections(warnings)
        snapshot.update(connections=rows, connection_source=source,
                        connection_states=dict(Counter(item["state"] for item in rows)))
        return snapshot
