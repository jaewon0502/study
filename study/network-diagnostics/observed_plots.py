"""Visualize observed socket counters and captured TCP headers without inference.

Only the program-owned loopback socket exposes kernel congestion counters.
Packet captures expose header observations, not delivery, loss or kernel cwnd.
"""
from collections import Counter
import math

from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MaxNLocator

from plots import configure_style, style_axis, BLUE, TEAL, INK, MUTED, ORANGE, RED


def _number(value, unit='', digits=2):
    return '확인 불가' if value is None else f'{value:,.{digits}f}{unit}'


def _value(value, divisor=1):
    return value / divisor if value is not None and math.isfinite(value) else math.nan


def _six_axes(fig, right, top, bottom):
    grid = fig.add_gridspec(2, 3, left=.055, right=right, top=top, bottom=bottom,
                           hspace=.68, wspace=.42)
    return [fig.add_subplot(grid[row, col]) for row in range(2) for col in range(3)]


def _empty(axes, message):
    for ax in axes:
        ax.axis('off')
    axes[0].text(0, .85, message, va='top', fontsize=10, color=MUTED,
                 transform=axes[0].transAxes, wrap=True)


def _series(ax, samples, fields, title, ylabel, divisor=1, log=False):
    times = [sample['elapsed'] for sample in samples]
    for key, label, color, linestyle in fields:
        ax.plot(times, [_value(sample.get(key), divisor) for sample in samples],
                label=label, color=color, ls=linestyle, marker='.', ms=3, lw=1.5)
    style_axis(ax, title, ylabel, '실측 시작 이후 (초)')
    ax.set_xlim(0, max(1, times[-1] * 1.03))
    if log:
        # Large initial ssthresh and inactive zero-valued RTO must both remain visible.
        ax.set_yscale('symlog', linthresh=1)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f'{value:g}'))
    ax.set_ylim(bottom=0)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
    ax.legend(fontsize=7, frameon=False, loc='upper left')
    if not any(math.isfinite(value) for line in ax.lines for value in line.get_ydata()):
        ax.text(.5, .4, '운영체제 제공값 없음', ha='center', color=MUTED,
                fontsize=8, transform=ax.transAxes)


def socket_metrics(result):
    samples = result.get('samples', []) if result else []
    last = samples[-1] if samples else {}
    return [('송신 앱 누적 바이트', _number(last.get('sent_bytes'), ' B', 0)),
            ('수신 앱 누적 바이트', _number(last.get('received_bytes'), ' B', 0)),
            ('커널 cwnd', _number(last.get('cwnd_bytes'), ' B', 0)),
            ('앱 응답 왕복 시간', _number(last.get('app_echo_ms'), ' ms', 3))]


def socket_axes(fig, right=.79, top=.70, bottom=.14):
    return _six_axes(fig, right, top, bottom)


def plot_socket(result, axes):
    samples = result.get('samples', []) if result else []
    if not samples:
        error = result.get('error') if result else None
        _empty(axes, f'측정하지 못했습니다.\n{error}' if error else
               '기기 내부 TCP 실측 · 루프백\n측정을 시작하면 현재 기기의 TCP 값이 표시됩니다.')
        return
    _series(axes[0], samples,
            [('cwnd_bytes', 'cwnd', BLUE, '-'),
             ('ssthresh_bytes', 'ssthresh', ORANGE, '--')],
            '01  커널 혼잡 윈도우', 'KiB · 1 이후 로그 축', 1024, log=True)
    _series(axes[1], samples,
            [('rtt_ms', '커널 RTT', BLUE, '-'),
             ('srtt_ms', '커널 SRTT', TEAL, '--'),
             ('app_echo_ms', '앱 응답 왕복', ORANGE, ':'),
             ('rto_ms', '커널 RTO · OS 보고값', MUTED, '-.')],
            '02  커널 지연 · 앱 왕복 시간', 'ms · 1 이후 로그 축', log=True)
    _series(axes[2], samples,
            [('tx_mbps', '송신 측 전송률', BLUE, '-'),
             ('rx_mbps', '수신 측 처리율', TEAL, '--')],
            '03  실측 데이터 전송률', 'Mbps')
    _series(axes[3], samples,
            [('snd_wnd_bytes', '송신 윈도우', BLUE, '-'),
             ('rcv_wnd_bytes', '수신 윈도우', TEAL, '--')],
            '04  커널 흐름 제어 윈도우', 'KiB', 1024)
    _series(axes[4], samples,
            [('send_buffer_bytes', '커널 송신 버퍼', BLUE, '-')],
            '05  송신 버퍼에 남은 데이터', '바이트')
    axes[4].text(0, -.36, '미확인 전송량(bytes in flight)과 다른 값입니다.',
                 fontsize=7, color=MUTED, transform=axes[4].transAxes)
    _series(axes[5], samples,
            [('retransmit_packets', '커널 재전송 누계', RED, '-')],
            '06  이 소켓의 재전송', '패킷')
    axes[5].yaxis.set_major_locator(MaxNLocator(nbins=5, integer=True))
    retransmits = [sample.get('retransmit_packets') for sample in samples]
    axes[5].set_ylim(0, max(1, max((n for n in retransmits if n is not None), default=0) * 1.1))


