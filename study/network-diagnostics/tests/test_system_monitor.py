"""Offline monitor checks. No network, administrative rights or private fixtures."""

from collections import namedtuple
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from system_monitor import SystemMonitor, counter_rates, cpu_usage, parse_lsof_tcp, parse_netstat_tcp


FIXTURE = """Active Internet connections (including servers)
Proto Recv-Q Send-Q Local Address Foreign Address (state)
tcp4 0 0 192.0.2.1.51234 198.51.100.2.443 ESTABLISHED
tcp6 0 0 ::1.5432 ::1.43210 CLOSE_WAIT
tcp46 0 0 *.8080 *.* LISTEN
tcp6 0 0 fe80::1%en0.1234 fe80::2%en0.443 TIME_WAIT
udp4 0 0 *.53 *.*
tcp4 0 0 example.com.23 198.51.100.2.443 ESTABLISHED
tcp4 bad 0 192.0.2.1.51234 198.51.100.2.443 ESTABLISHED
tcp4 0 0 192.0.2.1.99999 *.* LISTEN
"""
Cpu = namedtuple("Cpu", "user system idle")
LSOF_FIXTURE = """p123
f7
tIPv4
PTCP
n192.0.2.1:12345->198.51.100.2:443
TST=ESTABLISHED
TQR=0
TQS=0
f8
tIPv6
PTCP
n[::1]:8080
TST=LISTEN
p456
f10
tIPv4
PTCP
n192.0.2.1:12345->198.51.100.2:443
TST=ESTABLISHED
f11
tIPv6
PTCP
n[::1]:12345->[::1]:8080
TST=ESTABLISHED
f12
tIPv4
PTCP
n*:9000
TST=LISTEN
f13
tIPv4
PTCP
nexample.com:443
TST=LISTEN
f14
tIPv4
PUDP
n*:123
f15
tIPv4
PTCP
n*:99999
TST=LISTEN
"""


class FakeBackend:
    def __init__(self):
        self.cpu = Cpu(10, 5, 85)
        self.counters = {"en0": SimpleNamespace(bytes_sent=1000, bytes_recv=2000)}

    def cpu_times(self):
        return self.cpu

    def virtual_memory(self):
        return SimpleNamespace(total=1000, available=400, used=950)

    def boot_time(self):
        return 100

    def net_if_addrs(self):
        return {"en0": [SimpleNamespace(family=socket.AF_INET, address="192.0.2.1"),
                        SimpleNamespace(family=999, address="aa:bb:cc:dd:ee:ff")]}

    def net_if_stats(self):
        return {"en0": SimpleNamespace(isup=True, speed=1000)}

    def net_io_counters(self, *, pernic, nowrap):
        assert pernic and not nowrap
        return self.counters

    def net_connections(self, *, kind):
        assert kind == "tcp"
        return [SimpleNamespace(family=socket.AF_INET, laddr=("192.0.2.1", 1234),
                                raddr=("198.51.100.2", 443), status="ESTABLISHED")]


