import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rdt_sim import compare_rdt


class RdtComparisonTests(unittest.TestCase):
    def test_default_trace_delivers_every_packet_deterministically(self):
        results = compare_rdt()
        self.assertEqual(results, compare_rdt())
        self.assertEqual([r["name"] for r in results], ["Stop-and-Wait", "GBN", "SR"])
        for result in results:
            self.assertTrue(result["completed"])
            self.assertEqual(result["delivered_packets"], 12)
            lost = [e for e in result["events"] if e["lost"]]
            self.assertEqual(len(lost), 1)
            self.assertEqual((lost[0]["kind"], lost[0]["packet"]), ("DATA", 3))
            self.assertFalse(lost[0]["retransmission"])
            self.assertEqual(result["transmissions"], 12 + result["retransmissions"])
            self.assertEqual(result["duration"], max(e["arrival"] for e in result["events"]))

    def test_selective_repeat_buffers_and_only_retries_the_missing_packet(self):
        sw, gbn, sr = compare_rdt()
        self.assertEqual(sw["retransmissions"], 1)
        self.assertEqual(gbn["retransmissions"], 4)
        self.assertEqual(sr["retransmissions"], 1)
        self.assertEqual(gbn["peak_buffer_packets"], 0)
        self.assertGreater(sr["peak_buffer_packets"], gbn["peak_buffer_packets"])
        self.assertEqual({e["packet"] for e in sr["events"] if e["retransmission"]}, {3})
        # Before recovery of packet 3, GBN repeatedly acknowledges packet 2.
        duplicate_acks = [e for e in gbn["events"] if e["kind"] == "ACK" and e["packet"] == 2]
        self.assertGreater(len(duplicate_acks), 1)

    def test_no_loss_duration_has_serialization_and_propagation(self):
        sw, gbn, sr = compare_rdt(loss=False)
        self.assertAlmostEqual(sw["duration"], 12 * 0.088)
        self.assertAlmostEqual(gbn["duration"], 3 * 0.088 + 3 * 0.008)
        self.assertAlmostEqual(gbn["duration"], sr["duration"])
        for result in (sw, gbn, sr):
            self.assertEqual(result["transmissions"], 12)
            self.assertEqual(result["retransmissions"], 0)
            self.assertEqual(result["peak_buffer_packets"], 0)

    def test_sender_respects_sequence_window_even_with_selective_acks(self):
        for result in compare_rdt(packet_count=19, window=4):
            selective = result["name"] == "SR"
            window = 1 if result["name"] == "Stop-and-Wait" else 4
            acknowledged = set()
            base = 1
            timeline = []
            for event in result["events"]:
                if event["kind"] == "ACK":
                    timeline.append((event["arrival"], 0, event))
                else:
                    timeline.append((event["time"], 1, event))
            for _, order, event in sorted(timeline, key=lambda row: row[:2]):
                if order == 0:
                    if selective:
                        acknowledged.add(event["packet"])
                        while base in acknowledged:
                            base += 1
                    else:
                        base = max(base, event["packet"] + 1)
                else:
                    self.assertLess(event["packet"], base + window)

    def test_zero_propagation_last_packet_loss_and_window_one(self):
        for lost_packet in (1, 5):
            for result in compare_rdt(5, 1, lost_packet, rtt_ms=0):
                self.assertTrue(result["completed"])
                self.assertEqual(result["delivered_packets"], 5)
                self.assertEqual(result["retransmissions"], 1)

    def test_invalid_inputs_are_rejected(self):
        for kwargs in (
            {"packet_count": 0}, {"packet_count": True}, {"packet_count": 2.5},
            {"window": 0}, {"window": False}, {"window": "4"},
            {"lost_packet": 0}, {"lost_packet": 13}, {"lost_packet": 3.0},
            {"rtt_ms": -1}, {"rtt_ms": float("nan")},
            {"rtt_ms": float("inf")}, {"rtt_ms": "80"}, {"rtt_ms": True},
            {"loss": 1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                compare_rdt(**kwargs)
        self.assertTrue(all(r["completed"] for r in compare_rdt(1, loss=False)))


if __name__ == "__main__":
    unittest.main()
