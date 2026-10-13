"""Read-only operating-system observations, kept separate from TCP simulation."""
import math
import time

from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator
from plots import configure_style, style_axis, BLUE, TEAL, INK, MUTED


def pick_interface(snapshot, current=None):
    interfaces = snapshot.get('interfaces', [])
    if any(i['name'] == current for i in interfaces):
        return current
    active = [i for i in interfaces if i['is_up'] and i['addresses']
              and not i['name'].startswith(('lo', 'utun', 'awdl', 'llw', 'bridge'))]
    if not active:
        active = [i for i in interfaces if i['is_up']] or interfaces
    return max(active, key=lambda i: i['rx_bytes'] + i['tx_bytes'])['name'] if active else None


def selected(snapshot, name):
    return next((i for i in snapshot.get('interfaces', []) if i['name'] == name), {})


def number(value, unit='%'):
    return '측정 대기' if value is None else f'{value:.1f}{unit}'


def _tcp_available(snapshot):
    return snapshot.get('connection_source') not in ('unavailable', None, '')


def live_metrics(snapshot, interface):
    nic = selected(snapshot, interface)
    return [('CPU 사용률', number(snapshot.get('cpu_percent'))),
            ('메모리 사용률', number(snapshot.get('memory_percent'))),
            (f'수신 속도 · {interface or "미확인"}', number(nic.get('rx_mbps'), ' Mbps')),
            ('TCP ESTABLISHED · 조회',
             str(snapshot.get('connection_states', {}).get('ESTABLISHED', 0)) + '개'
             if _tcp_available(snapshot) else '확인 불가')]


def live_axes(fig, right=.79, top=.70, bottom=.14):
    """Three time series above two wide tables, shared by GUI and PNG export."""
    grid = fig.add_gridspec(2, 6, left=.055, right=right, top=top, bottom=bottom,
                           hspace=.49, wspace=1.05)
    return [fig.add_subplot(grid[0, start:start + 2]) for start in (0, 2, 4)] + [
        fig.add_subplot(grid[1, :3]), fig.add_subplot(grid[1, 3:])]


def _observed(value):
    # NaN breaks a line at missing observations instead of inventing a zero or
    # drawing a continuous trace across an unobserved interval.
    return value if value is not None and math.isfinite(value) else math.nan


def _timeline(ax, title, ylabel, times):
    style_axis(ax, title, ylabel, '최근 관측 시점 기준 (초)')
    ax.set_xlim(min(-5, times[0]), 1)
    values = [value for line in ax.lines for value in line.get_ydata() if math.isfinite(value)]
    peak = max(values, default=0)
    ax.set_ylim(0, peak * 1.3 if peak > 0 else 1)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
    ax.legend(fontsize=7.5, frameon=True, facecolor='white', edgecolor='none',
              framealpha=.9, loc='upper left')


def _table(ax, rows, labels, widths, bbox, font_size=8):
    table = ax.table(cellText=rows, colLabels=labels, colWidths=widths,
                     bbox=bbox, cellLoc='left')
    table.auto_set_font_size(False)
    table.set_fontsize(font_size)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor('#dce3ec')
        cell.set_facecolor('#e6edf6' if row == 0 else 'white')
    return table


