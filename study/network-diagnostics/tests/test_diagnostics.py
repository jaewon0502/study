"""Offline diagnostics checks: local loopback only, no external Internet access."""

from contextlib import contextmanager
import errno
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import diagnostics
from diagnostics import DiagnosticConfig, normalize_host, run_diagnostics


@contextmanager
def loopback_server():
    stopped = threading.Event()
    received = []
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(20)
        server.settimeout(0.05)

        def accept_connections():
            while True:
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    if stopped.is_set():
                        break
                    continue
                with connection:
                    connection.settimeout(1)
                    received.append(connection.recv(1))

        worker = threading.Thread(target=accept_connections, daemon=True)
        worker.start()
        try:
            yield server.getsockname()[1], received
        finally:
            stopped.set()
            worker.join(timeout=2)
            if worker.is_alive():
                raise AssertionError("Loopback server did not stop")


def dns_fixture(addresses=None):
    return {"status": "ok", "duration_ms": 1.0,
            "addresses": addresses or [{"family": "IPv4", "address": "192.0.2.1"}],
            "error": None}


class ConfigTests(unittest.TestCase):
    def test_normalizes_idna_case_and_bracketed_ipv6(self):
        self.assertEqual(normalize_host("EXAMPLE.COM."), "example.com.")
        self.assertEqual(normalize_host("예시.한국"), "xn--vv4b11d.xn--3e0b707e")
        self.assertEqual(normalize_host("[2001:0DB8::1]"), "2001:db8::1")
        self.assertEqual(normalize_host("127.0.0.1"), "127.0.0.1")
        self.assertEqual(normalize_host("[fe80::1%en0]"), "fe80::1%en0")

    def test_rejects_urls_ports_options_paths_and_malformed_hosts(self):
        bad = ["", None, "https://example.com", "example.com:443", "[::1]:443",
               "--help", "-x.example", "example.com/path", "user@example.com",
               "example.com?key=abc", "example.com#fragment", "example.com\\path",
               "example..com", ".example.com", "a" * 64 + ".com", "_service.com",
               "[127.0.0.1]", "999.1.1.1", "127.1", "2130706433", "127.0.0.01",
               "0x7f000001", "0x7f.0.0.1", "０１２７.０.０.１",
               "::xyz", "::1%bad/zone", "example.com%en0", "::1%", "[::1",
               "example.com\n", " example.com", "exa\x00mple.com", "example.com\x7f",
               "exa\x80mple.com", "exam\u200bple.com", "example\u202e.com"]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_host(value)

    def test_bounds_and_nonfinite_types_rejected(self):
        for name, values in {
                "port": [0, 65536, 443.0, "443", True],
                "attempts": [0, 21, 1.5, True],
                "timeout": [0.19, 10.01, 10 ** 999, float("nan"), float("inf"), "2", True],
                "interval": [-0.1, 2.1, float("nan"), True]}.items():
            for value in values:
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    DiagnosticConfig("localhost", **{name: value})

    def test_boundary_values_and_frozen_config(self):
        config = DiagnosticConfig("LOCALHOST", port=65535, attempts=20, timeout=0.2, interval=2)
        self.assertEqual(config.host, "localhost")
        with self.assertRaises(AttributeError):
            config.host = "other.example"


