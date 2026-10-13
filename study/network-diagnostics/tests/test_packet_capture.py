"""Synthetic header fixtures only; no real device captures are committed."""

import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import packet_capture as capture


SYN = ("1700000000.123456 IP 192.0.2.10.50000 > 198.51.100.20.443: "
       "Flags [SEW], seq 4294967294, win 65535, options [mss 1460,nop,wscale 7], length 0")
DATA = ("1700000000.200000 IP 192.0.2.10.50000 > 198.51.100.20.443: "
        "Flags [P.], seq 10:22, ack 200, win 512, length 12: HTTP: secret=do-not-retain")
ACK = ("1700000000.250000 IP 198.51.100.20.443 > 192.0.2.10.50000: "
       "Flags [.], ack 22, win 65535, length 0")


def write_fixture(path, pcapng=False):
    """One synthetic Ethernet/IPv4/TCP frame with TEST-NET addresses."""
    ethernet = b"\x00" * 12 + b"\x08\x00"
    ipv4 = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 40, 1, 0, 64, 6, 0,
                       bytes([192, 0, 2, 10]), bytes([198, 51, 100, 20]))
    tcp = struct.pack("!HHIIBBHHH", 50000, 443, 100, 0, 0x50, 0x02, 4096, 0, 0)
    packet = ethernet + ipv4 + tcp
    if not pcapng:
        path.write_bytes(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
                         + struct.pack("<IIII", 1700000000, 123456, len(packet), len(packet)) + packet)
    else:
        def block(kind, body):
            return struct.pack("<II", kind, len(body) + 12) + body + struct.pack("<I", len(body) + 12)
        padded = packet + b"\0" * (-len(packet) % 4)
        micros = 1700000000123456
        path.write_bytes(block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
                         + block(1, struct.pack("<HHI", 1, 0, 65535))
                         + block(6, struct.pack("<IIIII", 0, micros >> 32, micros & 0xffffffff,
                                                len(packet), len(packet)) + padded))


