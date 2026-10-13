"""Kernel ABI fixtures plus actual bounded loopback tests; no external traffic."""

import json
from pathlib import Path
import socket
import struct
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import socket_metrics
from socket_metrics import SocketTestConfig, read_tcp_info, run_socket_test


class ConfigTests(unittest.TestCase):
    def test_bounds_types_and_nonfinite_values(self):
        for field, values in {
                "duration": [0.99, 30.01, True, "10", float("nan"), float("inf"), 10 ** 999],
                "interval": [0.099, 1.01, True, "0.25", float("nan")],
                "chunk_bytes": [1023, 65537, 16384.0, True, "16384"]}.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    SocketTestConfig(**{field: value})

    def test_boundary_values_and_frozen_config(self):
        self.assertEqual(SocketTestConfig(duration=30, interval=0.1, chunk_bytes=65536).duration, 30.0)
        config = SocketTestConfig(duration=1, interval=1, chunk_bytes=1024)
        with self.assertRaises(AttributeError):
            config.duration = 2
        with self.assertRaises(TypeError):
            run_socket_test({"duration": 1})


class DarwinAbiTests(unittest.TestCase):
    @staticmethod
    def fixture():
        # Four one-byte states, thirteen uint32 fields, seven aligned uint64 counters.
        return struct.pack("=4B13I7Q", 4, 2, 3, 0,
                           15, 0, 200, 1448, 200000, 30000, 250000, 16384,
                           300000, 7, 9, 3, 0,
                           100, 123456, 1000, 90, 10000, 0, 2 ** 32 + 3)

    def test_documented_byte_units_offsets_and_wide_retransmission_counter(self):
        data = self.fixture()
        self.assertEqual(len(data), 112)
        decoded = socket_metrics._decode_darwin_tcp_info(data)
        self.assertEqual(decoded, {
            "cwnd_bytes": 30000, "ssthresh_bytes": 200000,
            "snd_wnd_bytes": 250000, "rcv_wnd_bytes": 300000,
            "send_buffer_bytes": 16384, "rtt_ms": 7, "srtt_ms": 9,
            "rto_ms": 200, "retransmit_packets": 2 ** 32 + 3})

    def test_short_extended_struct_does_not_fabricate_retransmissions(self):
        self.assertIsNone(socket_metrics._decode_darwin_tcp_info(self.fixture()[:56])["retransmit_packets"])
        with self.assertRaises(ValueError):
            socket_metrics._decode_darwin_tcp_info(bytes(55))

    def test_kernel_zero_values_remain_real_zeroes(self):
        decoded = socket_metrics._decode_darwin_tcp_info(bytes(112))
        self.assertTrue(all(value == 0 for value in decoded.values()))

    def test_getsockopt_uses_darwin_level_option_and_size(self):
        connection = Mock()
        connection.getsockopt.return_value = self.fixture()
        with patch("socket_metrics.sys.platform", "darwin"):
            result = read_tcp_info(connection)
        connection.getsockopt.assert_called_once_with(socket.IPPROTO_TCP, 0x106, 112)
        self.assertTrue(result["info_available"])
        self.assertIsNone(result["info_error"])
        self.assertEqual(result["source"], "local_loopback_tcp_measurement")

    def test_unavailable_kernel_information_is_null_not_zero(self):
        for platform_name, error in (("darwin", OSError("not supported")), ("linux", None),
                                     ("win32", None)):
            connection = Mock()
            connection.getsockopt.side_effect = error
            with self.subTest(platform=platform_name), patch("socket_metrics.sys.platform", platform_name):
                result = read_tcp_info(connection)
            self.assertFalse(result["info_available"])
            self.assertTrue(result["info_error"])
            self.assertTrue(all(result[field] is None for field in socket_metrics._METRIC_FIELDS))
            if platform_name != "darwin":
                connection.getsockopt.assert_not_called()


