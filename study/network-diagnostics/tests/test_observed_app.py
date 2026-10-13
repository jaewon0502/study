"""Observed-mode integration checks; fixtures use reserved TEST-NET addresses."""

from concurrent.futures import Future
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import matplotlib
matplotlib.use('Agg')
from matplotlib.backend_bases import MouseEvent
import main as app_module
from packet_capture import parse_tcpdump_line
from socket_metrics import SocketTestConfig, run_socket_test
from tcp_sim import Config


def packet_fixture(port=50000, time=1700000000.1):
    return parse_tcpdump_line(
        f'{time} IP 192.0.2.10.{port} > 198.51.100.20.443: '
        'Flags [P.], seq 100:112, ack 200, win 512, length 12')


def socket_sample(elapsed=.25):
    return dict(elapsed=elapsed, sent_bytes=16384, received_bytes=16384,
                tx_mbps=.52, rx_mbps=.52, app_echo_ms=.3, cwnd_bytes=20000,
                ssthresh_bytes=1000000000, snd_wnd_bytes=64000, rcv_wnd_bytes=64000,
                send_buffer_bytes=0, rtt_ms=0, srtt_ms=0, rto_ms=0,
                retransmit_packets=0, platform='test_fixture',
                source='local_loopback_tcp_measurement', info_available=True,
                info_error=None)


class PendingExecutor:
    """Record real submitted callback contracts without running device workers."""
    def __init__(self):
        self.jobs = []

    def submit(self, function, *args, **kwargs):
        future = Future()
        self.jobs.append((future, function, args, kwargs))
        return future

    def shutdown(self, **kwargs):
        for future, _, _, _ in self.jobs:
            if not future.done():
                future.cancel()


class ObservedInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.executor = PendingExecutor()
        self.executor_patch = patch('main.ThreadPoolExecutor', return_value=self.executor)
        self.executor_patch.start()
        self.apps = []

    def tearDown(self):
        for app in self.apps:
            app.close()
            app.plt.close(app.fig)
        self.executor_patch.stop()

    def make_app(self, view=None):
        app = app_module.Lab(Config(total_bytes=8000), **({'start_view': view} if view else {}))
        self.apps.append(app)
        return app

    @staticmethod
    def click(app, button):
        canvas = app.fig.canvas
        canvas.draw()
        x, y = button.ax.transAxes.transform((.5, .5))
        for name in ('button_press_event', 'button_release_event'):
            canvas.callbacks.process(name, MouseEvent(name, canvas, x, y, button=1))

    def test_default_five_tabs_never_invoke_simulations_or_active_tests(self):
        with patch('main.simulate') as simulate, patch('main.compare_rdt') as compare:
            app = self.make_app()
            self.assertEqual(app.view, 'live')
            self.assertEqual(set(app.tab_buttons), {'live', 'diagnostic', 'socket', 'capture', 'packet'})
            for tab in app.tab_buttons:
                app.select(tab)
            simulate.assert_not_called()
            compare.assert_not_called()
        self.assertIsNone(app.result)
        self.assertIsNone(app.socket_result)
        self.assertIsNone(app.capture_result)
        self.assertEqual(len(self.executor.jobs), 1)
        self.assertEqual(self.executor.jobs[0][1], app.read_system)
        self.assertTrue(all(not slider.active for slider in app.sliders.values()))

    def test_socket_callback_draws_before_completion_and_finish_respects_hidden_widgets(self):
        app = self.make_app('socket')
        self.click(app, app.socket_run_button)
        future, function, args, _ = self.executor.jobs[-1]
        self.assertEqual(function, run_socket_test)
        sample = socket_sample()
        args[2](sample)
        app.observed_tick()
        self.assertFalse(future.done())
        self.assertEqual(app.socket_result['samples'], [sample])
        self.assertIn('측정 중 · 1개 표본', app.status.get_text())
        self.assertEqual(list(app.chart_axes[0].lines[0].get_ydata()), [sample['cwnd_bytes'] / 1024])
        self.assertFalse(app.socket_run_button.active)
        self.assertTrue(app.socket_cancel_button.active)
        self.assertTrue(all(not field.active for field in app.socket_inputs.values()))
        app.select('capture')
        self.assertFalse(app.socket_cancel_button.active)
        complete = {**app.socket_result, 'status': 'completed', 'cancelled': False}
        future.set_result(complete)
        app.observed_tick()
        self.assertIsNone(app.socket_future)
        self.assertFalse(app.socket_run_button.active)
        self.assertTrue(all(not field.active for field in app.socket_inputs.values()))
        self.assertTrue(app.capture_run_button.active)
        before = len(self.executor.jobs)
        self.click(app, app.capture_run_button)
        self.assertEqual(len(self.executor.jobs), before + 1)
        self.assertIsNone(app.socket_future)
        self.assertIsNotNone(app.capture_future)
        self.assertIsNone(app.fig.canvas.mouse_grabber)

    def test_capture_rows_are_drawn_incrementally_and_flow_is_shared_across_views(self):
        app = self.make_app('capture')
        app.start_capture()
        future, _, _, kwargs = self.executor.jobs[-1]
        first, second = packet_fixture(), packet_fixture(50001, 1700000000.2)
        kwargs['on_packet'](first)
        app.observed_tick()
        self.assertFalse(future.done())
        self.assertEqual(app.capture_result['packets'], [first])
        self.assertEqual(app.capture_flow, first['flow_id'])
        self.assertIn('1개 패킷', app.status.get_text())
        kwargs['on_packet'](second)
        app.observed_next_draw = 0
        app.observed_tick()
        self.assertEqual(len(app.capture_result['packets']), 2)
        app.cycle_flow()
        self.assertEqual(app.capture_flow, second['flow_id'])
        import observed_plots
        with patch('observed_plots.plot_capture', wraps=observed_plots.plot_capture) as plot:
            app.select('packet')
            self.assertEqual(app.capture_flow, second['flow_id'])
            self.assertEqual(plot.call_args.args[1], second['flow_id'])
            self.assertEqual(plot.call_args.kwargs['view'], 'headers')
            self.assertEqual(len(app.chart_axes), 2)
            app.select('capture')
            self.assertEqual(plot.call_args.args[1], second['flow_id'])
            self.assertEqual(plot.call_args.kwargs['view'], 'flow')
        self.assertTrue(all(not field.active for field in app.capture_inputs.values()))
        self.assertTrue(app.capture_cancel_button.active)

    def test_capture_permission_failure_stays_empty_error_without_simulated_fallback(self):
        app = self.make_app('capture')
        with patch('main.simulate') as simulate:
            app.start_capture()
            future = app.capture_future
            denied = {**app.capture_result, 'status': 'error',
                      'error': 'Permission denied: packet capture access is unavailable.'}
            future.set_result(denied)
            app.observed_tick()
            simulate.assert_not_called()
        self.assertEqual(app.capture_result['status'], 'error')
        self.assertEqual(app.capture_result['packets'], [])
        self.assertIn('Permission denied', app.status.get_text())
        self.assertIn('실행 실패', app.status.get_text())
        self.assertFalse(any(axis.lines for axis in app.chart_axes))
        self.assertTrue(any('Permission denied' in text.get_text()
                            for axis in app.chart_axes for text in axis.texts))
        self.assertIsNone(app.capture_future)
        self.assertTrue(app.capture_run_button.active)
        self.assertFalse(app.capture_cancel_button.active)

    def test_jsonl_follow_uses_incremental_callback_without_starting_live_capture(self):
        app = self.make_app('packet')
        app.capture_inputs['path'].set_val('captures/live_tcp.jsonl')
        app.start_capture_file()
        future, function, args, kwargs = self.executor.jobs[-1]
        from packet_capture import follow_metadata
        self.assertEqual(function, follow_metadata)
        self.assertEqual(kwargs['path'], ROOT / 'captures/live_tcp.jsonl')
        self.assertEqual(app.capture_result['source'], 'live_tcp_metadata_file')
        self.assertEqual(args, ())
        kwargs['on_packet'](packet_fixture())
        app.observed_tick()
        self.assertFalse(future.done())
        self.assertEqual(len(app.capture_result['packets']), 1)
        self.assertEqual(len(app.chart_axes), 2)


