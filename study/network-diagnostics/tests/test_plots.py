import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import matplotlib
matplotlib.use('Agg')
from plots import report_figure
from rdt_sim import compare_rdt
from tcp_sim import Config, simulate


class GraphMeaningTests(unittest.TestCase):
    def test_cwnd_graph_exposes_phase_and_rtt_convention(self):
        result = simulate(Config(seed=1))
        fig = report_figure(result)
        ax = fig.axes[0]
        self.assertIn('설정 RTT', ax.get_xlabel())
        labels = ax.get_legend_handles_labels()[1]
        for label in ['Slow Start', '혼잡 회피', '빠른 복구']:
            self.assertIn(label, labels)
        self.assertTrue(any(t.get_text() == '3 dupACK' for t in ax.texts))
        fig.clear()

    def test_rdt_header_is_about_rdt_not_tcp(self):
        fig = report_figure(simulate(Config(loss_pct=0)), 'rdt', compare_rdt())
        text = '\n'.join(t.get_text() for t in fig.texts)
        self.assertIn('GBN 재전송', text)
        self.assertIn('4개', text)
        self.assertNotIn('평균 유효 전송률', text)
        fig.clear()

    def test_in_flight_syn_is_not_drawn_as_arrived(self):
        result = simulate(Config(max_time=.01))
        self.assertFalse(result.packets[0]['received'])
        fig = report_figure(result, 'connection')
        self.assertTrue(any('미도착' in t.get_text() for t in fig.axes[0].texts))
        self.assertTrue(any('미완료' in t.get_text() for t in fig.texts))
        fig.clear()


if __name__ == '__main__':
    unittest.main()