def _figure_header(fig, title, subtitle, metrics):
    fig.text(.05, .955, title, fontsize=23, weight='bold')
    fig.text(.05, .917, subtitle, fontsize=10, color=MUTED)
    for i, (label, value) in enumerate(metrics):
        x = .05 + i * .24
        fig.text(x, .86, label, fontsize=10, color=MUTED)
        fig.text(x, .82, value, fontsize=20, weight='bold', color=INK)


def socket_figure(result):
    configure_style()
    fig = Figure(figsize=(17, 10.4), dpi=120)
    _figure_header(fig, '기기 내부 TCP 실측 · 루프백',
                   '이 프로그램이 만든 로컬 TCP 연결의 운영체제 관측값 · 인터넷 경로 성능을 나타내지 않습니다.',
                   socket_metrics(result))
    plot_socket(result, socket_axes(fig, right=.95))
    fig.text(.05, .035, '일정 간격으로 보내는 로컬 테스트입니다. 두 전송률은 같은 방향을 측정합니다. 앱 왕복 시간에는 프로그램 처리 시간이 포함됩니다.',
             fontsize=9, color=MUTED)
    return fig


def _endpoint(packet, prefix):
    address = str(packet.get(f'{prefix}_ip', '?'))
    if ':' in address:
        address = f'[{address}]'
    return f"{address}:{packet.get(f'{prefix}_port', '?')}"


def capture_flows(result):
    found = {}
    for packet in (result or {}).get('packets', []):
        flow = packet.get('flow_id')
        if flow is not None and flow not in found:
            found[flow] = f'{_endpoint(packet, "src")} ↔ {_endpoint(packet, "dst")}'
    return list(found.items())


def _flow_packets(result, flow_id):
    flows = capture_flows(result)
    selected = flow_id if flow_id is not None else (flows[0][0] if flows else None)
    return sorted([packet for packet in (result or {}).get('packets', [])
                   if packet.get('flow_id') == selected], key=lambda packet: packet['time'])


def capture_metrics(result, flow_id=None):
    packets = _flow_packets(result, flow_id)
    dropped = (result or {}).get('dropped_packets')
    payload = sum(packet.get('payload_length') or 0 for packet in packets)
    return [('선택 연결 · 포착 패킷', f'{len(packets):,}개'),
            ('선택 연결 · 관측 페이로드', f'{payload:,} B'),
            ('포착된 연결', f'{len(capture_flows(result)):,}개'),
            ('캡처 도구 누락', _number(dropped, '개', 0))]


def capture_axes(fig, view='flow', right=.79, top=.70, bottom=.14):
    if view == 'flow':
        return _six_axes(fig, right, top, bottom)
    if view != 'headers':
        raise ValueError("view는 'flow' 또는 'headers'여야 합니다.")
    grid = fig.add_gridspec(1, 2, left=.055, right=right, top=top, bottom=bottom,
                           width_ratios=[1.05, 1], wspace=.22)
    return [fig.add_subplot(grid[0, col]) for col in range(2)]


def _directions(packets):
    endpoint_a = (packets[0]['src_ip'], packets[0]['src_port'])
    return [0 if (p['src_ip'], p['src_port']) == endpoint_a else 1 for p in packets]


def _sequence_bases(packets, directions):
    bases = [None, None]
    for packet, direction in zip(packets, directions):
        for key, domain in [('seq', direction), ('ack', 1 - direction)]:
            value = packet.get(key)
            if value is not None and bases[domain] is None:
                bases[domain] = value
    return bases


def _relative(value, base):
    if value is None or base is None:
        return math.nan
    return (value - base + 2**31) % 2**32 - 2**31


def _capture_axis(ax, title, ylabel, duration):
    style_axis(ax, title, ylabel, '첫 포착 이후 (초)')
    ax.set_xlim(0, max(.1, duration * 1.03))
    ax.xaxis.set_major_locator(MaxNLocator(nbins=5))


