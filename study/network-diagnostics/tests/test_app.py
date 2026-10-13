import contextlib
from concurrent.futures import Future
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import matplotlib
matplotlib.use('Agg')
from matplotlib.backend_bases import MouseEvent
from diagnostic_plots import diagnostic_figure
from diagnostics import DiagnosticConfig, summarize_probes
from dataclasses import asdict
from main import Lab, export_diagnostics, main, parse_args
from tcp_sim import Config


def fixture():
    probes = [dict(attempt=1, status='ok', duration_ms=12.0, address='127.0.0.1', family='IPv4', error=None),
              dict(attempt=2, status='timeout', duration_ms=2000.0, address='127.0.0.1', family='IPv4', error='timeout')]
    return dict(source='active_tcp_connection_probe', started_at='2026-01-01T00:00:00+00:00',
                config=asdict(DiagnosticConfig('127.0.0.1', attempts=2)),
                dns=dict(status='skipped', duration_ms=None, addresses=[dict(family='IPv4', address='127.0.0.1')], error=None),
                probes=probes, summary=summarize_probes(probes), cancelled=False,
                findings=['2회 중 1회 TCP 연결에 성공했습니다.'], limitations=[])


class DiagnosticOutputTests(unittest.TestCase):
    def test_failures_are_gaps_not_zero_latency(self):
        fig = diagnostic_figure(fixture())
        values = fig.axes[0].lines[0].get_ydata()
        self.assertEqual(values[0], 12)
        self.assertTrue(math.isnan(values[1]))
        self.assertIn('실패 (지연값 없음)', fig.axes[0].get_legend_handles_labels()[1])
        fig.clear()

    def test_export_has_readable_complete_data_and_png(self):
        result = fixture()
        with tempfile.TemporaryDirectory() as temp:
            folder = export_diagnostics(result, temp)
            self.assertEqual(json.loads((folder/'diagnostics.json').read_text()), result)
            self.assertIn('timeout', (folder/'probes.csv').read_text(encoding='utf-8-sig'))
            self.assertGreater((folder/'diagnostics.png').stat().st_size, 10_000)

    def test_cli_json_failure_and_invalid_input_exit_codes(self):
        output = io.StringIO()
        with patch('diagnostics.run_diagnostics', return_value=fixture()), contextlib.redirect_stdout(output):
            self.assertEqual(main(['--diagnose', '127.0.0.1', '--attempts', '2']), 1)
        self.assertEqual(json.loads(output.getvalue())['summary']['succeeded'], 1)
        with patch('diagnostics.run_diagnostics') as run, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--diagnose', 'https://example.com']), 2)
            run.assert_not_called()

    def test_conflicting_modes_and_unrelated_exports_rejected(self):
        for args in [['--live', '--diagnose', 'localhost'], ['--status', '--export', 'exports/test']]:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit): parse_args(args)


class InterfaceStateTests(unittest.TestCase):
    def setUp(self):
        self.app = Lab(Config(total_bytes=8000), start_view='diagnostic')
        self.app.executor.shutdown(wait=True)
        self.pending = Future()
        self.app.executor = Mock()
        self.app.executor.submit.return_value = self.pending

    def tearDown(self):
        self.app.close()
        self.app.plt.close(self.app.fig)

    def click(self, button):
        canvas = self.app.fig.canvas
        canvas.draw()
        x, y = button.ax.transAxes.transform((.5, .5))
        for event in ['button_press_event', 'button_release_event']:
            canvas.callbacks.process(event, MouseEvent(event, canvas, x, y, button=1))

    def test_opening_diagnostic_tab_sends_no_probe_and_intro_does_not_overlap(self):
        self.assertIsNone(self.app.diagnostic_future)
        self.app.executor.submit.assert_not_called()
        self.app.fig.canvas.draw()
        texts = self.app.chart_axes[0].texts
        renderer = self.app.fig.canvas.get_renderer()
        self.assertFalse(texts[0].get_window_extent(renderer).overlaps(texts[1].get_window_extent(renderer)))

    def test_real_mouse_click_reaches_visible_button_only(self):
        self.click(self.app.diagnostic_run_button)
        self.assertIs(self.app.diagnostic_future, self.pending)
        self.app.executor.submit.assert_called_once()
        self.assertIsNone(self.app.fig.canvas.mouse_grabber)
        self.assertFalse(self.app.pause_button.active)
        self.assertFalse(self.app.sliders['loss_pct'].active)

    def test_progress_is_visible_before_future_finishes_and_stays_scoped_to_tab(self):
        self.app.start_diagnostic()
        self.app.diagnostic_queue.put(fixture()['probes'][0])
        self.app.diagnostic_tick()
        self.assertFalse(self.pending.done())
        self.assertEqual(self.app.diagnostic_result['summary']['attempted'], 1)
        self.assertIn('1/5회', self.app.status.get_text())
        self.assertEqual(len(self.app.chart_axes[0].lines[0].get_ydata()), 1)
        self.app.select('overview')
        self.pending.set_result(fixture())
        self.app.diagnostic_tick()
        self.assertFalse(self.app.diagnostic_run_button.active)
        self.assertTrue(all(not f.active for f in self.app.diagnostic_inputs.values()))
        self.assertIsNone(self.app.diagnostic_future)

    def test_pause_discards_pending_sample_and_resume_starts_new_baseline(self):
        self.app.view = 'live'
        self.app.live_enabled = True
        self.app.live_future = Future()
        self.app.live_future.set_result({'timestamp': 'discarded'})
        self.app.toggle_live()
        self.app.live_tick()
        self.assertEqual(len(self.app.live_history), 0)
        self.app.executor.submit.assert_not_called()
        self.app.toggle_live()
        self.app.live_tick()
        self.app.executor.submit.assert_called_once_with(self.app.read_system, True)
        self.assertFalse(self.app.live_paused)


if __name__ == '__main__':
    unittest.main()