class LoopbackTests(unittest.TestCase):
    def assert_no_receiver_left(self):
        self.assertFalse(any(thread.name == "tcp-loopback-receiver" for thread in threading.enumerate()))

    def test_real_payload_counts_callback_copies_and_monotonic_interval_rates(self):
        callbacks = []

        def receive_sample(sample):
            callbacks.append(dict(sample))
            sample["sent_bytes"] = -1

        config = SocketTestConfig(duration=1, interval=0.1, chunk_bytes=1024)
        result = run_socket_test(config, on_sample=receive_sample)
        self.assertEqual(result["status"], "completed", result)
        self.assertIsNone(result["error"])
        self.assertFalse(result["cancelled"])
        self.assertEqual(result["summary"]["sent_bytes"], result["summary"]["received_bytes"])
        self.assertGreater(result["summary"]["sent_bytes"], 0)
        self.assertLessEqual(result["summary"]["sent_bytes"], socket_metrics.MAX_TRANSFER_BYTES)
        self.assertEqual(len(callbacks), len(result["samples"]))
        self.assertGreater(len(callbacks), 3)
        self.assertEqual(result["samples"][0]["sent_bytes"], 0)
        self.assertIsNone(result["samples"][0]["app_echo_ms"])
        for previous, current in zip(result["samples"], result["samples"][1:]):
            self.assertGreater(current["elapsed"], previous["elapsed"])
            self.assertGreaterEqual(current["sent_bytes"], previous["sent_bytes"])
            self.assertGreaterEqual(current["received_bytes"], previous["received_bytes"])
            delta = current["elapsed"] - previous["elapsed"]
            self.assertAlmostEqual(current["tx_mbps"],
                                   (current["sent_bytes"] - previous["sent_bytes"]) * 8 / delta / 1e6)
            self.assertAlmostEqual(current["rx_mbps"],
                                   (current["received_bytes"] - previous["received_bytes"]) * 8 / delta / 1e6)
            self.assertEqual(current["source"], socket_metrics.SOURCE)
        self.assertTrue(any(sample["app_echo_ms"] is not None for sample in result["samples"]))
        if sys.platform == "darwin":
            self.assertTrue(result["summary"]["info_available"], result["limitations"])
            self.assertGreater(result["samples"][-1]["cwnd_bytes"], 0)
        json.dumps(result, allow_nan=False)
        self.assert_no_receiver_left()

    def test_pre_cancelled_run_does_not_open_sockets(self):
        cancelled = threading.Event()
        cancelled.set()
        with patch("socket_metrics.socket.socket") as create_socket:
            result = run_socket_test(SocketTestConfig(), cancel_event=cancelled)
        create_socket.assert_not_called()
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["summary"]["sent_bytes"], 0)
        self.assertEqual(result["samples"], [])

    def test_cancel_during_transfer_stops_promptly_and_releases_receiver(self):
        cancelled = threading.Event()

        def stop_after_payload(sample):
            if sample["sent_bytes"]:
                cancelled.set()

        started = time.monotonic()
        result = run_socket_test(SocketTestConfig(duration=30, interval=0.1),
                                 cancel_event=cancelled, on_sample=stop_after_payload)
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(result["status"], "cancelled")
        self.assertGreater(result["summary"]["sent_bytes"], 0)
        self.assert_no_receiver_left()

    def test_hard_byte_cap_stops_before_the_next_chunk(self):
        with patch("socket_metrics.MAX_TRANSFER_BYTES", 2500):
            result = run_socket_test(SocketTestConfig(duration=1, interval=0.1, chunk_bytes=1024))
        self.assertEqual(result["summary"]["sent_bytes"], 2048)
        self.assertEqual(result["summary"]["received_bytes"], 2048)
        self.assertTrue(result["summary"]["byte_limit_reached"])
        self.assert_no_receiver_left()

    def test_missing_tcp_capability_keeps_actual_payload_measurements(self):
        with patch("socket_metrics.sys.platform", "unsupported"):
            result = run_socket_test(SocketTestConfig(duration=1, interval=1, chunk_bytes=1024))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["summary"]["sent_bytes"], 1024)
        self.assertEqual(result["summary"]["received_bytes"], 1024)
        self.assertFalse(result["summary"]["info_available"])
        self.assertIsNone(result["samples"][-1]["cwnd_bytes"])
        self.assertGreater(result["samples"][-1]["tx_mbps"], 0)
        self.assert_no_receiver_left()

    def test_socket_setup_failure_returns_error_envelope(self):
        with patch("socket_metrics.socket.socket", side_effect=OSError("socket unavailable")):
            result = run_socket_test(SocketTestConfig())
        self.assertEqual(result["status"], "error")
        self.assertIn("socket unavailable", result["error"])
        self.assertEqual(result["summary"]["sent_bytes"], 0)
        self.assert_no_receiver_left()

    def test_callback_failure_still_cleans_up_connections_and_thread(self):
        with self.assertRaisesRegex(RuntimeError, "callback failed"):
            run_socket_test(SocketTestConfig(), on_sample=Mock(side_effect=RuntimeError("callback failed")))
        self.assert_no_receiver_left()


if __name__ == "__main__":
    unittest.main()