class ResolverTests(unittest.TestCase):
    def test_numeric_ip_skips_dns_subprocess(self):
        with patch("diagnostics.subprocess.Popen") as popen:
            resolved = diagnostics._resolve_host(DiagnosticConfig("[::1]"), threading.Event())
        popen.assert_not_called()
        self.assertEqual(resolved["status"], "skipped")
        self.assertIsNone(resolved["duration_ms"])
        self.assertEqual(resolved["addresses"], [{"family": "IPv6", "address": "::1"}])

    def test_scoped_ipv6_retains_interface(self):
        with patch("diagnostics.socket.if_nametoindex", return_value=4):
            resolved = diagnostics._resolve_host(DiagnosticConfig("fe80::1%test0"), threading.Event())
        self.assertEqual(resolved["addresses"][0]["scope_id"], 4)
        with patch("diagnostics.socket.if_nametoindex", side_effect=OSError):
            missing = diagnostics._resolve_host(DiagnosticConfig("fe80::1%missing"), threading.Event())
        self.assertEqual(missing["status"], "error")

    def test_successful_subprocess_uses_argv_not_shell(self):
        process = MagicMock()
        process.communicate.return_value = (json.dumps({"addresses": dns_fixture()["addresses"]}), None)
        process.returncode = 0
        process.poll.return_value = 0
        with patch("diagnostics.subprocess.Popen", return_value=process) as popen:
            resolved = diagnostics._resolve_host(DiagnosticConfig("example.com"), threading.Event())
        self.assertEqual(resolved["status"], "ok")
        self.assertEqual(popen.call_args.args[0][-2:], ["example.com", "443"])
        self.assertFalse(popen.call_args.kwargs["shell"])
        self.assertGreater(process.communicate.call_args.kwargs["timeout"], 0)
        process.kill.assert_not_called()

    def test_dns_timeout_kills_and_reaps_child(self):
        process = MagicMock()
        process.poll.return_value = None
        process.communicate.side_effect = [subprocess.TimeoutExpired("resolver", 0.05), ("", None)]
        with patch("diagnostics.subprocess.Popen", return_value=process), patch(
                "diagnostics.time.monotonic", side_effect=[0, 0.1, 0.3, 0.31]):
            resolved = diagnostics._resolve_host(
                DiagnosticConfig("example.com", timeout=0.2), threading.Event())
        self.assertEqual(resolved["status"], "timeout")
        process.kill.assert_called_once()
        self.assertEqual(process.communicate.call_count, 2)

    def test_cancellation_during_dns_kills_and_reaps_child(self):
        event = threading.Event()
        process = MagicMock()
        process.poll.return_value = None

        def communicate(**kwargs):
            if kwargs:
                event.set()
                raise subprocess.TimeoutExpired("resolver", 0.05)
            return "", None

        process.communicate.side_effect = communicate
        with patch("diagnostics.subprocess.Popen", return_value=process):
            result = run_diagnostics(DiagnosticConfig("example.com"), event)
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["summary"]["attempted"], 0)
        process.kill.assert_called_once()

    def test_dns_failure_does_not_attempt_connections(self):
        resolved = {"status": "error", "duration_ms": 1, "addresses": [], "error": "not found"}
        with patch("diagnostics._resolve_host", return_value=resolved), patch(
                "diagnostics._connect_address") as connect:
            result = run_diagnostics(DiagnosticConfig("example.invalid"))
        connect.assert_not_called()
        self.assertIsNone(result["summary"]["success_pct"])
        self.assertTrue(result["findings"])

    def test_invalid_child_output_and_spawn_failure_are_reported(self):
        process = MagicMock()
        process.communicate.return_value = ("not-json", None)
        process.poll.return_value = 0
        for kwargs in ({"return_value": process}, {"side_effect": OSError("cannot spawn")}):
            with self.subTest(kwargs=kwargs), patch("diagnostics.subprocess.Popen", **kwargs):
                resolved = diagnostics._resolve_host(DiagnosticConfig("example.com"), threading.Event())
            self.assertEqual(resolved["status"], "error")


