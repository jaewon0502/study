"""Charts for explicit DNS/TCP probes, separate from the educational simulator."""
from collections import Counter
import textwrap

from matplotlib.figure import Figure
from plots import configure_style, style_axis, BLUE, TEAL, RED, MUTED, INK


STATUS_LABELS = {
    'ok': '성공', 'timeout': '시간 초과', 'refused': '연결 거부',
    'unreachable': '경로 없음', 'error': '기타 오류',
}


def milliseconds(value):
    return '측정 없음' if value is None else f'{value:.1f} ms'


def diagnostic_metrics(result):
    summary = result['summary']
    dns = result['dns']
    dns_value = ('결과 대기' if dns['status'] == 'pending' else 'IP 직접 입력' if dns['status'] == 'skipped' else
                 milliseconds(dns.get('duration_ms')) if dns['status'] == 'ok' else '조회 실패')
    success = summary.get('success_pct')
    return [
        ('DNS 이름 조회', dns_value),
        ('TCP 연결 성공률', '시도 없음' if success is None else f'{success:.0f}%'),
        ('평균 연결 지연 · 성공 표본', milliseconds(summary.get('mean_ms'))),
        ('연결 실패 / 시도', f"{summary['failed']} / {summary['attempted']}회"),
    ]


def diagnostic_axes(fig, *, right=.79, top=.70, bottom=.14):
    gs = fig.add_gridspec(2, 2, left=.055, right=right, bottom=bottom,
                         top=top, hspace=.65, wspace=.32, width_ratios=[1.3, 1])
    return [fig.add_subplot(gs[row, col]) for row in range(2) for col in range(2)]


def plot_diagnostics(result, axes):
    probes = result['probes']
    ax = axes[0]
    style_axis(ax, '01  TCP 연결 지연', '연결 시간 (ms)', '진단 회차')
    # NaN breaks the line at failures; a failure is never plotted as zero latency.
    ax.plot([p['attempt'] for p in probes],
            [p['duration_ms'] if p['status'] == 'ok' else float('nan') for p in probes],
            color=BLUE, marker='o', ms=5, lw=1.5, label='성공한 연결')
    failed = [p['attempt'] for p in probes if p['status'] != 'ok']
    if failed:
        ax.scatter(failed, [.03] * len(failed), transform=ax.get_xaxis_transform(),
                   marker='x', color=RED, s=42, label='실패 (지연값 없음)', zorder=4)
    if not any(p['status'] == 'ok' for p in probes):
        ax.text(.5, .55, '성공한 TCP 연결 표본이 없습니다.', ha='center',
                transform=ax.transAxes, color=MUTED, fontsize=9)
    ax.set_xlim(.5, max(1, result['config']['attempts']) + .5)
    ax.set_xticks(range(1, result['config']['attempts'] + 1))
    ax.set_ylim(bottom=0)
    ax.legend(fontsize=8, frameon=False, loc='upper right')

    ax = axes[1]
    style_axis(ax, '02  연결 결과', '회', '')
    counts = Counter(p['status'] for p in probes)
    keys = ['ok'] + [s for s in STATUS_LABELS if s != 'ok' and counts[s]]
    bars = ax.bar([STATUS_LABELS[k] for k in keys], [counts[k] for k in keys],
                  color=[TEAL if k == 'ok' else RED for k in keys], width=.55)
    for bar in bars:
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + .08,
                str(int(bar.get_height())), ha='center', fontsize=10)
    ax.set_ylim(0, max(1, max(counts.values(), default=0)) + 1)
    ax.yaxis.get_major_locator().set_params(integer=True)

    ax = axes[2]
    ax.axis('off')
    ax.set_title('03  대상과 이름 조회', loc='left', pad=12)
    host = result['config']['host']
    if len(host) > 46:
        host = host[:43] + '…'
    dns = result['dns']
    state = {'ok': '조회 성공', 'error': '조회 실패', 'timeout': '조회 시간 초과', 'pending': '진단 완료 후 표시',
             'skipped': '숫자 IP · DNS 생략'}.get(dns['status'], dns['status'])
    lines = [f"대상  {host}:{result['config']['port']}", f'DNS  {state}']
    for address in dns.get('addresses', [])[:4]:
        lines.append(f"{address['family']}  {address['address'][:48]}")
    more = len(dns.get('addresses', [])) - 4
    if more > 0:
        lines.append(f'외 {more}개 · 전체 주소는 JSON에서 확인')
    if dns.get('error'):
        lines.extend(textwrap.wrap(dns['error'], width=52)[:2])
    ax.text(0, .96, '\n'.join(lines), va='top', fontsize=9, linespacing=1.65)

    ax = axes[3]
    ax.axis('off')
    ax.set_title('04  결과 해석', loc='left', pad=12)
    findings = result.get('findings', [])
    lines = []
    if result.get('cancelled'):
        lines.append('중단됨 · 완료된 시도만 집계합니다.')
    for finding in findings[:3]:
        lines.extend(textwrap.wrap(f'• {finding}', width=31))
    ax.text(0, .96, '\n'.join(lines[:9]), va='top', fontsize=9, linespacing=1.5, color=INK)


def diagnostic_figure(result):
    configure_style()
    fig = Figure(figsize=(16, 10), dpi=120)
    fig.text(.05, .955, 'NETWORK DIAGNOSTICS', fontsize=24, weight='bold')
    fig.text(.05, .916, f"DNS · TCP 실제 연결 진단  /  {result['started_at']}", fontsize=11, color=MUTED)
    for i, (label, value) in enumerate(diagnostic_metrics(result)):
        x = .05 + i * .24
        fig.text(x, .86, label, fontsize=10, color=MUTED)
        fig.text(x, .82, value, fontsize=21, weight='bold', color=TEAL if i == 1 else INK)
    plot_diagnostics(result, diagnostic_axes(fig, right=.95, top=.72, bottom=.14))
    fig.text(.05, .05, 'TCP 연결 지연은 ICMP RTT와 다릅니다. 연결 실패 비율은 패킷 손실률이 아닙니다.', fontsize=10, color=MUTED)
    fig.text(.05, .025, '대상 포트의 연결만 검사합니다. HTTP 응답·TLS 인증서·인터넷 최대 속도는 검사하지 않습니다.', fontsize=9, color=MUTED)
    return fig
