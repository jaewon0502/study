"""Regression checks for redraw storms and capture bursts, not timing thresholds."""
from concurrent.futures import Future
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import matplotlib
matplotlib.use('Agg')
from matplotlib.backend_bases import MouseEvent
from main import Lab
from packet_capture import parse_tcpdump_line
from tcp_sim import Config


def packet(index=0):
    return parse_tcpdump_line(
        f'{1700000000 + index / 1000:.3f} IP 192.0.2.1.50000 > '
        '198.51.100.1.443: Flags [P.], seq 100:112, ack 200, win 512, length 12')


class ResponsiveInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.app = Lab(Config(total_bytes=8000), start_view='capture')
        self.app.timer.stop()
        self.app.capture_result = {
            'source': 'live_tcp_metadata_file', 'status': 'complete', 'error': None,
            'packets': [packet()], 'warnings': [], 'dropped_packets': None}
        self.app.draw_observed()

    def tearDown(self):
        self.app.close()
        self.app.plt.close(self.app.fig)

    def click(self, widget):
        canvas = self.app.fig.canvas
        x, y = widget.ax.transAxes.transform((.5, .5))
        for name in ('button_press_event', 'button_release_event'):
            canvas.callbacks.process(name, MouseEvent(name, canvas, x, y, button=1))

    def test_unfocused_fields_do_not_force_redundant_canvas_draws(self):
        canvas = self.app.fig.canvas
        with patch.object(canvas, 'draw', wraps=canvas.draw) as draw:
            self.app.update_widget_activity()
            for field in self.app.capture_inputs.values():
                field.stop_typing()
        self.assertEqual(draw.call_count, 0)

    def test_tab_and_flow_clicks_paint_once_without_focus(self):
        canvas = self.app.fig.canvas
        for widget, expected in [(self.app.tab_buttons['packet'], 'packet'),
                                 (self.app.tab_buttons['capture'], 'capture'),
                                 (self.app.capture_flow_button, 'capture')]:
            with self.subTest(view=expected):
                with patch.object(canvas, 'draw', wraps=canvas.draw) as draw:
                    self.click(widget)
                self.assertEqual(self.app.view, expected)
                self.assertEqual(draw.call_count, 1)

    def test_leaving_a_focused_field_still_submits_and_releases_keys(self):
        field = self.app.capture_inputs['interface']
        submitted = []
        field.on_submit(submitted.append)
        self.click(field)
        self.assertTrue(field.capturekeystrokes)
        self.click(self.app.tab_buttons['socket'])
        self.assertEqual(submitted, ['en0'])
        self.assertFalse(field.capturekeystrokes)
        self.assertFalse(field.cursor.get_visible())
        self.assertFalse(field.active)

    def test_capture_burst_yields_but_completed_result_loses_no_records(self):
        records = [packet(i) for i in range(2000)]
        self.app.capture_result['packets'] = []
        self.app.capture_result['status'] = 'running'
        future = self.app.capture_future = Future()
        for row in records:
            self.app.capture_queue.put(row)
        with patch.object(self.app, 'draw_observed'):
            self.app.observed_tick()
            self.assertEqual(len(self.app.capture_result['packets']), 128)
            self.assertEqual(self.app.capture_queue.qsize(), 1872)
            future.set_result(dict(self.app.capture_result, packets=records, status='complete'))
            self.app.observed_tick()
        self.assertIsNone(self.app.capture_future)
        self.assertEqual(len(self.app.capture_result['packets']), 2000)
        self.assertTrue(self.app.capture_queue.empty())

    def test_count_limit_explains_restarting_the_capture_not_replaying_file(self):
        self.app.capture_result['stop_reason'] = 'packet_limit'
        self.app.draw_observed()
        self.assertIn('패킷 수 제한 도달', self.app.status.get_text())
        self.assertIn('수집 터미널', self.app.help_text.get_text())


if __name__ == '__main__':
    unittest.main()
