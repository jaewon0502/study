"""Plot semantics tested with synthetic fixtures, never live traffic records."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import matplotlib
matplotlib.use('Agg')
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from observed_plots import (capture_axes, capture_figure, capture_flows,
                            capture_metrics, plot_capture, plot_socket,
                            socket_axes, socket_figure, socket_metrics)


def packet(at, *, reverse=False, seq=1000, ack=None, window=4096,
           length=0, flags='.', flow='fixture-a'):
    src, dst = ('192.0.2.1', 50000), ('198.51.100.2', 443)
    if reverse:
        src, dst = dst, src
    return dict(time=at, src_ip=src[0], src_port=src[1], dst_ip=dst[0],
                dst_port=dst[1], flags=flags, seq=seq,
                seq_end=seq + length if seq is not None else None, ack=ack,
                window=window, window_scale=7, payload_length=length, flow_id=flow)


def capture():
    return dict(source='capture_file_tcp_metadata', status='complete',
                dropped_packets=None, packets=[
                    packet(100, seq=1000, flags='S'),
                    packet(100.1, reverse=True, seq=8000, ack=1001, flags='S.'),
                    packet(100.2, seq=None, ack=8001),
                    packet(100.3, seq=1001, ack=8001, length=100, flags='P.'),
                    packet(100.4, reverse=True, seq=None, ack=1101),
                    packet(100.5, seq=900000, window=60000, length=80000, flow='fixture-b')])


def socket_result():
    return dict(source='local_loopback_tcp_measurement', status='complete', samples=[
        dict(elapsed=.1, sent_bytes=1024, received_bytes=1024,
             tx_mbps=.1, rx_mbps=.09, app_echo_ms=.7, cwnd_bytes=8192,
             ssthresh_bytes=2**31, snd_wnd_bytes=65536, rcv_wnd_bytes=32768,
             send_buffer_bytes=100, rtt_ms=0, srtt_ms=.1, rto_ms=0,
             retransmit_packets=0, info_available=True),
        dict(elapsed=.2, sent_bytes=2048, received_bytes=2048,
             tx_mbps=.2, rx_mbps=.19, app_echo_ms=.8, cwnd_bytes=None,
             ssthresh_bytes=None, snd_wnd_bytes=None, rcv_wnd_bytes=None,
             send_buffer_bytes=None, rtt_ms=None, srtt_ms=None, rto_ms=None,
             retransmit_packets=None, info_available=False)])


class ObservedPlotTests(unittest.TestCase):
    def setUp(self):
        self.figures = []

    def tearDown(self):
        for figure in self.figures:
            figure.clear()

    def figure(self):
        figure = Figure(figsize=(17, 10.4))
        self.figures.append(figure)
        return figure

    def plot(self, result=None, flow='fixture-a', view='flow'):
        axes = capture_axes(self.figure(), view=view)
        plot_capture(capture() if result is None else result, flow, axes, view=view)
        return axes

    def test_flow_inventory_preserves_first_seen_order(self):
        self.assertEqual([flow for flow, _ in capture_flows(capture())], ['fixture-a', 'fixture-b'])
        self.assertIn('192.0.2.1:50000', capture_flows(capture())[0][1])

    def test_mixed_flows_do_not_change_selected_sequence_or_metrics(self):
        axes = self.plot()
        seq = list(axes[0].lines[0].get_ydata())
        self.assertEqual(seq[0], 0)
        self.assertTrue(math.isnan(seq[1]))
        self.assertEqual(seq[2], 1)
        self.assertEqual(capture_metrics(capture(), 'fixture-a')[:2],
                         [('선택 연결 · 포착 패킷', '5개'), ('선택 연결 · 관측 페이로드', '100 B')])
        self.assertEqual(capture_metrics(capture(), 'fixture-a')[3][1], '확인 불가')
        self.assertLess(max(axes[2].lines[0].get_ydata()), 1)

    def test_ack_uses_opposite_sequence_space_and_missing_seq_remains_unknown(self):
        axes = self.plot()
        self.assertEqual(list(axes[1].lines[1].get_ydata()), [1, 101])
        self.assertTrue(math.isnan(axes[1].lines[0].get_ydata()[1]))
        self.assertTrue(math.isnan(axes[0].lines[1].get_ydata()[0]))
        self.assertIn('B ACK · A 기준', axes[1].get_legend_handles_labels()[1])

    def test_window_is_raw_even_if_syn_scale_was_observed(self):
        axes = self.plot()
        self.assertEqual(list(axes[3].lines[0].get_ydata()), [4096, 4096, 4096])
        self.assertIn('스케일 미적용', axes[3].get_ylabel())

    def test_payload_rate_bins_keep_boundary_packets_in_the_correct_interval(self):
        axes = self.plot()
        rates = list(axes[2].lines[0].get_ydata())
        self.assertEqual(rates[:3], [0, 0, 0])
        self.assertAlmostEqual(rates[3], .008)

    def test_capture_dropped_zero_is_observed_not_missing(self):
        result = capture()
        result['dropped_packets'] = 0
        self.assertEqual(capture_metrics(result)[3][1], '0개')

    def test_header_table_has_only_selected_flow_and_omitted_fields_are_dashes(self):
        axes = self.plot(view='headers')
        table = next(iter(axes[1].tables))
        self.assertEqual(table[3, 2].get_text().get_text(), '—')
        self.assertEqual(table[2, 4].get_text().get_text(), '4096')
        self.assertNotIn('900000', [cell.get_text().get_text() for cell in table.get_celld().values()])
        text = '\n'.join(t.get_text() for t in axes[0].texts)
        self.assertIn('상대 도착 여부는 알 수 없습니다', text)

    def test_no_syn_does_not_claim_full_handshake(self):
        result = capture()
        result['packets'] = result['packets'][2:5]
        axes = self.plot(result)
        self.assertIn('SYN 미관측', '\n'.join(t.get_text() for t in axes[5].texts))

    def test_socket_missing_metrics_break_trace_and_real_zero_is_preserved(self):
        axes = socket_axes(self.figure())
        plot_socket(socket_result(), axes)
        self.assertEqual(axes[0].lines[0].get_ydata()[0], 8)
        self.assertTrue(math.isnan(axes[0].lines[0].get_ydata()[1]))
        self.assertEqual(axes[1].lines[3].get_ydata()[0], 0)
        self.assertEqual(axes[5].lines[0].get_ydata()[0], 0)
        self.assertTrue(math.isnan(axes[5].lines[0].get_ydata()[1]))
        self.assertEqual(axes[0].get_yscale(), 'symlog')
        self.assertEqual(socket_metrics(socket_result())[2][1], '확인 불가')
        self.assertIn('다른 값', axes[4].texts[0].get_text())

    def test_error_empty_and_complete_figures_render(self):
        for result in ({}, {'status': 'error', 'error': '캡처 권한 없음'}, capture()):
            for view in ['flow', 'headers']:
                fig = capture_figure(result, view=view)
                self.figures.append(fig)
                FigureCanvasAgg(fig).draw()
                self.assertEqual(len(fig.axes), 6 if view == 'flow' else 2)
        for result in ({}, {'error': '측정 오류'}, socket_result()):
            fig = socket_figure(result)
            self.figures.append(fig)
            FigureCanvasAgg(fig).draw()
            self.assertEqual(len(fig.axes), 6)


if __name__ == '__main__':
    unittest.main()
