"""Matplotlib views for the course's simulated TCP events (not packet capture)."""
from __future__ import annotations

import math
from matplotlib import font_manager, rcParams
from matplotlib.figure import Figure

BG = '#f3f5f8'
INK = '#172b46'
MUTED = '#53677e'
BLUE = '#2865c7'
TEAL = '#008477'
ORANGE = '#bb5c13'
RED = '#c53443'


def configure_style():
    available = {f.name for f in font_manager.fontManager.ttflist}
    font = next((f for f in ['NanumGothic', 'Malgun Gothic', 'AppleGothic', 'Noto Sans CJK KR'] if f in available), 'DejaVu Sans')
    rcParams.update({'font.family': font, 'font.size': 10, 'axes.unicode_minus': False,
                     'figure.facecolor': BG, 'axes.facecolor': 'white', 'axes.edgecolor': '#cbd3df',
                     'axes.labelcolor': MUTED, 'text.color': INK, 'xtick.color': MUTED,
                     'ytick.color': MUTED, 'grid.color': '#e4e9f0', 'grid.linewidth': .7,
                     'savefig.facecolor': BG, 'axes.titlesize': 11, 'axes.titleweight': 'bold'})


def style_axis(ax, title, ylabel='', xlabel='시간 (s)'):
    ax.set_title(title, loc='left', pad=12)
    ax.set_xlabel(xlabel, fontsize=9)
    ax.set_ylabel(ylabel, fontsize=9)
    ax.grid(True, alpha=.9)
    ax.set_axisbelow(True)
    ax.spines[['top', 'right']].set_visible(False)
    ax.tick_params(labelsize=8)


def grid_axes(fig, area=(.055, .13, .73, .59), rows=2, cols=3):
    left, bottom, width, height = area
    gs = fig.add_gridspec(rows, cols, left=left, right=left+width,
                         bottom=bottom, top=bottom+height, hspace=.62, wspace=.4)
    return [fig.add_subplot(gs[r, c]) for r in range(rows) for c in range(cols)]


def plot_overview(result, axes):
    samples = result.samples
    times = [s['time'] for s in samples]
    ax = axes[0]
    rtt = result.config.rtt_ms / 1000
    rounds = [t / rtt for t in times]
    style_axis(ax, '01  혼잡 윈도우 · cwnd', '세그먼트 (MSS)', '경과 시간 / 설정 RTT (회)')
    ax.step(rounds, [s['cwnd'] for s in samples], where='post', color=BLUE, lw=1.7)
    phases = [('slow_start', BLUE, 'Slow Start'), ('congestion_avoidance', TEAL, '혼잡 회피'), ('fast_recovery', ORANGE, '빠른 복구')]
    for phase,color,label in phases:
        points = [s for s in samples if s['phase']==phase]
        if points:
            ax.scatter([s['time']/rtt for s in points],[s['cwnd'] for s in points],c=color,s=8,label=label,zorder=3)
            for a,b in zip(samples,samples[1:]):
                if a['phase']==phase:
                    ax.axvspan(a['time']/rtt,b['time']/rtt,color=color,alpha=.045,lw=0)
    ax.step(rounds, [s['ssthresh'] for s in samples], where='post', color=ORANGE, ls='--', label='ssthresh', lw=1)
    for e in result.events:
        if e['kind'] in ('timeout','fast_retransmit'):
            ax.axvline(e['time']/rtt,c=RED,lw=.8,ls=':')
            ax.annotate('RTO' if e['kind']=='timeout' else '3 dupACK',(e['time']/rtt,e['cwnd']),
                        xytext=(3,8),textcoords='offset points',fontsize=6,color=RED)
    ax.legend(fontsize=6.5, frameon=False, loc='upper right',ncols=2)
    ax.set_ylim(bottom=0)

    ax = axes[1]
    style_axis(ax, '02  시퀀스 번호 · 누적 ACK', '상대 바이트 번호')
    data = [p for p in result.packets if p['length'] > 0 and p['direction'] == 'C>S']
    ok = [p for p in data if not p['lost'] and not p['retransmission']]
    lost = [p for p in data if p['lost']]
    retry = [p for p in data if p['retransmission']]
    for packets, color, marker, label in [(ok, BLUE, '.', 'DATA'), (lost, RED, 'x', '손실'), (retry, ORANGE, '^', '재전송')]:
        if packets:
            ax.scatter([p['time'] for p in packets], [p['seq']-1000 for p in packets], s=18, c=color, marker=marker, label=label, zorder=3)
    acks = [p for p in result.packets if p['direction']=='S>C' and p['flags']=='ACK']
    ax.step([p['time'] for p in acks], [p['ack']-1000 for p in acks], where='post', c=TEAL, label='ACK', lw=1)
    ax.legend(fontsize=7, frameon=False, loc='upper left', ncols=2)
    ax.ticklabel_format(axis='y', style='sci', scilimits=(0, 4))

    ax = axes[2]
    style_axis(ax, '03  새로 전달된 데이터 속도', '유효 전송률 (Mbps)')
    duration = max(float(result.summary['duration']), .001)
    bin_width = max(.05, duration / 40)
    count = max(1, math.ceil(duration / bin_width))
    bins = [0] * count
    last_delivered = 0
    for s in samples:
        delta = max(0, s['delivered']-last_delivered)
        last_delivered = max(last_delivered, s['delivered'])
        index = min(count-1, max(0, int(s['time']/bin_width)))
        bins[index] += delta
    ax.bar([i*bin_width for i in range(count)], [n*8/bin_width/1e6 for n in bins],
           width=bin_width*.9, align='edge', color=TEAL, alpha=.8)
    ax.set_ylim(bottom=0)

    ax = axes[3]
    style_axis(ax, '04  왕복 지연 · RTT', '지연 (ms)')
    rtts = result.rtt_samples
    if rtts:
        ax.plot([s['time'] for s in rtts], [s['rtt_ms'] for s in rtts], '.', color='#8596b1', label='RTT 표본', ms=4)
        ax.plot([s['time'] for s in rtts], [s['srtt_ms'] for s in rtts], color=BLUE, label='SRTT', lw=1.7)
        ax.legend(fontsize=8, frameon=False)
    else:
        ax.text(.5, .5, '유효 RTT 표본 없음', transform=ax.transAxes, ha='center', color=MUTED)
    ax.set_ylim(bottom=0)

    ax = axes[4]
    style_axis(ax, '05  송신 중인 데이터 · 흐름 제어', '바이트')
    ax.step(times, [s['inflight'] for s in samples], where='post', c=BLUE, label='미확인 데이터')
    ax.step(times, [s['rwnd'] for s in samples], where='post', c=TEAL, ls='--', label='고정 rwnd')
    ax.legend(fontsize=8, frameon=False)
    ax.set_ylim(bottom=0)

    ax = axes[5]
    style_axis(ax, '06  손실과 재전송 시점', 'DATA 시도 번호')
    for i, p in enumerate(data, 1):
        c = RED if p['lost'] else ORANGE if p['retransmission'] else '#b5c6da'
        ax.scatter(p['time'], i, c=c, s=24 if p['lost'] or p['retransmission'] else 8,
                   marker='x' if p['lost'] else '^' if p['retransmission'] else '.')
    ax.text(.02, .98, f"타임아웃 {result.summary.get('timeout_count', 0)}회  /  빠른 재전송 {result.summary.get('fast_retransmits', 0)}회",
            transform=ax.transAxes, va='top', fontsize=8, color=MUTED)
    for ax in axes:
        ax.set_xlim(left=0)


