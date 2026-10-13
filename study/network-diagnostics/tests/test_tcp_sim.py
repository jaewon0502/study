"""Run from the project directory: python3 -m unittest discover -s tests -v."""

import dataclasses
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tcp_sim import Config, _RttEstimator, simulate


class SimulationTests(unittest.TestCase):
    def test_no_loss_complete_and_sequence_arithmetic(self):
        result = simulate(Config(loss_pct=0, total_bytes=16500))
        self.assertTrue(result.summary["completed"])
        self.assertTrue(result.summary["connection_closed"])
        self.assertEqual(result.summary["delivered_bytes"], 16500)
        self.assertEqual(result.summary["acknowledged_bytes"], 16500)
        self.assertEqual(result.summary["transmissions"], 17)
        self.assertEqual(result.summary["retransmissions"], 0)
        self.assertEqual(result.summary["timeout_count"], 0)
        handshake = result.packets[:3]
        self.assertEqual([(p["flags"], p["seq"], p["ack"]) for p in handshake],
                         [("SYN", 1000, 0), ("SYN,ACK", 5000, 1001), ("ACK", 1001, 5001)])
        data = [p for p in result.packets if p["length"]]
        self.assertEqual(data[-1]["seq"] + data[-1]["length"], 17501)
        close = result.packets[-4:]
        self.assertEqual([(p["seq"], p["ack"]) for p in close],
                         [(17501, 5001), (5001, 17502), (5001, 17502), (17502, 5002)])
        client_states = [e["state"] for e in result.events if e.get("side") == "client"]
        self.assertEqual(client_states[-4:], ["FIN-WAIT-1", "FIN-WAIT-2", "TIME-WAIT", "CLOSED"])
        self.assertTrue(all(p["received"] and p["arrival"] >= p["wire_time"] >= p["time"]
                            for p in result.packets))

    def test_repeatable_seed(self):
        config = Config(total_bytes=120000, loss_pct=10, seed=21)
        self.assertEqual(dataclasses.asdict(simulate(config)), dataclasses.asdict(simulate(config)))

    def test_sender_window_limits_new_sends(self):
        result = simulate(Config(loss_pct=12, initial_cwnd=12, receiver_window=5500,
                                 total_bytes=24000, seed=3))
        self.assertTrue(result.summary["completed"])
        for packet in result.packets:
            if packet["length"] and not packet["retransmission"]:
                self.assertLessEqual(packet["inflight"], packet["window"])
                self.assertLessEqual(packet["inflight"], packet["cwnd"] * result.config.mss + 1e-8)
        self.assertTrue(all(s["inflight"] <= 5500 for s in result.samples))

    def test_window_smaller_than_mss(self):
        result = simulate(Config(total_bytes=1500, receiver_window=250, loss_pct=0))
        self.assertTrue(result.summary["completed"])
        self.assertEqual(result.summary["transmissions"], 6)
        self.assertTrue(all(s["inflight"] <= 250 for s in result.samples))

    def test_slow_start_and_congestion_avoidance_growth(self):
        short = simulate(Config(total_bytes=500, loss_pct=0))
        self.assertAlmostEqual(short.samples[-1]["cwnd"], 1.5)
        result = simulate(Config(total_bytes=4000, initial_cwnd=4,
                                 initial_ssthresh=2, loss_pct=0))
        previous_ack = 0
        cwnd = 4
        for sample in result.samples:
            if sample["acknowledged"] > previous_ack:
                cwnd += 1 / cwnd
                self.assertAlmostEqual(sample["cwnd"], cwnd)
                self.assertEqual(sample["phase"], "congestion_avoidance")
                previous_ack = sample["acknowledged"]

    def test_timeout_reuses_sequence_and_karn_excludes_sample(self):
        with patch("tcp_sim.random.Random.random", side_effect=[0.0, 0.9]):
            result = simulate(Config(total_bytes=1000, loss_pct=50))
        data = [p for p in result.packets if p["length"]]
        self.assertEqual([p["seq"] for p in data], [1001, 1001])
        self.assertEqual(result.summary["timeout_count"], 1)
        self.assertEqual(result.summary["delivered_bytes"], 1000)
        self.assertEqual(result.rtt_samples, [])
        timeout = next(e for e in result.events if e["kind"] == "timeout")
        self.assertEqual(timeout["cwnd"], 1)
        self.assertEqual(timeout["rto_ms"], 2000)
        self.assertEqual(timeout["ssthresh"], 2)

    def test_three_duplicate_acks_trigger_reno_recovery(self):
        with patch("tcp_sim.random.Random.random", side_effect=[0.0] + [0.9] * 30):
            result = simulate(Config(total_bytes=8000, initial_cwnd=8, loss_pct=50))
        self.assertEqual(result.summary["fast_retransmits"], 1)
        self.assertEqual(result.summary["timeout_count"], 0)
        self.assertEqual(result.summary["delivered_bytes"], 8000)
        recovery = next(e for e in result.events if e["kind"] == "fast_retransmit")
        self.assertEqual(recovery["ssthresh"], 4)
        self.assertEqual(recovery["cwnd"], 7)
        acks = [p["ack"] for p in result.packets if p["reason"] == "cumulative_ack"]
        self.assertEqual(acks[:7], [1001] * 7)
        self.assertEqual(acks[-1], 9001)
        self.assertTrue(any(e["kind"] == "fast_recovery_exit" for e in result.events))

    def test_timeout_backoff_and_repeated_threshold(self):
        result = simulate(Config(total_bytes=8000, initial_cwnd=8, loss_pct=100, max_time=8))
        timeouts = [e for e in result.events if e["kind"] == "timeout"]
        self.assertEqual([e["rto_ms"] for e in timeouts], [2000, 4000, 8000])
        self.assertEqual([e["ssthresh"] for e in timeouts], [4, 4, 4])
        self.assertFalse(result.summary["completed"])
        # Existing flight can exceed the reduced cwnd after a loss.
        self.assertTrue(any(s["inflight"] > s["cwnd"] * 1000 for s in result.samples))
        self.assertEqual(result.summary["duration"], 8)

    def test_partial_outcomes_include_handshake_and_in_transit(self):
        early = simulate(Config(max_time=0.01))
        self.assertFalse(early.summary["completed"])
        self.assertEqual(early.summary["delivered_bytes"], 0)
        self.assertEqual(len(early.packets), 1)
        self.assertIsNone(early.packets[0]["arrival"])
        partial = simulate(Config(loss_pct=0, max_time=0.2))
        self.assertGreater(partial.summary["delivered_bytes"], 0)
        self.assertLess(partial.summary["delivered_bytes"], partial.config.total_bytes)
        self.assertEqual(partial.summary["stop_reason"], "max_time")
        self.assertTrue(all(p["arrival"] is None or p["arrival"] <= 0.2 for p in partial.packets))

    def test_payload_completion_does_not_claim_closed_connection(self):
        result = simulate(Config(total_bytes=1000, loss_pct=0, max_time=0.2))
        self.assertTrue(result.summary["completed"])
        self.assertFalse(result.summary["connection_closed"])
        self.assertEqual(result.summary["stop_reason"], "max_time")

    def test_late_original_packet_and_retransmissions_do_not_duplicate_delivery(self):
        result = simulate(Config(total_bytes=9000, mss=9000, bandwidth_mbps=0.01,
                                 loss_pct=0, max_time=60))
        self.assertTrue(result.summary["completed"])
        self.assertGreater(result.summary["retransmissions"], 0)
        self.assertEqual(result.summary["delivered_bytes"], 9000)
        self.assertTrue(all(s["delivered"] <= 9000 for s in result.samples))

    def test_estimator_uses_old_srtt_for_rttvar_and_one_second_floor(self):
        estimator = _RttEstimator()
        self.assertEqual(estimator.rto, 1)
        estimator.observe(0.100)
        self.assertAlmostEqual(estimator.srtt, 0.100)
        self.assertAlmostEqual(estimator.rttvar, 0.050)
        estimator.observe(0.140)
        self.assertAlmostEqual(estimator.srtt, 0.105)
        self.assertAlmostEqual(estimator.rttvar, 0.0475)
        self.assertEqual(estimator.rto, 1)

    def test_delivered_bytes_and_acks_are_monotonic_despite_loss(self):
        result = simulate(Config(total_bytes=64000, loss_pct=8, seed=42))
        for key in ("delivered", "acknowledged"):
            values = [s[key] for s in result.samples]
            self.assertEqual(values, sorted(values))
            self.assertTrue(all(0 <= value <= result.config.total_bytes for value in values))
        self.assertTrue(all(s["acknowledged"] <= s["delivered"] for s in result.samples))
        original = {p["seq"]: p["length"] for p in result.packets
                    if p["length"] and not p["retransmission"]}
        for packet in result.packets:
            if packet["retransmission"]:
                self.assertEqual(original[packet["seq"]], packet["length"])
        self.assertTrue(all(s["rto_ms"] >= 1000 for s in result.rtt_samples))

    def test_invalid_config(self):
        invalid = [
            {"total_bytes": 0}, {"total_bytes": True}, {"mss": 0}, {"mss": 1000.5},
            {"receiver_window": 0}, {"loss_pct": -1}, {"loss_pct": 101},
            {"rtt_ms": float("nan")}, {"bandwidth_mbps": float("inf")},
            {"rtt_ms": 10**1000},
            {"max_time": 0}, {"seed": "7"}, {"initial_cwnd": 0},
            {"initial_ssthresh": 1}, {"receiver_window": 1, "total_bytes": 20001},
        ]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                Config(**values)
        with self.assertRaises(TypeError):
            simulate({})


if __name__ == "__main__":
    unittest.main()