def _capture_scope(ax, result, packets, bases):
    ax.axis('off')
    ax.set_title('06  관측 범위와 기준', loc='left', pad=12)
    has_syn = any('S' in p.get('flags', '') for p in packets)
    source = (result or {}).get('source', '확인 불가')
    source_label = ('기기에서 실시간 포착' if source == 'live_tcp_packet_metadata'
                    else '저장된 캡처 기록')
    lines = [f'출처: {source_label}',
             'A는 처음 포착한 송신 측입니다.',
             'B는 그 패킷의 상대 측입니다.',
             f'A 번호 기준: {bases[0] if bases[0] is not None else "없음"}',
             f'B 번호 기준: {bases[1] if bases[1] is not None else "없음"}',
             'SYN 관측 있음 · 전체 핸드셰이크는 미판정' if has_syn else 'SYN 미관측 · 중간 구간일 수 있습니다.',
             '반복 패킷을 포함하므로 goodput이 아닙니다.',
             '캡처 누락은 네트워크 손실률이 아닙니다.']
    ax.text(0, .95, '\n'.join(lines), va='top', fontsize=8.3,
            linespacing=1.6, color=MUTED, transform=ax.transAxes)


def _plot_packet_flow(result, packets, axes):
    origin = packets[0]['time']
    duration = max(0, packets[-1]['time'] - origin)
    directions = _directions(packets)
    bases = _sequence_bases(packets, directions)
    for direction, ax in enumerate(axes[:2]):
        selected = [packet for packet, observed_direction in zip(packets, directions)
                    if direction == observed_direction]
        times = [packet['time'] - origin for packet in selected]
        name, opposite = ('A', 'B') if direction == 0 else ('B', 'A')
        ax.plot(times, [_relative(p.get('seq'), bases[direction]) for p in selected],
                '.', color=BLUE, ms=5, label=f'{name} SEQ · {name} 기준')
        ax.plot(times, [_relative(p.get('ack'), bases[1-direction]) for p in selected],
                'x', color=TEAL, ms=4, label=f'{name} ACK · {opposite} 기준')
        _capture_axis(ax, f'0{direction+1}  {name} → {opposite} · SEQ / ACK',
                      '처음 관측된 번호 기준 (바이트)', duration)
        ax.legend(fontsize=7, frameon=False, loc='upper left')
        if not any(math.isfinite(v) for line in ax.lines for v in line.get_ydata()):
            ax.text(.5, .4, '번호 관측값 없음', ha='center', color=MUTED,
                    fontsize=8, transform=ax.transAxes)

    width = max(.1, duration / 30)
    count = max(1, math.ceil(round(duration / width, 6)))
    for direction, color, label, linestyle in [(0, BLUE, 'A → B', '-'), (1, TEAL, 'B → A', '--')]:
        bins = [0] * count
        for packet, observed_direction in zip(packets, directions):
            if direction == observed_direction:
                index = min(count - 1, int(round((packet['time'] - origin) / width, 6)))
                bins[index] += packet.get('payload_length') or 0
        axes[2].step([i * width for i in range(count + 1)],
                     [value * 8 / width / 1e6 for value in bins] + [bins[-1] * 8 / width / 1e6],
                     where='post', color=color, ls=linestyle, label=label, lw=1.6)
    _capture_axis(axes[2], '03  포착된 페이로드 전송률', 'Mbps · 반복 관측 포함', count * width)
    axes[2].set_ylim(bottom=0)
    axes[2].legend(fontsize=7, frameon=False)

    for direction, color, label, linestyle in [(0, BLUE, 'A → B', '-'), (1, TEAL, 'B → A', '--')]:
        selected = [p for p, d in zip(packets, directions) if d == direction]
        axes[3].plot([p['time'] - origin for p in selected],
                     [_value(p.get('window')) for p in selected],
                     color=color, ls=linestyle, marker='.', ms=3, lw=1, label=label)
    _capture_axis(axes[3], '04  TCP 헤더의 Window 원시값', '16비트 필드 · 스케일 미적용', duration)
    axes[3].set_ylim(bottom=0)
    axes[3].legend(fontsize=7, frameon=False)

    counts = Counter(packet.get('flags') or '(없음)' for packet in packets)
    ordered = counts.most_common(8)
    axes[4].bar([flags for flags, _ in ordered], [count for _, count in ordered], color=BLUE, alpha=.85)
    style_axis(axes[4], '05  관측된 TCP Flags · 상위 8개', '패킷', 'S=SYN · F=FIN · R=RST · .=ACK')
    axes[4].yaxis.set_major_locator(MaxNLocator(nbins=5, integer=True))
    _capture_scope(axes[5], result, packets, bases)


def _short_endpoint(packet, prefix):
    value = _endpoint(packet, prefix)
    return value if len(value) <= 25 else value[:11] + '…' + value[-11:]


def _header_value(value):
    return '—' if value is None else str(value)