class ObservedCliAndExportTests(unittest.TestCase):
    def test_actual_headless_tcp_test_returns_real_json_without_gui(self):
        environment = dict(os.environ, MPLBACKEND='Agg')
        process = subprocess.run(
            [sys.executable, str(ROOT / 'main.py'), '--tcp-test', '--duration', '1', '--chunk-kib', '1'],
            capture_output=True, text=True, timeout=10, env=environment)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result['source'], 'local_loopback_tcp_measurement')
        self.assertEqual(result['status'], 'completed')
        self.assertGreater(result['summary']['sent_bytes'], 0)
        self.assertEqual(result['summary']['sent_bytes'], result['summary']['received_bytes'])
        self.assertGreater(len(result['samples']), 1)
        self.assertTrue(any(row['app_echo_ms'] is not None for row in result['samples']))

    def test_socket_export_preserves_actual_metadata_and_all_samples(self):
        result = run_socket_test(SocketTestConfig(duration=1, interval=.25, chunk_bytes=1024))
        with tempfile.TemporaryDirectory() as folder:
            exported = app_module.export_observed(result, folder)
            self.assertEqual(json.loads((exported / 'tcp_socket.json').read_text()), result)
            with (exported / 'tcp_samples.csv').open(encoding='utf-8-sig', newline='') as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), len(result['samples']))
            self.assertEqual(int(rows[-1]['sent_bytes']), result['summary']['sent_bytes'])
            self.assertEqual(rows[-1]['source'], 'local_loopback_tcp_measurement')
            self.assertGreater((exported / 'tcp_socket.png').stat().st_size, 10000)

    def test_capture_export_preserves_missing_header_fields_and_all_packets(self):
        packet = packet_fixture()
        packet['seq'] = None
        result = dict(source='capture_file_tcp_metadata', status='complete', error=None,
                      packets=[packet, packet_fixture(50001, 1700000000.2)], warnings=[],
                      dropped_packets=None, duration_seconds=.1)
        with tempfile.TemporaryDirectory() as folder:
            exported = app_module.export_observed(result, folder, 'capture', packet['flow_id'])
            self.assertEqual(json.loads((exported / 'tcp_capture.json').read_text()), result)
            with (exported / 'tcp_packets.csv').open(encoding='utf-8-sig', newline='') as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]['seq'], '')
            self.assertEqual(rows[0]['ack'], '200')
            for name in ('tcp_capture_flow.png', 'tcp_capture_packets.png'):
                self.assertGreater((exported / name).stat().st_size, 10000)


if __name__ == '__main__':
    unittest.main()