class MonitorTests(unittest.TestCase):
    def make_monitor(self, backend=None, **kwargs):
        clock = iter([10, 12, 15, 19, 23])
        return SystemMonitor(backend=backend or FakeBackend(), clock=lambda: next(clock),
                             wall_time=lambda: 1000, platform_name=kwargs.pop("platform_name", "linux"),
                             **kwargs)

    def test_netstat_numeric_ipv4_ipv6_wildcard_and_scope(self):
        rows = parse_netstat_tcp(FIXTURE)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["local"], "192.0.2.1:51234")
        self.assertEqual(rows[1]["remote"], "[::1]:43210")
        self.assertEqual(rows[2]["local"], "*:8080")
        self.assertEqual(rows[2]["remote"], "*:*")
        self.assertEqual(rows[3]["local"], "[fe80::1%en0]:1234")
        self.assertEqual(parse_netstat_tcp("permission denied"), [])

    def test_rates_use_actual_delta_and_reset(self):
        self.assertEqual(counter_rates(None, (10, 10), 2), (None, None))
        self.assertEqual(counter_rates((0, 0), (250000, 500000), 2), (1, 2))
        self.assertEqual(counter_rates((10, 10), (1, 11), 2), (None, None))
        self.assertEqual(counter_rates((0, 0), (1, 1), 0), (None, None))
        self.assertEqual(counter_rates((0, 0), (1, 1), float("nan")), (None, None))

    def test_lsof_structured_parser_discards_processes_and_duplicate_fds(self):
        rows = parse_lsof_tcp(LSOF_FIXTURE)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0], {"protocol": "TCP4", "local": "192.0.2.1:12345",
                                   "remote": "198.51.100.2:443", "state": "ESTABLISHED"})
        self.assertEqual(rows[1]["local"], "[::1]:8080")
        self.assertEqual(rows[1]["protocol"], "TCP6")
        self.assertEqual(rows[2]["remote"], "[::1]:8080")
        self.assertEqual(rows[3]["local"], "*:9000")
        self.assertTrue(all(set(row) == {"protocol", "local", "remote", "state"} for row in rows))

    def test_lsof_fallback_after_empty_netstat_uses_bounded_fixed_command(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            output = "" if argv[0].endswith("netstat") else LSOF_FIXTURE
            return SimpleNamespace(returncode=0, stdout=output, stderr="")

        result = self.make_monitor(platform_name="darwin", runner=runner).sample()
        self.assertEqual(result["connection_source"], "lsof (현재 계정 조회)")
        self.assertEqual(result["connection_states"], {"ESTABLISHED": 2, "LISTEN": 2})
        self.assertEqual(calls[1][0], ["/usr/sbin/lsof", "-nP", "-iTCP", "-FftPnT"])
        self.assertEqual(calls[1][1]["timeout"], 2)
        self.assertNotIn("shell", calls[1][1])
        self.assertIn("조회 가능한 TCP 항목만 표시하며 TIME_WAIT 등 커널 전용 상태는 이 목록에서 확인할 수 없습니다.", result["warnings"])

    def test_lsof_fallback_after_netstat_error_and_no_matches(self):
        for mode in ("exception", "returncode"):
            def runner(argv, **kwargs):
                if argv[0].endswith("netstat"):
                    if mode == "exception":
                        raise FileNotFoundError()
                    return SimpleNamespace(returncode=1, stdout="", stderr="denied")
                return SimpleNamespace(returncode=1, stdout="", stderr="")

            with self.subTest(mode=mode):
                result = self.make_monitor(platform_name="darwin", runner=runner).sample()
                self.assertEqual(result["connection_source"], "unavailable")
                self.assertEqual(result["connections"], [])

    def test_cpu_guest_and_iowait_not_double_counted(self):
        before = {"user": 0, "system": 0, "idle": 0, "iowait": 0, "guest": 0}
        after = {"user": 30, "system": 10, "idle": 50, "iowait": 10, "guest": 10}
        self.assertAlmostEqual(cpu_usage(before, after), 40)
        self.assertIsNone(cpu_usage(after, before))
        self.assertIsNone(cpu_usage(None, after))

    def test_snapshot_first_sample_and_second_delta(self):
        backend = FakeBackend()
        monitor = self.make_monitor(backend)
        first = monitor.sample()
        self.assertIsNone(first["cpu_percent"])
        self.assertIsNone(first["interfaces"][0]["tx_mbps"])
        self.assertEqual(first["memory_used_bytes"], 600)
        self.assertEqual(first["memory_percent"], 60)
        self.assertEqual(first["uptime_seconds"], 900)
        self.assertEqual(first["interfaces"][0]["addresses"], ["192.0.2.1"])
        self.assertEqual(first["connection_states"], {"ESTABLISHED": 1})
        backend.cpu = Cpu(15, 10, 95)
        backend.counters["en0"] = SimpleNamespace(bytes_sent=251000, bytes_recv=502000)
        second = monitor.sample()
        self.assertEqual(second["cpu_percent"], 50)
        self.assertEqual(second["interfaces"][0]["tx_mbps"], 1)
        self.assertEqual(second["interfaces"][0]["rx_mbps"], 2)

    def test_disappearance_and_reappearance_resets_baseline(self):
        backend = FakeBackend()
        monitor = self.make_monitor(backend)
        monitor.sample()
        backend.counters = {}
        missing = monitor.sample()["interfaces"][0]
        self.assertFalse(missing["counters_available"])
        self.assertIsNone(missing["rx_mbps"])
        backend.counters = {"en0": SimpleNamespace(bytes_sent=999999, bytes_recv=999999)}
        returned = monitor.sample()["interfaces"][0]
        self.assertIsNone(returned["tx_mbps"])

    def test_partial_failure_retains_other_metrics(self):
        class FailedBackend(FakeBackend):
            def virtual_memory(self):
                raise PermissionError("private detail must not be included")

            def net_connections(self, **kwargs):
                raise PermissionError("private detail must not be included")

        result = self.make_monitor(FailedBackend()).sample()
        self.assertIsNone(result["memory_percent"])
        self.assertEqual(len(result["interfaces"]), 1)
        self.assertEqual(result["connection_source"], "unavailable")
        self.assertEqual(result["connections"], [])
        self.assertNotIn("private detail", " ".join(result["warnings"]))

    def test_darwin_fixed_command_and_timeout(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return SimpleNamespace(returncode=0, stdout=FIXTURE, stderr="")

        result = self.make_monitor(platform_name="darwin", runner=runner).sample()
        self.assertEqual(result["connection_source"], "netstat")
        self.assertEqual(len(result["connections"]), 4)
        self.assertEqual(calls[0][0], ["/usr/sbin/netstat", "-an", "-p", "tcp"])
        self.assertEqual(calls[0][1]["timeout"], 2)
        self.assertNotIn("shell", calls[0][1])

    def test_netstat_timeout_is_reported_without_crashing(self):
        def runner(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 2)

        result = self.make_monitor(platform_name="darwin", runner=runner).sample()
        self.assertEqual(result["connection_source"], "unavailable")
        self.assertEqual(result["connections"], [])
        self.assertEqual(result["memory_percent"], 60)

    def test_empty_netstat_output_is_not_reported_as_zero_observed_connections(self):
        def runner(argv, **kwargs):
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        result = self.make_monitor(platform_name="darwin", runner=runner).sample()
        self.assertEqual(result["connection_source"], "unavailable")
        self.assertTrue(any("확인할 수 없습니다" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