class ParserTests(unittest.TestCase):
    def test_syn_ecn_and_window_scale(self):
        packet = capture.parse_tcpdump_line(SYN)
        self.assertEqual(packet["flags"], "SEW")
        self.assertEqual(packet["seq"], 4294967294)
        self.assertEqual(packet["window_scale"], 7)
        self.assertEqual(packet["window"], 65535)
        self.assertIsNone(packet["ack"])
        self.assertIsNone(packet["seq_end"])

    def test_pure_ack_does_not_invent_sequence(self):
        packet = capture.parse_tcpdump_line(ACK)
        self.assertIsNone(packet["seq"])
        self.assertEqual(packet["ack"], 22)
        self.assertEqual(packet["payload_length"], 0)
        self.assertEqual(packet["flow_id"], capture.parse_tcpdump_line(DATA)["flow_id"])

    def test_application_suffix_never_retained_or_parsed(self):
        packet = capture.parse_tcpdump_line(DATA + ", ack 999, win 33, options [wscale 14]")
        self.assertEqual(packet["seq_end"], 22)
        self.assertEqual(packet["ack"], 200)
        self.assertIsNone(packet["window_scale"])
        self.assertNotIn("secret", json.dumps(packet))
        self.assertEqual(set(packet), {"time", "src_ip", "src_port", "dst_ip", "dst_port",
                                      "flags", "seq", "seq_end", "ack", "window",
                                      "payload_length", "flow_id", "window_scale"})

    def test_ipv6_scopes_and_fin(self):
        line = ("1700000000.5 IP6 fe80::1%en0.50000 > fe80::2%en0.443: "
                "Flags [F.], seq 22, ack 200, win 512, length 0")
        packet = capture.parse_tcpdump_line(line)
        self.assertEqual(packet["src_ip"], "fe80::1%en0")
        self.assertIn("[fe80::1%en0]:50000", packet["flow_id"])
        self.assertEqual(packet["flags"], "F.")

    def test_malformed_truncated_and_giant_lines(self):
        for line in ("[|tcp]", "", DATA.replace("192.0.2.10", "bad-host"),
                     DATA.replace(".50000", ".70000"), SYN.replace("65535", "65536"),
                     SYN.replace("wscale 7", "wscale 15"), SYN.replace("SEW", "BAD"),
                     DATA.replace("seq 10:22", "seq invalid"), ACK.replace("ack 22", "ack -1"),
                     SYN.replace("wscale 7", "wscale invalid"),
                     "x" * (capture.MAX_LINE_BYTES + 1)):
            with self.subTest(line=line[:100]):
                self.assertIsNone(capture.parse_tcpdump_line(line))

    def test_missing_window_preserved_as_missing(self):
        self.assertIsNone(capture.parse_tcpdump_line(ACK.replace(", win 65535", ""))["window"])

    def test_json_metadata_whitelist_and_validation(self):
        packet = capture.parse_tcpdump_line(DATA)
        packet.update(application="private", flow_id="untrusted")
        clean = capture.validate_metadata(packet)
        self.assertNotIn("application", clean)
        self.assertNotEqual(clean["flow_id"], "untrusted")
        for key, value in (("time", float("nan")), ("src_port", True), ("seq", -1),
                           ("payload_length", "12"), ("flags", "DROP")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                capture.validate_metadata({**packet, key: value})

    def test_chunked_lines_discard_whole_giant_record(self):
        frames = capture._Lines()
        self.assertEqual(frames.feed(b"a" * 8000), [])
        self.assertEqual(frames.feed(b"b" * 500 + b"\n" + b"valid\n"), [b"valid\n"])
        self.assertEqual(frames.rejected, 1)
        self.assertEqual(frames.buffer, b"")

    def test_sanitize_stream_emits_only_jsonl(self):
        source = io.BytesIO((DATA + "\nmalformed\n" + SYN + "\n").encode())
        output = io.StringIO()
        capture.sanitize_stream(source, output)
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertNotIn("secret", output.getvalue())
        self.assertEqual(rows[0]["payload_length"], 12)


class CaptureTests(unittest.TestCase):
    def test_config_limits(self):
        for kwargs in ({"duration": 0}, {"duration": 121}, {"duration": float("nan")},
                       {"duration": True}, {"max_packets": 0}, {"max_packets": 10001},
                       {"max_packets": 2.5}, {"interface": "en0; echo bad"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                capture.CaptureConfig(**kwargs)

    @mock.patch.object(capture, "_collect")
    @mock.patch.object(capture, "_tcpdump", return_value="/usr/sbin/tcpdump")
    @mock.patch.object(capture.socket, "if_nameindex", return_value=[(1, "en0")])
    def test_fixed_live_command(self, interfaces, binary, collect):
        capture.run_capture(capture.CaptureConfig(max_packets=7))
        command = collect.call_args.args[0]
        self.assertEqual(command, ["/usr/sbin/tcpdump", "-l", "-nn", "-tt", "-S", "-K",
                                  "-p", "--immediate-mode", "-s", "160", "-i", "en0",
                                  "-c", "7", "tcp"])
        self.assertNotIn("-w", command)
        self.assertNotIn("sudo", command)

    @mock.patch.object(capture.socket, "if_nameindex", return_value=[(1, "en0")])
    def test_unknown_interface_and_missing_binary(self, interfaces):
        unknown = capture.run_capture(capture.CaptureConfig("other"))
        self.assertEqual(unknown["status"], "error")
        self.assertEqual(unknown["stop_reason"], "error")
        with mock.patch.object(capture, "_tcpdump", return_value=None):
            missing = capture.run_capture(capture.CaptureConfig())
            self.assertEqual(missing["status"], "error")
            self.assertEqual(missing["stop_reason"], "error")

    def test_regular_file_and_size_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(capture.read_capture(directory)["status"], "error")
            path = Path(directory) / "large.pcap"
            with path.open("wb") as handle:
                handle.truncate(capture.MAX_FILE_BYTES + 1)
            self.assertEqual(capture.read_capture(path)["status"], "error")

    def test_collect_permission_error_does_not_expose_stderr(self):
        code = "import sys; sys.stderr.write('Permission denied private_details'); sys.exit(1)"
        result = capture._collect([sys.executable, "-c", code], capture._result("test"),
                                  1, 10, None, None, live=True)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["stop_reason"], "error")
        self.assertIn("권한", result["error"])
        self.assertNotIn("private_details", json.dumps(result))

    def test_collect_both_pipes_bounded_and_dropped_count(self):
        code = ("import sys; sys.stderr.write('x'*100000+'\\n'); "
                f"print({DATA!r}, flush=True); "
                "sys.stdout.write('z'*20000+'\\n'); "
                "sys.stderr.write('3 packets dropped by kernel\\n')")
        rows = []
        result = capture._collect([sys.executable, "-c", code], capture._result("test"),
                                  3, 10, None, rows.append, live=True)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["stop_reason"], "end_of_file")
        self.assertEqual(len(rows), 1)
        self.assertEqual(result["dropped_packets"], 3)
        self.assertTrue(any("1줄" in item for item in result["warnings"]))
        self.assertNotIn("secret", json.dumps(result))

    def test_collect_packet_limit_stops_producer(self):
        code = f"import time; print({DATA!r}, flush=True); time.sleep(10)"
        result = capture._collect([sys.executable, "-c", code], capture._result("test"),
                                  5, 1, None, None, live=True)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["stop_reason"], "packet_limit")
        self.assertEqual(len(result["packets"]), 1)
        self.assertLess(result["duration_seconds"], 2)

    def test_natural_count_exit_includes_unparseable_packets(self):
        # tcpdump counts packets even when their headers are truncated. The
        # child exits itself here, before the collector reaches its own limit.
        code = (f"import sys; print({DATA!r}); print('[|tcp]'); "
                "sys.stderr.write('2 packets captured\\n')")
        result = capture._collect([sys.executable, "-c", code], capture._result("test"),
                                  5, 2, None, None, live=True)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["stop_reason"], "packet_limit")
        self.assertEqual(len(result["packets"]), 1)

    def test_live_duration_is_normal_completion(self):
        result = capture._collect([sys.executable, "-c", "import time; time.sleep(10)"],
                                  capture._result("test"), 0.1, 10, None, None, live=True)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["stop_reason"], "duration_limit")
        self.assertIsNone(result["error"])
        self.assertLess(result["duration_seconds"], 2)

    def test_cancel_reaps_subprocess(self):
        event = threading.Event()
        process_list = []
        actual_popen = subprocess.Popen
        def popen(*args, **kwargs):
            process = actual_popen(*args, **kwargs)
            process_list.append(process)
            return process
        timer = threading.Timer(0.1, event.set)
        timer.start()
        try:
            with mock.patch.object(capture.subprocess, "Popen", side_effect=popen):
                result = capture._collect([sys.executable, "-c", "import time; time.sleep(10)"],
                                          capture._result("test"), 5, 10, event, None, live=True)
        finally:
            timer.cancel()
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["stop_reason"], "cancelled")
        self.assertIsNotNone(process_list[0].poll())
        self.assertTrue(process_list[0].stdout.closed)
        self.assertTrue(process_list[0].stderr.closed)
        self.assertLess(result["duration_seconds"], 2)

    def test_cancel_before_start_does_not_spawn(self):
        event = threading.Event()
        event.set()
        with mock.patch.object(capture.subprocess, "Popen") as popen:
            result = capture._collect([], capture._result("test"), 1, 10, event, None, live=True)
        popen.assert_not_called()
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["stop_reason"], "cancelled")

    def test_callback_error_reaps_process(self):
        code = f"import time; print({SYN!r}, flush=True); time.sleep(10)"
        def failing_callback(packet):
            raise RuntimeError("private callback detail")
        result = capture._collect([sys.executable, "-c", code], capture._result("test"),
                                  2, 10, None, failing_callback, live=True)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["stop_reason"], "error")
        self.assertNotIn("private callback detail", json.dumps(result))
        self.assertLess(result["duration_seconds"], 2)

    def test_file_processing_deadline_returns_error(self):
        result = capture._collect([sys.executable, "-c", "import time; time.sleep(10)"],
                                  capture._result("test"), 0.1, 10, None, None, live=False)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["stop_reason"], "error")
        self.assertIn("처리 시간", result["error"])
        self.assertLess(result["duration_seconds"], 2)

    @unittest.skipUnless(capture._tcpdump(), "tcpdump unavailable")
    def test_real_tcpdump_imports_synthetic_pcap_and_pcapng(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("pcap", "pcapng"):
                with self.subTest(extension=extension):
                    path = Path(directory) / ("synthetic." + extension)
                    write_fixture(path, pcapng=extension == "pcapng")
                    result = capture.read_capture(path)
                    self.assertEqual(result["status"], "complete", result)
                    self.assertEqual(result["stop_reason"], "end_of_file")
                    self.assertEqual(len(result["packets"]), 1, result)
                    self.assertEqual(result["packets"][0]["src_ip"], "192.0.2.10")
                    self.assertEqual(result["packets"][0]["seq"], 100)
                    self.assertEqual(result["source"], "capture_file_tcp_metadata")
                    limited = capture.read_capture(path, max_packets=1)
                    self.assertEqual(limited["stop_reason"], "packet_limit")


class FollowTests(unittest.TestCase):
    def test_follow_validates_and_drops_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "headers.jsonl"
            record = {**capture.parse_tcpdump_line(SYN), "raw_payload": "private"}
            path.write_text("bad\n" + json.dumps(record) + "\n", encoding="utf-8")
            result = capture.follow_metadata(path, max_packets=1)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["stop_reason"], "packet_limit")
        self.assertEqual(result["source"], "live_tcp_metadata_file")
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual(len(result["packets"]), 1)
        self.assertTrue(any("1줄" in item for item in result["warnings"]))

    def test_follow_waits_for_partial_line_and_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "headers.jsonl"
            encoded = json.dumps(capture.parse_tcpdump_line(ACK))
            path.write_text(encoded[:30])
            event = threading.Event()
            def append_rest():
                with path.open("a") as handle:
                    handle.write(encoded[30:] + "\n")
            timer = threading.Timer(0.1, append_rest)
            timer.start()
            try:
                result = capture.follow_metadata(path, cancel_event=event,
                                                 on_packet=lambda packet: event.set())
            finally:
                timer.cancel()
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["stop_reason"], "cancelled")
        self.assertEqual(len(result["packets"]), 1)
        self.assertLess(result["duration_seconds"], 2)

    def test_partial_record_at_end_and_giant_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "headers.jsonl"
            path.write_bytes(b"x" * 10000 + b"\n{\"time\":")
            result = capture.follow_metadata(path, duration=1)
        self.assertEqual(result["packets"], [])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["stop_reason"], "duration_limit")
        self.assertEqual(len(result["warnings"]), 2)

    def test_full_log_replay_reaches_packet_limit_on_each_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "headers.jsonl"
            record = json.dumps(capture.parse_tcpdump_line(DATA)) + "\n"
            path.write_text(record * 2000, encoding="utf-8")
            for _ in range(2):
                result = capture.follow_metadata(path, duration=10)
                self.assertEqual(result["stop_reason"], "packet_limit")
                self.assertEqual(len(result["packets"]), 2000)
                self.assertLess(result["duration_seconds"], 2)

    def test_follow_cancel_and_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "headers.jsonl"
            missing = capture.follow_metadata(path)
            self.assertEqual(missing["status"], "error")
            self.assertEqual(missing["stop_reason"], "error")
            path.touch()
            event = threading.Event()
            event.set()
            result = capture.follow_metadata(path, cancel_event=event)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["stop_reason"], "cancelled")
        self.assertEqual(result["packets"], [])


if __name__ == "__main__":
    unittest.main()
