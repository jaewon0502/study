"""Graph semantics against synthetic, documentation-only network addresses."""
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import matplotlib
matplotlib.use('Agg')
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from live_plots import live_axes, live_figure, live_metrics, pick_interface, plot_live


def snapshot(at, *, source='psutil', established=3, listen=1, rx=2.0):
    return {
        'monotonic': at, 'timestamp': '2026-10-13T12:00:00+09:00',
        'cpu_percent': 25.0, 'memory_percent': 50.0,
        'connection_source': source,
        'connection_states': {'ESTABLISHED': established, 'LISTEN': listen},
        'connections': [{'local': '192.0.2.10:50000', 'remote': '198.51.100.8:443',
                         'state': 'ESTABLISHED'}] if source != 'unavailable' else [],
        'interfaces': [
            {'name': 'en0', 'is_up': True, 'addresses': ['192.0.2.10'],
             'rx_bytes': 1000, 'tx_bytes': 2000, 'rx_mbps': rx, 'tx_mbps': 1.0},
            {'name': 'utun0', 'is_up': True, 'addresses': ['2001:db8::10'],
             'rx_bytes': 9000, 'tx_bytes': 9000, 'rx_mbps': 80.0, 'tx_mbps': 90.0},
        ],
    }


class LiveGraphTests(unittest.TestCase):
    def make_axes(self, history, interface='en0'):
        fig = Figure(figsize=(17, 10.4))
        axes = live_axes(fig)
        with patch('live_plots.time.monotonic', return_value=20):
            plot_live(history, interface, axes)
        self.addCleanup(fig.clear)
        return axes

    def test_missing_tcp_query_breaks_trace_instead_of_becoming_zero(self):
        history = [snapshot(10), snapshot(11, source='unavailable'),
                   snapshot(12, established=0, listen=0)]
        ax = self.make_axes(history)[2]
        established, listen = (line.get_ydata() for line in ax.lines)
        self.assertEqual(established[0], 3)
        self.assertTrue(math.isnan(established[1]))
        self.assertTrue(math.isnan(listen[1]))
        self.assertEqual(established[2], 0)
        self.assertEqual(listen[2], 0)
        self.assertEqual(live_metrics(history[1], 'en0')[-1][1], '확인 불가')

    def test_tcp_available_observations_use_latest_relative_time_and_named_legend(self):
        ax = self.make_axes([snapshot(10), snapshot(12, established=8, listen=2)])[2]
        self.assertEqual(list(ax.lines[0].get_xdata()), [-2, 0])
        self.assertEqual(list(ax.lines[0].get_ydata()), [3, 8])
        self.assertEqual(list(ax.lines[1].get_ydata()), [1, 2])
        labels = ax.get_legend_handles_labels()[1]
        self.assertIn('연결됨 ESTABLISHED', labels)
        self.assertIn('수신 대기 LISTEN', labels)
        self.assertNotEqual(ax.lines[0].get_linestyle(), ax.lines[1].get_linestyle())

    def test_selected_interface_is_not_aggregated_and_absence_breaks_trace(self):
        history = [snapshot(10, rx=2), snapshot(11), snapshot(12, rx=4)]
        history[1]['interfaces'] = history[1]['interfaces'][1:]
        ax = self.make_axes(history)[0]
        self.assertEqual(list(ax.lines[0].get_xdata()), [-2, -1, 0])
        values = ax.lines[0].get_ydata()
        self.assertEqual(values[0], 2)
        self.assertTrue(math.isnan(values[1]))
        self.assertEqual(values[2], 4)
        self.assertEqual(pick_interface(history[0]), 'en0')
        self.assertEqual(pick_interface(history[0], 'utun0'), 'utun0')
        self.assertEqual(ax.get_legend_handles_labels()[1], ['수신 RX', '송신 TX'])

    def test_missing_cpu_sample_is_not_connected_across_gap(self):
        history = [snapshot(10), snapshot(11), snapshot(12)]
        history[1]['cpu_percent'] = None
        self.assertTrue(math.isnan(self.make_axes(history)[1].lines[0].get_ydata()[1]))

    def test_source_scope_age_and_unavailable_time_wait_are_visible(self):
        axes = self.make_axes([snapshot(12, source='lsof (현재 계정 조회)')])
        nic_text = '\n'.join(t.get_text() for t in axes[3].texts)
        tcp_text = '\n'.join(t.get_text() for t in axes[4].texts)
        self.assertIn('마지막 관측 8.0초 전', nic_text)
        self.assertIn('출처: lsof (현재 계정 조회)', tcp_text)
        self.assertIn('TIME_WAIT 확인 불가', tcp_text)
        self.assertIn('현재 계정에서 조회한 항목', tcp_text)

    def test_empty_history_and_export_layout_render_without_measurements(self):
        for history in ([], [snapshot(10, rx=None)]):
            with patch('live_plots.time.monotonic', return_value=12):
                fig = live_figure(history, 'en0')
            self.addCleanup(fig.clear)
            FigureCanvasAgg(fig).draw()
            self.assertEqual(len(fig.axes), 5)
            self.assertTrue(all(ax.get_position().y0 > fig.axes[3].get_position().y1
                                for ax in fig.axes[:3]))
            self.assertGreater(fig.axes[3].get_position().width, fig.axes[0].get_position().width)


if __name__ == '__main__':
    unittest.main()