def plot_live(history, interface, axes):
    if not history:
        for ax in axes:
            ax.axis('off')
        axes[0].text(0, .8, '첫 관측값을 기다립니다.', color=MUTED, fontsize=10)
        return

    last = history[-1]
    times = [snapshot['monotonic'] - last['monotonic'] for snapshot in history]
    ax = axes[0]
    for key, color, label, line, marker in [
            ('rx_mbps', TEAL, '수신 RX', '-', 'o'),
            ('tx_mbps', BLUE, '송신 TX', '--', 's')]:
        ax.plot(times, [_observed(selected(s, interface).get(key)) for s in history],
                color=color, label=label, ls=line, lw=1.6, marker=marker, ms=2.5)
    _timeline(ax, f'네트워크 송수신 · {interface or "선택 없음"}', 'Mbps', times)
    if not any(math.isfinite(v) for line in ax.lines for v in line.get_ydata()):
        ax.text(.5, .42, '속도 표본 대기\n선택한 인터페이스를 연속 관측합니다.',
                ha='center', transform=ax.transAxes, fontsize=8, color=MUTED)

    ax = axes[1]
    for key, color, label, line in [('cpu_percent', BLUE, 'CPU', '-'),
                                    ('memory_percent', TEAL, '메모리', '--')]:
        ax.plot(times, [_observed(s.get(key)) for s in history], color=color,
                label=label, ls=line, lw=1.6, marker='.', ms=3)
    _timeline(ax, 'CPU · 메모리 사용률', '%', times)
    ax.set_ylim(0, 100)
    if not any(math.isfinite(v) for line in ax.lines for v in line.get_ydata()):
        ax.text(.5, .42, '사용률 관측값 없음', ha='center', transform=ax.transAxes,
                fontsize=8, color=MUTED)

    ax = axes[2]
    for state, color, label, line, marker in [
            ('ESTABLISHED', TEAL, '연결됨 ESTABLISHED', '-', 'o'),
            ('LISTEN', BLUE, '수신 대기 LISTEN', '--', 's')]:
        values = [s.get('connection_states', {}).get(state, 0)
                  if _tcp_available(s) else None for s in history]
        ax.step(times, [_observed(v) for v in values], where='post', color=color,
                label=label, ls=line, lw=1.6, marker=marker, ms=2.5)
    _timeline(ax, '조회 가능한 TCP 연결 수', '개', times)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=5, integer=True))
    if not _tcp_available(last):
        ax.text(.5, .42, 'TCP 조회 불가\n누락 구간은 선을 연결하지 않습니다.',
                ha='center', transform=ax.transAxes, fontsize=8, color=MUTED)

    ax = axes[3]
    ax.axis('off')
    ax.set_title('인터페이스 · IP 주소', loc='left', pad=12)
    interfaces = sorted(last.get('interfaces', []),
                        key=lambda i: (i['name'] != interface, not i['is_up'], i['name']))[:7]
    rows = []
    for nic in interfaces:
        addresses = nic['addresses']
        address = next((a for a in addresses if ':' not in a),
                       addresses[0] if addresses else '주소 없음')
        if len(address) > 27:
            address = address[:24] + '…'
        rows.append([('▶ ' if nic['name'] == interface else '') + nic['name'],
                     'UP' if nic['is_up'] else 'DOWN', address])
    if rows:
        _table(ax, rows, ['인터페이스', '상태', 'IP'], [.24, .18, .58], [0, .12, 1, .86])
    else:
        ax.text(0, .6, '조회 가능한 인터페이스가 없습니다.', fontsize=9, color=MUTED)
    age = max(0, time.monotonic() - last['monotonic'])
    ax.text(0, -.04, f'마지막 관측 {age:.1f}초 전 · 표본 {len(history)}개', fontsize=8, color=MUTED)
    ax.text(0, -.16, '선택한 인터페이스의 모든 프로토콜 트래픽을 표시합니다.',
            fontsize=8, color=MUTED)

    ax = axes[4]
    ax.axis('off')
    counts = last.get('connection_states', {})
    source = last.get('connection_source') or 'unavailable'
    states = ' · '.join(f'{state} ' + (
        '확인 불가' if not _tcp_available(last) or (source.startswith('lsof') and state == 'TIME_WAIT')
        else str(counts.get(state, 0))) for state in ['ESTABLISHED', 'LISTEN', 'TIME_WAIT'])
    ax.set_title('현재 TCP 연결 · 최대 6개 표시', loc='left', pad=12)
    ax.text(0, .95, states, fontsize=8, color=INK, weight='bold')
    connections = sorted(last.get('connections', []),
                         key=lambda c: (c['state'] != 'ESTABLISHED', c['state'] != 'LISTEN'))[:6]
    if connections:
        rows = [[c[key] if len(c[key]) < 24 else c[key][:21] + '…'
                 for key in ('local', 'remote')] + [c['state']] for c in connections]
        _table(ax, rows, ['로컬 주소·포트', '상대 주소·포트', '상태'],
               [.35, .40, .25], [0, .15, 1, .73], 7.2)
    else:
        message = '조회 결과 TCP 연결이 없습니다.' if _tcp_available(last) else 'TCP 연결 목록을 조회할 수 없습니다.'
        ax.text(0, .6, message, fontsize=9, color=MUTED)
    ax.text(0, -.04, f'출처: {source if _tcp_available(last) else "확인 불가"}',
            fontsize=8, color=MUTED)
    caveat = ('현재 계정에서 조회한 항목 · TIME_WAIT는 확인 불가'
              if source.startswith('lsof') else '조회 권한에 따라 전체 연결 중 일부만 표시될 수 있습니다.')
    ax.text(0, -.16, caveat, fontsize=8, color=MUTED)


def live_figure(history, interface):
    configure_style()
    fig = Figure(figsize=(17, 10.4), dpi=120)
    fig.text(.05, .955, 'MY COMPUTER · 실제 상태', fontsize=24, weight='bold')
    observed = history[-1]['timestamp'] if history else '대기 중'
    fig.text(.05, .918, f'관측 시각 {observed} · 운영체제에서 조회한 값', fontsize=11, color=MUTED)
    if history:
        for i, (label, value) in enumerate(live_metrics(history[-1], interface)):
            x = .05 + i * .24
            fig.text(x, .86, label, fontsize=10, color=MUTED)
            fig.text(x, .82, value, fontsize=20, weight='bold', color=TEAL if i == 2 else INK)
    plot_live(history, interface, live_axes(fig, right=.95))
    fig.text(.05, .04, 'NIC 송수신 속도는 인터넷 최대 속도나 TCP 전용 처리율이 아닙니다. 누락된 관측값은 빈 구간으로 표시합니다.',
             fontsize=9, color=MUTED)
    return fig