def plot_connection(result, axes):
    ladder, table = axes
    ladder.set_title('연결 설정 → 데이터 → 연결 종료', loc='left', pad=15)
    control = [p for p in result.packets if 'SYN' in p['flags'] or 'FIN' in p['flags']]
    initial = result.packets[:3]
    interesting = [p for p in result.packets[3:] if p['length']>0 or p['retransmission']][:4]
    end = []
    fin_times = [p['time'] for p in control if 'FIN' in p['flags']]
    if fin_times:
        end = [p for p in result.packets if p['time'] >= min(fin_times)]
    selected = initial + interesting + end
    ladder.set_xlim(-.25, 1.25)
    ladder.set_ylim(len(selected)+1, -1.4)
    for x, label in [(0, '클라이언트'), (1, '서버')]:
        ladder.plot([x,x], [-.4,len(selected)], c='#9aaabe', lw=1.2, ls='--')
        ladder.text(x, -.85, label, ha='center', weight='bold')
    for i,p in enumerate(selected):
        start, finish = (0,1) if p['direction']=='C>S' else (1,0)
        c = RED if p['lost'] else ORANGE if p['retransmission'] else BLUE if start==0 else TEAL
        pending = not p['lost'] and not p.get('received', p.get('arrival') is not None)
        if p['lost'] or pending:
            finish = .5
        ladder.annotate('', (finish,i+.22), (start,i), arrowprops={'arrowstyle':'->','color':c,'lw':1.4,'linestyle':'--' if pending else '-'})
        label = f"{p['time']:.3f}s  {p['flags']}  seq={p['seq']}  ack={p['ack']}"
        if p['length']: label += f"  len={p['length']}"
        if p['lost']: label += '  × 손실'
        if pending: label += '  미도착'
        ladder.text(.5,i-.12,label,ha='center',va='bottom',fontsize=8,color=c)
    ladder.axis('off')
    table.axis('off')
    table.set_title('패킷 헤더 확인 · 앞 12개',loc='left',pad=15)
    rows=[]
    for p in result.packets[:12]:
        rows.append([f"{p['time']:.3f}",p['flags'],str(p['seq']),str(p['ack']),str(p['length']),str(p['window'])])
    tbl=table.table(cellText=rows,colLabels=['초','Flags','Seq','ACK','길이','rwnd'],loc='upper center',cellLoc='center',bbox=[0,.43,1,.54])
    tbl.auto_set_font_size(False); tbl.set_fontsize(8)
    for (r,c), cell in tbl.get_celld().items():
        cell.set_edgecolor('#dce3ec')
        cell.set_facecolor('#e6edf6' if r==0 else 'white')
    note=('SEQ: 이 세그먼트의 첫 바이트 번호\nACK: 다음에 받을 바이트 번호\nSYN과 FIN은 각각 번호 1개를 사용합니다.\n데이터 없는 ACK는 번호를 소비하지 않습니다.\n\n화살표의 행 간격은 실제 시간 비율이 아닙니다.\n전체 기록은 CSV로 저장할 수 있습니다.\nTIME_WAIT의 실제 대기 시간은 생략합니다.')
    table.text(0,.35,note,va='top',fontsize=10,linespacing=1.65,color=MUTED)