class ProbeTests(unittest.TestCase):
    def test_real_loopback_handshake_sends_no_application_data(self):
        progress = []
        with loopback_server() as (port, received):
            result = run_diagnostics(DiagnosticConfig("127.0.0.1", port, 3, 1, 0), on_progress=progress.append)
        self.assertEqual(result["summary"]["succeeded"], 3)
        self.assertEqual(result["summary"]["success_pct"], 100)
        self.assertEqual(len(progress), 3)
        self.assertEqual(received, [b"", b"", b""])
        self.assertEqual(result["dns"]["status"], "skipped")
        self.assertFalse(result["cancelled"])
        json.dumps(result, allow_nan=False)

    def test_real_local_unavailable_port_is_excluded_from_latency(self):
        # Bound without listen: no other process can claim this fixture port.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as unavailable:
            unavailable.bind(("127.0.0.1", 0))
            result = run_diagnostics(DiagnosticConfig("127.0.0.1", unavailable.getsockname()[1], 1, 0.2, 0))
        # macOS can silently drop SYNs on a bound, non-listening socket.
        self.assertIn(result["probes"][0]["status"], {"refused", "timeout"})
        self.assertEqual(result["summary"]["failed"], 1)
        self.assertIsNone(result["summary"]["mean_ms"])

    def test_real_localhost_resolution_then_connection(self):
        with loopback_server() as (port, _):
            result = run_diagnostics(DiagnosticConfig("localhost", port, 1, 1, 0))
        self.assertEqual(result["dns"]["status"], "ok")
        self.assertEqual(result["summary"]["succeeded"], 1)

    def test_address_family_fallback_shares_one_deadline(self):
        addresses = [{"family": "IPv6", "address": "2001:db8::1"},
                     {"family": "IPv4", "address": "192.0.2.1"}]
        deadlines = []

        def connect(address, port, deadline, cancel_event):
            deadlines.append(deadline)
            return ("unreachable", "no route") if address["family"] == "IPv6" else ("ok", None)

        with patch("diagnostics._resolve_host", return_value=dns_fixture(addresses)), patch(
                "diagnostics._connect_address", side_effect=connect), patch(
                "diagnostics.time.monotonic", return_value=10):
            result = run_diagnostics(DiagnosticConfig("example.com", attempts=1, timeout=2))
        probe = result["probes"][0]
        self.assertEqual(deadlines, [11, 12])
        self.assertEqual(probe["status"], "ok")
        self.assertEqual(probe["family"], "IPv4")
        self.assertEqual(len(probe["address_attempts"]), 2)

    def test_address_order_alternates_families(self):
        addresses = [{"family": "IPv6", "address": "::1"},
                     {"family": "IPv6", "address": "::2"},
                     {"family": "IPv4", "address": "127.0.0.1"}]
        ordered = diagnostics._address_order(addresses)
        self.assertEqual([address["family"] for address in ordered], ["IPv6", "IPv4", "IPv6"])

    def test_timeout_and_cancellation_close_socket_and_selector(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                connection = MagicMock()
                connection.__enter__.return_value = connection
                connection.connect_ex.return_value = errno.EINPROGRESS
                selector = MagicMock()
                selector.__enter__.return_value = selector
                selector.select.return_value = []
                event = threading.Event()
                if cancelled:
                    event.set()
                with patch("diagnostics.socket.socket", return_value=connection), patch(
                        "diagnostics.selectors.DefaultSelector", return_value=selector):
                    if cancelled:
                        with self.assertRaises(diagnostics._Cancelled):
                            diagnostics._connect_address(dns_fixture()["addresses"][0], 443, time.monotonic(), event)
                    else:
                        status, _ = diagnostics._connect_address(
                            dns_fixture()["addresses"][0], 443, time.monotonic(), event)
                        self.assertEqual(status, "timeout")
                connection.__exit__.assert_called_once()
                selector.__exit__.assert_called_once()

    def test_immediate_socket_exception_becomes_classified_failure(self):
        with patch("diagnostics.socket.socket", side_effect=OSError(errno.ENETUNREACH, "no route")):
            status, _ = diagnostics._connect_address(
                dns_fixture()["addresses"][0], 443, time.monotonic() + 1, threading.Event())
        self.assertEqual(status, "unreachable")

    def test_refusal_error_code_is_not_a_timeout(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.connect_ex.return_value = errno.ECONNREFUSED
        with patch("diagnostics.socket.socket", return_value=connection):
            status, _ = diagnostics._connect_address(
                dns_fixture()["addresses"][0], 443, time.monotonic() + 1, threading.Event())
        self.assertEqual(status, "refused")
        connection.__exit__.assert_called_once()

    def test_cancellation_during_socket_wait_closes_resources(self):
        event = threading.Event()
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.connect_ex.return_value = errno.EINPROGRESS
        selector = MagicMock()
        selector.__enter__.return_value = selector

        def cancel_during_select(timeout):
            self.assertLessEqual(timeout, 0.05)
            event.set()
            return []

        selector.select.side_effect = cancel_during_select
        with patch("diagnostics.socket.socket", return_value=connection), patch(
                "diagnostics.selectors.DefaultSelector", return_value=selector):
            with self.assertRaises(diagnostics._Cancelled):
                diagnostics._connect_address(
                    dns_fixture()["addresses"][0], 443, time.monotonic() + 1, event)
        connection.__exit__.assert_called_once()
        selector.__exit__.assert_called_once()

    def test_cancel_before_start_and_between_attempts(self):
        event = threading.Event()
        event.set()
        with patch("diagnostics.subprocess.Popen") as popen, patch("diagnostics.socket.socket") as connect:
            cancelled = run_diagnostics(DiagnosticConfig("example.com"), event)
        popen.assert_not_called()
        connect.assert_not_called()
        self.assertTrue(cancelled["cancelled"])
        self.assertIsNone(cancelled["summary"]["success_pct"])
        event.clear()
        with loopback_server() as (port, _):
            result = run_diagnostics(DiagnosticConfig("127.0.0.1", port, 5, 1, 2), event,
                                     on_progress=lambda probe: event.set())
        self.assertEqual(result["summary"]["attempted"], 1)
        self.assertTrue(result["cancelled"])

    def test_callback_mutation_does_not_modify_results(self):
        def corrupt(probe):
            probe["status"] = "error"
            probe["address_attempts"][0]["status"] = "error"

        with loopback_server() as (port, _):
            result = run_diagnostics(DiagnosticConfig("127.0.0.1", port, 1), on_progress=corrupt)
        self.assertEqual(result["probes"][0]["status"], "ok")
        self.assertEqual(result["probes"][0]["address_attempts"][0]["status"], "ok")

    def test_statistics_exclude_failures_and_break_jitter_pairs(self):
        probes = [{"status": status, "duration_ms": duration} for status, duration in
                  [("ok", 10), ("ok", 14), ("timeout", 2000), ("ok", 100), ("ok", 110)]]
        summary = diagnostics.summarize_probes(probes)
        self.assertEqual(summary["attempted"], 5)
        self.assertEqual(summary["success_pct"], 80)
        self.assertEqual(summary["mean_ms"], 58.5)
        self.assertEqual(summary["min_ms"], 10)
        self.assertEqual(summary["max_ms"], 110)
        self.assertEqual(summary["mean_jitter_ms"], 7)
        empty = diagnostics.summarize_probes([])
        self.assertIsNone(empty["mean_ms"])
        self.assertIsNone(empty["mean_jitter_ms"])
        self.assertIsNone(empty["success_pct"])


if __name__ == "__main__":
    unittest.main()