def _plot_headers(result, packets, axes):
    ladder, table_ax = axes
    selected = packets if len(packets) <= 10 else packets[:5] + packets[-5:]
    directions = _directions(packets)
    direction_by_id = {id(packet): direction for packet, direction in zip(packets, directions)}
    origin = packets[0]['time']
    ladder.set_title('포착 순서 · 앞 5개 / 뒤 5개', loc='left', pad=12)
    ladder.set_xlim(-.2, 1.2)
    ladder.set_ylim(len(selected) + .9, -1.8)
    for x, prefix, label in [(0, 'src', 'A'), (1, 'dst', 'B')]:
        ladder.plot([x, x], [-.25, len(selected) - .5], color='#9aaabe', ls='--', lw=1)
        ladder.text(x, -1.15, f'{label}\n{_short_endpoint(packets[0], prefix)}',
                    ha='center', fontsize=8, color=INK, linespacing=1.6)
    for row, packet in enumerate(selected):
        direction = direction_by_id[id(packet)]
        start, end = (0, 1) if direction == 0 else (1, 0)
        color = BLUE if direction == 0 else TEAL
        ladder.annotate('', (end, row + .2), (start, row + .2),
                        arrowprops={'arrowstyle': '->', 'lw': 1.2, 'color': color})
        label = (f"+{packet['time'] - origin:.4f}s  {packet.get('flags') or '—'}  "
                 f"SEQ {_header_value(packet.get('seq'))}  ACK {_header_value(packet.get('ack'))}")
        ladder.text(.5, row - .02, label, ha='center', fontsize=7.2, color=color)
    ladder.axis('off')
    skipped = f'가운데 {len(packets) - 10}개 패킷 생략 · ' if len(packets) > 10 else ''
    ladder.text(0, -.05, '화살표는 포착된 방향만 표시합니다. 상대 도착 여부는 알 수 없습니다.\n' + skipped + '행 간격은 일정하며 실제 시간 비율이 아닙니다.',
                transform=ladder.transAxes, fontsize=7.5, color=MUTED, linespacing=1.6)

    table_ax.axis('off')
    table_ax.set_title('선택 연결 · TCP 헤더 관측값', loc='left', pad=12)
    rows = [[f"{packet['time'] - origin:.4f}", packet.get('flags') or '—',
             _header_value(packet.get('seq')), _header_value(packet.get('ack')),
             _header_value(packet.get('window')), _header_value(packet.get('payload_length'))]
            for packet in selected]
    table = table_ax.table(cellText=rows, colLabels=['초', 'Flags', 'SEQ', 'ACK', 'Window', '길이'],
                           colWidths=[.14, .10, .23, .23, .16, .14],
                           cellLoc='center', bbox=[0, .34, 1, .63])
    table.auto_set_font_size(False)
    table.set_fontsize(6.5)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor('#dce3ec')
        cell.set_facecolor('#e6edf6' if row == 0 else 'white')
    has_syn = any('S' in p.get('flags', '') for p in packets)
    note = ('SEQ / ACK: 도구가 출력한 원본 번호 · —는 출력값 없음\n'
            'Window: 헤더의 16비트 원시값 · 스케일 미적용\n'
            '길이: TCP 페이로드 바이트 · 데이터 내용은 수집하지 않음\n'
            + ('SYN 관측 있음 · 전체 연결 수립 과정은 미판정' if has_syn else 'SYN 미관측 · 전체 연결 수립 과정은 확인 불가'))
    table_ax.text(0, .26, note, va='top', fontsize=8, color=MUTED, linespacing=1.7)


def plot_capture(result, flow_id, axes, view='flow'):
    packets = _flow_packets(result, flow_id)
    if not packets:
        error = (result or {}).get('error')
        message = ('TCP 패킷을 포착하지 못했습니다.\n' + str(error) if error else
                   '선택 연결의 TCP 패킷이 아직 없습니다.\n실시간 캡처를 시작하거나 캡처 파일을 여세요.\n실시간 캡처에는 인터페이스 접근 권한이 필요합니다.')
        _empty(axes, message)
        return
    if view == 'flow':
        _plot_packet_flow(result, packets, axes)
    elif view == 'headers':
        _plot_headers(result, packets, axes)
    else:
        raise ValueError("view는 'flow' 또는 'headers'여야 합니다.")


def capture_figure(result, flow_id=None, view='flow'):
    configure_style()
    fig = Figure(figsize=(17, 10.4), dpi=120)
    title = '실제 TCP 패킷 · 파형' if view == 'flow' else '실제 TCP 패킷 · 헤더'
    flows = dict(capture_flows(result))
    selected = flow_id if flow_id is not None else next(iter(flows), None)
    _figure_header(fig, title, flows.get(selected, 'TCP 메타데이터 관측 대기'),
                   capture_metrics(result, selected))
    plot_capture(result, selected, capture_axes(fig, view, right=.95), view)
    fig.text(.05, .035, '패킷이 보인 시점의 헤더 기록입니다. 전달 성공, 손실률, cwnd 또는 RTT를 추정해 채우지 않습니다.',
             fontsize=9, color=MUTED)
    return fig