def plot_rdt(results, axes):
    for result, ax in zip(results, axes[:3]):
        style_axis(ax, result['name'], '패킷 번호')
        for e in result['events']:
            if e['kind']!='DATA': continue
            c=RED if e['lost'] else ORANGE if e['retransmission'] else BLUE
            ax.scatter(e['time'],e['packet'],c=c,s=38,marker='x' if e['lost'] else '^' if e['retransmission'] else 'o')
        ax.set_yticks(list(range(1,13,2)))
        ax.set_ylim(.5,12.5); ax.set_xlim(left=0)
    labels = [r['name'] for r in results]
    for ax,key,title,ylabel in [(axes[3],'retransmissions','같은 손실에서 재전송 비교','패킷 수'),
                               (axes[4],'peak_buffer_packets','순서 밖 수신 버퍼의 최대 사용량','패킷 수'),
                               (axes[5],'duration','전송 완료까지 걸린 시간','초')]:
        style_axis(ax,title,ylabel,xlabel='')
        vals=[r[key] for r in results]
        bars=ax.bar(labels,vals,color=[BLUE,ORANGE,TEAL],width=.55)
        ax.bar_label(bars,labels=[f'{v:.3f}' if key=='duration' else str(v) for v in vals],padding=4,fontsize=9)
        ax.set_ylim(0,max(vals+[1])*1.3)
        ax.grid(axis='x',visible=False)


def summary_text(result):
    s=result.summary
    return [('전달된 데이터',f"{s['delivered_bytes']/1000:.1f} / {s['total_bytes']/1000:.1f} kB"),
            ('총 경과 시간',f"{s['duration']:.3f} s"),
            ('평균 유효 전송률',f"{s['goodput_mbps']:.3f} Mbps"),
            ('DATA 재전송',f"{s['retransmissions']}회")]


def rdt_summary_text(results):
    by_name={r['name']:r for r in results}
    return [('같은 손실 조건','3번 DATA 1회 손실'),('GBN 재전송',f"{by_name['GBN']['retransmissions']}개"),
            ('SR 재전송',f"{by_name['SR']['retransmissions']}개"),
            ('SR 순서 밖 버퍼',f"최대 {by_name['SR']['peak_buffer_packets']}개")]


def report_figure(result, view='overview', rdt_results=None):
    configure_style()
    fig=Figure(figsize=(16,10),dpi=120)
    fig.text(.055,.95,'TCP NETWORK LAB',fontsize=24,weight='bold')
    fig.text(.055,.91,'5~7주차 네트워크 학습 실험 · Python 시뮬레이션',fontsize=12,color=MUTED)
    metrics=rdt_summary_text(rdt_results) if view=='rdt' else summary_text(result)
    for i,(label,value) in enumerate(metrics):
        x=.055+i*.24
        fig.text(x,.855,label,fontsize=10,color=MUTED)
        fig.text(x,.817,value,fontsize=19,weight='bold',color=TEAL if i==2 else INK)
    if view=='connection':
        gs=fig.add_gridspec(1,2,left=.055,right=.96,bottom=.13,top=.72,wspace=.23,width_ratios=[1.3,1])
        plot_connection(result,[fig.add_subplot(gs[0,0]),fig.add_subplot(gs[0,1])])
    else:
        axes=grid_axes(fig,area=(.055,.16,.90,.56))
        plot_rdt(rdt_results,axes) if view=='rdt' else plot_overview(result,axes)
    c=result.config
    state='모든 데이터 확인 완료' if result.summary['completed'] else '시간 제한: 전달·확인 미완료'
    subtitle=f'{state}  |  RTT {c.rtt_ms:g} ms · 링크 {c.bandwidth_mbps:g} Mbps · DATA 손실 {c.loss_pct:g}% · rwnd {c.receiver_window} B · seed {c.seed}'
    if view=='rdt': subtitle='독립 비교 실험: 12개 패킷 · window=4 · 3번 DATA의 첫 전송만 손실 · ACK 손실 없음 · timeout=1s'
    fig.text(.055,.078,subtitle,fontsize=10,color=MUTED)
    fig.text(.055,.04,'모의 패킷의 시간 변화입니다. 실제 네트워크 캡처나 전기적 신호 파형이 아닙니다.',fontsize=10,color=MUTED)
    return fig
