#!/usr/bin/env python3
"""Observe actual device/network data; teaching simulations are opt-in."""
from __future__ import annotations

import argparse
import csv
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
from queue import Empty, Queue
import sys
from threading import Event
import time

from tcp_sim import Config, simulate
from rdt_sim import compare_rdt


def write_csv(path, rows):
    rows=list(rows)
    if not rows:
        path.write_text('',encoding='utf-8-sig')
        return
    fields=list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w',newline='',encoding='utf-8-sig') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def export_results(result, destination):
    """Export actual simulator results; never fabricate a capture trace."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from plots import report_figure
    destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=True)
    rdt=compare_rdt(rtt_ms=result.config.rtt_ms)
    for name,view in [('01_tcp_graphs.png','overview'),('02_connection.png','connection'),('03_gbn_sr.png','rdt')]:
        fig=report_figure(result,view,rdt)
        FigureCanvasAgg(fig)
        fig.savefig(destination/name,dpi=140)
        fig.clear()
    for name,rows in [('packets.csv',result.packets),('samples.csv',result.samples),
                      ('rtt.csv',result.rtt_samples),('events.csv',result.events)]:
        write_csv(destination/name,rows)
    write_csv(destination/'rdt_comparison.csv',({k:v for k,v in r.items() if k!='events'} for r in rdt))
    for r in rdt:
        write_csv(destination/f"rdt_{r['name'].lower().replace('-', '_')}.csv",r['events'])
    payload={'source':'educational_simulation_not_packet_capture','config':asdict(result.config),
             'summary':result.summary,'model_notes':getattr(result,'model_notes',[]),
             'rdt_summary':[{k:v for k,v in r.items() if k!='events'} for r in rdt]}
    (destination/'summary.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    return destination.resolve()


def export_diagnostics(result, destination):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from diagnostic_plots import diagnostic_figure
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    fig = diagnostic_figure(result)
    FigureCanvasAgg(fig)
    fig.savefig(destination / 'diagnostics.png', dpi=140)
    fig.clear()
    (destination / 'diagnostics.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    write_csv(destination / 'probes.csv',
              ({k: v for k, v in p.items() if k != 'address_attempts'} for p in result['probes']))
    return destination.resolve()


def export_observed(result, destination, kind='socket', flow_id=None):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from observed_plots import socket_figure, capture_figure
    destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=True)
    figures=[('tcp_socket.png',socket_figure(result))] if kind=='socket' else [
        ('tcp_capture_flow.png',capture_figure(result,flow_id,view='flow')),
        ('tcp_capture_packets.png',capture_figure(result,flow_id,view='headers'))]
    for name,fig in figures:
        FigureCanvasAgg(fig);fig.savefig(destination/name,dpi=140);fig.clear()
    name='tcp_socket' if kind=='socket' else 'tcp_capture'
    (destination/f'{name}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    write_csv(destination/('tcp_samples.csv' if kind=='socket' else 'tcp_packets.csv'),
              result.get('samples' if kind=='socket' else 'packets',[]))
    return destination.resolve()


class Lab:
    def __init__(self, config, start_view='live'):
        import matplotlib.pyplot as plt
        from matplotlib.widgets import Button, Slider, TextBox
        from plots import configure_style, BG, INK, MUTED, BLUE, TEAL
        configure_style()
        self.plt=plt
        self.config=config
        self.result=None
        self.learning_mode=start_view in ('overview','connection','rdt')
        self.view='overview'
        self.chart_axes=[]
        self.metric_text=[]
        self.live_history=deque(maxlen=60)
        self.live_interface=None
        self.live_future=None
        self.live_enabled=False
        self.live_paused=False
        self.live_generation=0
        self.live_future_generation=0
        self.live_reset=True
        self.live_next_read=0
        self.monitor=None
        self.closed=False
        self.diagnostic_future=None
        self.diagnostic_result=None
        self.diagnostic_queue=Queue()
        self.diagnostic_cancel=Event()
        self.diagnostic_error=None
        self.socket_future=None
        self.socket_result=None
        self.socket_queue=Queue()
        self.socket_cancel=Event()
        self.capture_future=None
        self.capture_result=None
        self.capture_queue=Queue()
        self.capture_cancel=Event()
        self.capture_flow=None
        self.observed_next_draw=0
        self.observed_dirty=False
        self.executor=ThreadPoolExecutor(max_workers=4,thread_name_prefix='network-observer')
        self.sim_controls=[]
        self.fig=plt.figure(figsize=(17,10.4))
        self.fig.canvas.manager.set_window_title('Network Diagnostics · 실시간 네트워크 진단')
        self.fig.text(.045,.952,'NETWORK DIAGNOSTICS',fontsize=24,weight='bold')
        self.subtitle=self.fig.text(.045,.916,'',fontsize=12,color=MUTED)
        self.badge=self.fig.text(.83,.952,'',fontsize=10,color=TEAL,weight='bold')
        self.status=self.fig.text(.045,.043,'',fontsize=10,color=MUTED)
        self.footer=self.fig.text(.045,.019,'',fontsize=9,color=MUTED)
        self.buttons=[]
        self.tab_buttons={}
        tabs=[('실시간 상태','live'),('대상 연결 진단','diagnostic')]
        tabs += [('TCP 파형 · 모의','overview'),('연결 · 모의','connection'),('GBN · SR · 모의','rdt')] if self.learning_mode else [
            ('TCP 내부 실측','socket'),('패킷 흐름','capture'),('패킷 헤더','packet')]
        for i,(label,view) in enumerate(tabs):
            x=.045+.152*i
            ax=self.fig.add_axes([x,.755,.139,.045])
            b=Button(ax,label,color='white',hovercolor='#e5edf8')
            b.on_clicked(lambda event,v=view:self.select(v))
            self.buttons.append(b)
            self.tab_buttons[view]=b
        self.settings_title=self.fig.text(.835,.79,'TCP 실험 설정',fontsize=12,weight='bold')
        self.sim_controls.append(self.fig.text(.835,.756,'값을 바꾸고 [다시 실행]을 누르세요.',fontsize=8,color=MUTED))
        self.sliders={}
        definitions=[('rtt_ms','왕복 전파 지연 (ms)',10,500,config.rtt_ms,10),
                     ('bandwidth_mbps','링크 속도 (Mbps)',.2,10,config.bandwidth_mbps,.2),
                     ('loss_pct','DATA 손실 확률 (%)',0,20,config.loss_pct,1),
                     ('receiver_kb','수신 윈도우 (kB)',1,64,config.receiver_window/1000,1),
                     ('total_kb','전송할 데이터 (kB)',8,256,config.total_bytes/1000,8),
                     ('seed','난수 시드',1,100,config.seed,1)]
        for i,(key,label,lo,hi,val,step) in enumerate(definitions):
            y=.687-i*.069
            self.sim_controls.append(self.fig.text(.835,y+.035,label,fontsize=9,color=MUTED))
            ax=self.fig.add_axes([.84,y,.105,.017],facecolor=BG)
            self.sim_controls.append(ax)
            slider=Slider(ax,'',lo,hi,valinit=val,valstep=step,color=BLUE)
            slider.valtext.set_fontsize(9)
            slider.on_changed(self.changed)
            self.sliders[key]=slider
        for x,y,w,label,callback in [(.835,.285,.13,'다시 실행',self.run),(.835,.222,.06,'손실 0%',self.normal),
                                    (.905,.222,.06,'손실 8%',self.lossy),(.835,.145,.13,'PNG · CSV 저장',self.save)]:
            b=Button(self.fig.add_axes([x,y,w,.045]),label,color='#e0ebf8',hovercolor='#cddff5')
            b.on_clicked(callback); self.buttons.append(b)
            if callback==self.save: self.save_button=b
            else: self.sim_controls.append(b.ax)
        self.help_text=self.fig.text(.835,.11,'',fontsize=8,color=MUTED,va='top',linespacing=1.7)
        self.live_info=self.fig.text(.835,.72,'',fontsize=10,color=MUTED,va='top',linespacing=1.8)
        cycle=Button(self.fig.add_axes([.835,.285,.13,.045]),'다음 인터페이스',color='#e0ebf8',hovercolor='#cddff5')
        cycle.on_clicked(self.cycle_interface);self.buttons.append(cycle)
        pause=Button(self.fig.add_axes([.835,.222,.13,.045]),'수집 중지',color='#e0ebf8',hovercolor='#cddff5')
        pause.on_clicked(self.toggle_live);self.buttons.append(pause)
        self.pause_button=pause
        self.live_controls=[self.live_info,cycle.ax,pause.ax]
        self.diagnostic_controls=[]
        self.diagnostic_inputs={}
        for i,(key,label,value) in enumerate([('host','호스트 이름 또는 IP','example.com'),
                                             ('port','TCP 포트','443'),('attempts','진단 횟수 · 1~20','5'),
                                             ('timeout','회당 제한 시간 · 초','2')]):
            y=.68-i*.088
            self.diagnostic_controls.append(self.fig.text(.835,y+.044,label,fontsize=9,color=MUTED))
            field=TextBox(self.fig.add_axes([.835,y,.13,.035]),'',initial=value,color='white')
            field.text_disp.set_fontsize(10)
            self.diagnostic_inputs[key]=field
            self.diagnostic_controls.append(field.ax)
        run=Button(self.fig.add_axes([.835,.285,.13,.045]),'진단 시작',color='#e0ebf8',hovercolor='#cddff5')
        run.on_clicked(self.start_diagnostic);self.buttons.append(run)
        cancel=Button(self.fig.add_axes([.835,.222,.13,.045]),'진단 중단',color='#e0ebf8',hovercolor='#cddff5')
        cancel.on_clicked(self.cancel_diagnostic);self.buttons.append(cancel)
        self.diagnostic_run_button=run
        self.diagnostic_cancel_button=cancel
        self.diagnostic_controls.extend([run.ax,cancel.ax])
        self.socket_controls=[]
        self.socket_inputs={}
        self.capture_controls=[]
        self.capture_inputs={}
        for group,definitions in [('socket',[('duration','측정 시간 · 1~30초','10',.68),
                                              ('chunk_kb','회당 데이터 · 1~64 KiB','16',.58)]),
                                  ('capture',[('interface','인터페이스','en0',.68),
                                               ('duration','수집 시간 · 1~120초','30',.585),
                                               ('path','PCAP 또는 실시간 JSONL 파일','captures/live_tcp.jsonl',.49)])]:
            controls=getattr(self,group+'_controls');inputs=getattr(self,group+'_inputs')
            for key,label,value,y in definitions:
                controls.append(self.fig.text(.835,y+.044,label,fontsize=9,color=MUTED))
                field=TextBox(self.fig.add_axes([.835,y,.13,.035]),'',initial=value,color='white')
                field.text_disp.set_fontsize(9)
                field.text_disp.set_clip_on(True)
                inputs[key]=field;controls.append(field.ax)
        self.socket_controls.append(self.fig.text(.835,.50,'127.0.0.1 실제 TCP 연결\n\n이 컴퓨터 안에서 데이터를\n전송하며 OS 값을 읽습니다.\n\n외부 서버로 전송하지 않습니다.',fontsize=9,color=MUTED,va='top',linespacing=1.6))
        for group,label,start,cancel in [('socket','측정',self.start_socket,self.cancel_socket),
                                         ('capture','수집',self.start_capture,self.cancel_capture)]:
            for suffix,y,text,callback in [('run',.285,label+' 시작',start),('cancel',.222,label+' 중단',cancel)]:
                b=Button(self.fig.add_axes([.835,y,.13,.045]),text,color='#e0ebf8',hovercolor='#cddff5')
                b.on_clicked(callback);self.buttons.append(b)
                getattr(self,group+'_controls').append(b.ax)
                setattr(self,group+'_'+suffix+'_button',b)
        for attr,y,label,callback in [('capture_file_button',.407,'파일 읽기 / 따라가기',self.start_capture_file),
                                     ('capture_flow_button',.346,'다음 TCP 연결',self.cycle_flow)]:
            b=Button(self.fig.add_axes([.835,y,.13,.040]),label,color='#e0ebf8',hovercolor='#cddff5')
            b.on_clicked(callback);self.buttons.append(b);self.capture_controls.append(b.ax);setattr(self,attr,b)
        self.timer=self.fig.canvas.new_timer(interval=200)
        self.timer.add_callback(self.live_tick)
        self.fig.canvas.mpl_connect('close_event',self.close)
        self.select(start_view)
        self.timer.start()

    def changed(self,value):
        self.status.set_text('설정이 변경되었습니다. [다시 실행]을 누르면 그래프에 반영됩니다.')
        self.fig.canvas.draw_idle()

    def select(self,view):
        self.view=view
        if view=='live':
            self.live_enabled=True
            self.live_tick()
        self.draw()

    def close(self,event=None):
        self.closed=True
        self.diagnostic_cancel.set()
        self.socket_cancel.set()
        self.capture_cancel.set()
        self.timer.stop()
        self.executor.shutdown(wait=False,cancel_futures=True)

    def read_system(self, reset=False):
        if self.monitor is None or reset:
            from system_monitor import SystemMonitor
            self.monitor=SystemMonitor()
        return self.monitor.sample()

    def live_tick(self):
        if self.closed: return False
        if self.live_future is not None and self.live_future.done():
            try:
                snapshot=self.live_future.result()
                from live_plots import pick_interface
                if not self.live_paused and self.live_future_generation==self.live_generation:
                    self.live_history.append(snapshot)
                    self.live_interface=pick_interface(snapshot,self.live_interface)
                    if self.view=='live': self.draw_live()
            except Exception as error:
                if self.view=='live':
                    self.status.set_text(f'컴퓨터 상태를 읽지 못했습니다: {error}')
                    self.fig.canvas.draw_idle()
            self.live_future=None
        if self.live_enabled and not self.live_paused and self.live_future is None and time.monotonic()>=self.live_next_read:
            self.live_future_generation=self.live_generation
            self.live_future=self.executor.submit(self.read_system,self.live_reset)
            self.live_reset=False
            self.live_next_read=time.monotonic()+1
        self.diagnostic_tick()
        self.observed_tick()
        return True

    def toggle_live(self,event=None):
        self.live_paused=not self.live_paused
        self.live_generation+=1
        self.pause_button.label.set_text('수집 재개' if self.live_paused else '수집 중지')
        if not self.live_paused:
            self.live_history.clear()
            self.live_reset=True
            self.live_next_read=0
        self.draw_live()

    def start_diagnostic(self,event=None):
        if self.diagnostic_future is not None: return
        from diagnostics import DiagnosticConfig, run_diagnostics, summarize_probes
        try:
            config=DiagnosticConfig(host=self.diagnostic_inputs['host'].text,
                                    port=int(self.diagnostic_inputs['port'].text),
                                    attempts=int(self.diagnostic_inputs['attempts'].text),
                                    timeout=float(self.diagnostic_inputs['timeout'].text))
        except ValueError as error:
            self.diagnostic_error=f'입력값을 확인하세요: {error}'
            self.status.set_text(self.diagnostic_error)
            self.fig.canvas.draw_idle();return
        self.diagnostic_cancel=Event()
        self.diagnostic_queue=Queue()
        self.diagnostic_error=None
        self.diagnostic_result={'source':'active_tcp_connection_probe','started_at':datetime.now().isoformat(timespec='seconds'),
                                'config':asdict(config),'dns':{'status':'pending','duration_ms':None,'addresses':[],'error':None},
                                'probes':[],'summary':summarize_probes([]),'cancelled':False,
                                'findings':['진단 중입니다. 연결 시도가 끝날 때마다 그래프를 갱신합니다.'], 'limitations':[]}
        self.diagnostic_future=self.executor.submit(run_diagnostics,config,self.diagnostic_cancel,self.diagnostic_queue.put)
        for field in self.diagnostic_inputs.values(): field.set_active(False)
        self.diagnostic_run_button.set_active(False)
        self.draw_diagnostic()

    def cancel_diagnostic(self,event=None):
        if self.diagnostic_future is not None:
            self.diagnostic_cancel.set()
            self.status.set_text('진단을 중단하고 완료된 시도까지 정리합니다…')
            self.fig.canvas.draw_idle()

    def diagnostic_tick(self):
        if self.diagnostic_future is None: return
        changed=False
        while True:
            try: probe=self.diagnostic_queue.get_nowait()
            except Empty: break
            self.diagnostic_result['probes'].append(probe)
            changed=True
        if changed:
            from diagnostics import summarize_probes
            self.diagnostic_result['summary']=summarize_probes(self.diagnostic_result['probes'])
        if self.diagnostic_future.done():
            try: self.diagnostic_result=self.diagnostic_future.result()
            except Exception as error:
                self.diagnostic_result=None
                self.diagnostic_error=f'진단 실행 실패: {error}'
            self.diagnostic_future=None
            for field in self.diagnostic_inputs.values(): field.set_active(self.view=='diagnostic')
            self.diagnostic_run_button.set_active(self.view=='diagnostic')
            changed=True
        if changed and self.view=='diagnostic': self.draw_diagnostic()

    def start_socket(self,event=None):
        if self.socket_future is not None: return
        from socket_metrics import SocketTestConfig, run_socket_test
        try:
            config=SocketTestConfig(duration=float(self.socket_inputs['duration'].text),
                                    chunk_bytes=int(self.socket_inputs['chunk_kb'].text)*1024)
        except ValueError as error:
            self.status.set_text(f'입력값을 확인하세요: {error}');self.fig.canvas.draw_idle();return
        self.socket_cancel=Event();self.socket_queue=Queue()
        self.socket_result={'source':'local_loopback_tcp_measurement','status':'running',
                            'started_at':datetime.now().isoformat(timespec='seconds'),
                            'config':asdict(config),'samples':[],'summary':{},'limitations':[], 'error':None}
        self.socket_future=self.executor.submit(run_socket_test,config,self.socket_cancel,self.socket_queue.put)
        self.draw()

    def cancel_socket(self,event=None):
        self.socket_cancel.set()

    def start_capture(self,event=None):
        self.begin_capture(from_file=False)

    def start_capture_file(self,event=None):
        self.begin_capture(from_file=True)

    def begin_capture(self,from_file=False):
        if self.capture_future is not None: return
        from packet_capture import CaptureConfig, run_capture, read_capture, follow_metadata
        try:
            duration=float(self.capture_inputs['duration'].text)
            if not 1<=duration<=120: raise ValueError('수집 시간은 1~120초입니다.')
            if from_file:
                path=Path(self.capture_inputs['path'].text).expanduser()
                if not path.is_absolute(): path=Path(__file__).resolve().parent/path
                follow=path.suffix.lower()=='.jsonl'
                function=follow_metadata if follow else read_capture
                arguments={'path':path,'max_packets':2000}
                if follow: arguments['duration']=duration
                source='live_tcp_metadata_file' if follow else 'capture_file_tcp_metadata'
            else:
                config=CaptureConfig(interface=self.capture_inputs['interface'].text.strip(),duration=duration,max_packets=2000)
                function=run_capture;arguments={'config':config};source='live_tcp_packet_metadata'
        except (ValueError,OSError) as error:
            self.status.set_text(f'입력값을 확인하세요: {error}');self.fig.canvas.draw_idle();return
        self.capture_cancel=Event();self.capture_queue=Queue();self.capture_flow=None
        self.capture_result={'source':source,'started_at':datetime.now().isoformat(timespec='seconds'),
                             'status':'running','error':None,'packets':[],'warnings':[],
                             'duration_seconds':0,'dropped_packets':None}
        self.capture_future=self.executor.submit(function,**arguments,cancel_event=self.capture_cancel,on_packet=self.capture_queue.put)
        self.draw()

    def cancel_capture(self,event=None):
        self.capture_cancel.set()

    def cycle_flow(self,event=None):
        from observed_plots import capture_flows
        flows=[key for key,label in capture_flows(self.capture_result or {})]
        if not flows: return
        index=flows.index(self.capture_flow) if self.capture_flow in flows else -1
        self.capture_flow=flows[(index+1)%len(flows)]
        self.draw_observed()

    def observed_tick(self):
        redraw=False;finished=False
        for kind,key,views in [('socket','samples',('socket',)),('capture','packets',('capture','packet'))]:
            future=getattr(self,kind+'_future')
            if future is None: continue
            result=getattr(self,kind+'_result');changed=False
            queue=getattr(self,kind+'_queue')
            while True:
                try: row=queue.get_nowait()
                except Empty: break
                result[key].append(row);changed=True
            if future.done():
                try: setattr(self,kind+'_result',future.result())
                except Exception as error:
                    result['status']='error';result['error']=str(error)
                setattr(self,kind+'_future',None)
                changed=True;finished=True
            if changed and self.view in views: redraw=True
        if finished:
            self.update_widget_activity()
        self.observed_dirty=self.observed_dirty or redraw
        if self.observed_dirty and self.view in ('socket','capture','packet') and (finished or time.monotonic()>=self.observed_next_draw):
            self.observed_next_draw=time.monotonic()+.5
            self.observed_dirty=False
            self.draw_observed()

    def draw_observed(self):
        from observed_plots import socket_axes, socket_metrics, plot_socket, capture_axes, capture_metrics, plot_capture, capture_flows
        from plots import MUTED,INK,TEAL
        self.clear_charts()
        is_socket=self.view=='socket'
        result=self.socket_result if is_socket else self.capture_result
        running=(self.socket_future if is_socket else self.capture_future) is not None
        if result is None:
            ax=self.fig.add_axes([.07,.19,.69,.48]);ax.axis('off');self.chart_axes=[ax]
            title='실제 TCP 연결의 내부 상태를 관측합니다.' if is_socket else '수집된 TCP 패킷으로 흐름을 확인합니다.'
            body=('01   측정 시작을 누르면 기기 내부에 실제 TCP 연결을 만듭니다.\n\n'
                  '02   전송 중 OS에서 cwnd · 윈도우 · RTT · 재전송 수를 읽습니다.\n\n'
                  '03   약 0.25초마다 얻은 측정값을 그래프로 표시합니다.') if is_socket else (
                  '01   인터페이스를 선택하고 수집 시작을 누릅니다.\n\n'
                  '02   권한이 필요한 Mac에서는 macOS_패킷_수집.command를 실행합니다.\n\n'
                  '03   파일 읽기 / 따라가기를 누르면 JSONL이 실시간 반영됩니다.\n\n'
                  '       기존 PCAP · PCAPNG 파일도 읽을 수 있습니다.')
            ax.text(0,.93,title,fontsize=22,weight='bold',va='top')
            ax.text(0,.72,body,fontsize=12,va='top',linespacing=1.5)
            self.status.set_text('측정 대기 · 시작 전에는 시험 데이터를 전송하지 않습니다.' if is_socket else '수집 대기 · 실제 패킷이 수집되어야 그래프가 표시됩니다.')
        else:
            if not is_socket:
                flows=capture_flows(result)
                if flows and self.capture_flow not in [key for key,label in flows]: self.capture_flow=flows[0][0]
            metrics=socket_metrics(result) if is_socket else capture_metrics(result,self.capture_flow)
            for i,(label,value) in enumerate(metrics):
                x=.045+i*.195
                self.metric_text.extend([self.fig.text(x,.868,label,fontsize=10,color=MUTED),
                                         self.fig.text(x,.828,value,fontsize=18,weight='bold',color=TEAL if i==2 else INK)])
            if is_socket:
                self.chart_axes=socket_axes(self.fig);plot_socket(result,self.chart_axes)
            else:
                view='headers' if self.view=='packet' else 'flow'
                self.chart_axes=capture_axes(self.fig,view=view);plot_capture(result,self.capture_flow,self.chart_axes,view=view)
            count=len(result.get('samples' if is_socket else 'packets',[]))
            state='측정 중' if running else {'completed':'측정 완료','complete':'수집 완료','cancelled':'중단됨','error':'실행 실패'}.get(result.get('status'),'수집 종료')
            error=result.get('error')
            self.status.set_text(f'{state} · {count}개 '+('표본' if is_socket else '패킷')+(f' · {error}' if error else ''))
        self.socket_run_button.label.set_text('측정 중…' if self.socket_future else '측정 시작')
        self.capture_run_button.label.set_text('수집 중…' if self.capture_future else '수집 시작')
        self.fig.canvas.draw_idle()

    def update_widget_activity(self):
        # Invisible axes still receive Matplotlib events unless widgets are disabled.
        for button in self.buttons: button.set_active(button.ax.get_visible())
        simulation=self.view in ('overview','connection','rdt')
        for slider in self.sliders.values(): slider.set_active(simulation)
        for kind,visible in [('diagnostic',self.view=='diagnostic'),('socket',self.view=='socket'),
                             ('capture',self.view in ('capture','packet'))]:
            running=getattr(self,kind+'_future') is not None
            for field in getattr(self,kind+'_inputs').values():
                if not visible: field.stop_typing()
                field.set_active(visible and not running)
            getattr(self,kind+'_run_button').set_active(visible and not running)
            getattr(self,kind+'_cancel_button').set_active(visible and running)
        self.capture_file_button.set_active(self.view in ('capture','packet') and self.capture_future is None)

    def clear_charts(self):
        for ax in self.chart_axes: ax.remove()
        for item in self.metric_text: item.remove()
        self.chart_axes=[];self.metric_text=[]

    def draw_diagnostic(self):
        from diagnostic_plots import diagnostic_axes, diagnostic_metrics, plot_diagnostics
        from plots import MUTED, INK, TEAL
        self.clear_charts()
        result=self.diagnostic_result
        running=self.diagnostic_future is not None
        self.diagnostic_run_button.label.set_text('진단 중…' if running else '진단 시작')
        self.diagnostic_cancel_button.set_active(running)
        if result is None:
            ax=self.fig.add_axes([.07,.19,.69,.48]);ax.axis('off');self.chart_axes=[ax]
            ax.text(0,.91,'어디에서 연결이 막히는지 확인하세요.',fontsize=23,weight='bold',va='top')
            ax.text(0,.69,'01   호스트 이름과 TCP 포트를 입력합니다.\n\n'
                                '02   진단 시작을 누르면 이름 조회와 실제 TCP 연결을 검사합니다.\n\n'
                                '03   매 회차의 지연과 성공·실패를 그래프에서 확인합니다.',fontsize=13,linespacing=1.6,va='top')
            ax.text(0,.13,'실시간 컴퓨터 관측은 [실시간 상태]에서 계속 볼 수 있습니다.',fontsize=11,color=TEAL)
            self.status.set_text(self.diagnostic_error or '진단 대기 · [진단 시작]을 누르기 전에는 대상 서버에 연결하지 않습니다.')
        else:
            for i,(label,value) in enumerate(diagnostic_metrics(result)):
                x=.045+i*.195
                self.metric_text.extend([self.fig.text(x,.868,label,fontsize=10,color=MUTED),
                                         self.fig.text(x,.828,value,fontsize=18,weight='bold',color=TEAL if i==1 else INK)])
            self.chart_axes=diagnostic_axes(self.fig)
            plot_diagnostics(result,self.chart_axes)
            summary=result['summary'];config=result['config']
            state='진단 중' if running else '중단됨' if result.get('cancelled') else '진단 완료'
            target=config['host'] if len(config['host'])<=48 else config['host'][:45]+'…'
            self.status.set_text(f"{state} · {target}:{config['port']} · {summary['attempted']}/{config['attempts']}회 · 성공 {summary['succeeded']}회 · 실패 {summary['failed']}회")
        self.fig.canvas.draw_idle()

    def cycle_interface(self,event=None):
        if not self.live_history: return
        names=[i['name'] for i in self.live_history[-1].get('interfaces',[])]
        if not names: return
        index=names.index(self.live_interface) if self.live_interface in names else -1
        self.live_interface=names[(index+1)%len(names)]
        self.draw_live()

    def draw_live(self):
        from live_plots import live_metrics,plot_live,selected,live_axes
        from plots import MUTED,INK,TEAL
        for ax in self.chart_axes: ax.remove()
        for t in self.metric_text: t.remove()
        self.chart_axes=[];self.metric_text=[]
        if not self.live_history:
            self.status.set_text('수집 중지 · [수집 재개]를 누르면 새 그래프를 시작합니다.' if self.live_paused
                                 else '운영체제에서 실제 CPU·메모리·네트워크 상태를 읽고 있습니다…')
            self.live_info.set_text('약 1초 간격 갱신\n\n네트워크 속도는\n두 표본 이후 표시됩니다.')
            self.fig.canvas.draw_idle();return
        snapshot=self.live_history[-1]
        for i,(label,value) in enumerate(live_metrics(snapshot,self.live_interface)):
            x=.045+i*.195
            self.metric_text += [self.fig.text(x,.868,label,fontsize=10,color=MUTED),
                                 self.fig.text(x,.828,value,fontsize=18,weight='bold',color=TEAL if i==2 else INK)]
        self.chart_axes=live_axes(self.fig)
        plot_live(list(self.live_history),self.live_interface,self.chart_axes)
        nic=selected(snapshot,self.live_interface)
        memory=snapshot.get('memory_total_bytes')
        memtext=f'{memory/1024**3:.1f} GiB' if memory is not None else '미확인'
        uptime=snapshot.get('uptime_seconds')
        uptext=f'{uptime/3600:.1f}시간' if uptime is not None else '미확인'
        link=nic.get('link_mbps')
        linktext=f'{link:g} Mbps' if link else '미확인'
        self.live_info.set_text(f"약 1초 간격 갱신\n최근 60개 표본 보관\n\n선택: {self.live_interface or '없음'}\n링크 속도: {linktext}\n\n전체 메모리: {memtext}\n부팅 후: {uptext}\n\n링크 속도와 현재\n송수신 속도는 다릅니다.")
        warnings=snapshot.get('warnings',[])
        warning=(' · '+warnings[0][:70]) if warnings else ''
        state='수집 중지 · 마지막 관측' if self.live_paused else '실시간 관측'
        self.status.set_text(f"{state} {snapshot['timestamp']} · 표본 {len(self.live_history)}개{warning}")
        self.fig.canvas.draw_idle()

    def normal(self,event=None):
        self.sliders['loss_pct'].set_val(0); self.run()

    def lossy(self,event=None):
        self.sliders['loss_pct'].set_val(8); self.run()

    def run(self,event=None):
        from dataclasses import replace
        values={k:s.val for k,s in self.sliders.items()}
        self.config=replace(self.config,rtt_ms=float(values['rtt_ms']),bandwidth_mbps=float(values['bandwidth_mbps']),
                            loss_pct=float(values['loss_pct']),receiver_window=int(values['receiver_kb']*1000),
                            total_bytes=int(values['total_kb']*1000),seed=int(values['seed']))
        try:
            self.result=simulate(self.config)
            self.draw()
        except ValueError as error:
            self.status.set_text(f'입력값을 확인하세요: {error}'); self.fig.canvas.draw_idle()

    def draw(self):
        from plots import grid_axes, plot_overview, plot_connection, plot_rdt, summary_text, rdt_summary_text, INK, MUTED, TEAL, RED
        live=self.view=='live'
        diagnostic=self.view=='diagnostic'
        socket=self.view=='socket'
        capture=self.view in ('capture','packet')
        simulation=self.view in ('overview','connection','rdt')
        for view,button in self.tab_buttons.items():
            button.color='#d8e9f8' if view==self.view else 'white'
            button.ax.set_facecolor(button.color)
        for artist in self.sim_controls: artist.set_visible(simulation)
        for artist in self.live_controls: artist.set_visible(live)
        for artist in self.diagnostic_controls: artist.set_visible(diagnostic)
        for artist in self.socket_controls: artist.set_visible(socket)
        for artist in self.capture_controls: artist.set_visible(capture)
        self.update_widget_activity()
        self.status.set_color(MUTED)
        if socket or capture:
            self.settings_title.set_text('실제 TCP 전송' if socket else '실제 TCP 패킷')
            self.badge.set_text('PYTHON · 기기 실측')
            self.subtitle.set_text('기기 내부 TCP 실측 · 루프백 연결에서 OS 값을 읽습니다.' if socket else '실제 수집된 TCP 헤더 · 연결별 SEQ / ACK / 윈도우 / 플래그')
            self.save_button.label.set_text('실측 결과 저장')
            self.help_text.set_text('기기 내부에서 제한된 속도로\n전송하는 시험입니다.\n인터넷 최대 속도가 아닙니다.' if socket else 'Mac 권한 오류 시 제공된\n패킷 수집 명령을 실행하세요.\n측정값은 로컬에만 보관합니다.')
            self.footer.set_text('실제 OS 관측 · 송신·수신 처리율은 같은 전송 방향의 양쪽 값입니다. 앱 응답 시간과 커널 RTT는 서로 다른 측정입니다.' if socket else '실제 헤더 관측 · 원시 윈도우 값 표시 · 누락·순서 변경만으로 패킷 손실이나 혼잡 윈도우를 추정하지 않습니다.')
            self.draw_observed();return
        self.settings_title.set_text('실제 상태 관측' if live else '진단할 대상' if diagnostic else 'TCP 실험 설정')
        self.badge.set_text('PYTHON · 실제 관측' if live else 'PYTHON · 실제 연결' if diagnostic else 'PYTHON · 모의 실험')
        self.subtitle.set_text('현재 컴퓨터의 CPU · 메모리 · 네트워크 · TCP 연결' if live
                               else '대상 하나를 정해 DNS와 TCP 연결을 검사합니다. 완료된 시도마다 그래프를 갱신합니다.' if diagnostic
                               else '5주차 신뢰 전송  /  6주차 TCP 기본  /  7주차 혼잡 제어')
        self.save_button.label.set_text('현재 상태 저장' if live else '진단 결과 저장' if diagnostic else 'PNG · CSV 저장')
        self.help_text.set_text('운영체제 정보만 읽습니다.\n외부 전송 · 패킷 내용 수집 없음' if live
                                else '진단 시작 시 DNS 조회와\n대상 포트 연결을 시도합니다.\n측정마다 그래프를 갱신합니다.' if diagnostic
                                else '고정 rwnd · 즉시 ACK\nDATA만 손실 · Reno 학습 모델')
        if diagnostic:
            self.footer.set_text('실제 연결 검사 · 연결 지연은 ICMP RTT와 다르며 연결 실패 비율은 패킷 손실률이 아닙니다. HTTP·TLS·최대 속도는 검사하지 않습니다.')
            self.draw_diagnostic();return
        if live:
            self.footer.set_text('실제 관측값 · 선택한 인터페이스의 모든 트래픽을 포함합니다. 인터넷 최대 속도나 TCP 전용 처리율이 아닙니다.')
            self.draw_live();return
        if self.result is None: self.result=simulate(self.config)
        for ax in self.chart_axes: ax.remove()
        for text in self.metric_text: text.remove()
        self.metric_text=[]
        rdt = compare_rdt(rtt_ms=self.config.rtt_ms) if self.view=='rdt' else None
        metrics = rdt_summary_text(rdt) if rdt else summary_text(self.result)
        for i,(label,value) in enumerate(metrics):
            x=.045+i*.195
            self.metric_text += [self.fig.text(x,.868,label,fontsize=10,color=MUTED),
                                 self.fig.text(x,.828,value,fontsize=18,weight='bold',color=TEAL if i==2 else INK)]
        if self.view=='connection':
            gs=self.fig.add_gridspec(1,2,left=.045,right=.785,bottom=.115,top=.70,wspace=.22,width_ratios=[1.25,1])
            self.chart_axes=[self.fig.add_subplot(gs[0,0]),self.fig.add_subplot(gs[0,1])]
            plot_connection(self.result,self.chart_axes)
        else:
            self.chart_axes=grid_axes(self.fig,area=(.055,.14,.725,.56))
            if self.view=='rdt':
                plot_rdt(rdt,self.chart_axes)
            else:
                plot_overview(self.result,self.chart_axes)
        c=self.config
        state='전달·확인 완료' if self.result.summary['completed'] else '시간 제한으로 일부만 전달됨'
        message=f'{state}   |   RTT {c.rtt_ms:g} ms · {c.bandwidth_mbps:g} Mbps · DATA 손실 {c.loss_pct:g}% · seed {c.seed}'
        if self.view=='rdt': message='별도 비교: 12개 패킷 · window=4 · 3번 DATA 첫 전송만 손실 · ACK 손실 없음 · TCP 손실 확률과 별개'
        self.status.set_text(message)
        self.status.set_color(MUTED if self.result.summary['completed'] else RED)
        self.footer.set_text('학습용 시뮬레이션 · RDT 비교에서는 순환하지 않는 패킷 번호를 사용합니다.' if self.view=='rdt'
                             else '실제 패킷 캡처가 아닌 학습용 시뮬레이션 · TCP 데이터와 ACK는 바이트 번호로 추적합니다.')
        self.fig.canvas.draw_idle()

    def save(self,event=None):
        folder=Path(__file__).resolve().parent/'exports'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        try:
            if self.view in ('socket','capture','packet'):
                kind='socket' if self.view=='socket' else 'capture'
                result=getattr(self,kind+'_result')
                if result is None or getattr(self,kind+'_future') is not None:
                    self.status.set_text('측정이 완료되거나 중단된 뒤 저장해 주세요.');self.fig.canvas.draw_idle();return
                saved=export_observed(result,folder,kind,self.capture_flow)
                self.status.set_text(f'실측 그래프 · JSON · CSV 저장 완료: exports/{saved.name}');self.fig.canvas.draw_idle();return
            if self.view=='diagnostic':
                if self.diagnostic_future is not None or self.diagnostic_result is None:
                    self.status.set_text('진단이 완료되거나 중단된 뒤 결과를 저장해 주세요.')
                    self.fig.canvas.draw_idle();return
                saved=export_diagnostics(self.diagnostic_result,folder)
                self.status.set_text(f'진단 그래프 · JSON · CSV 저장 완료: exports/{saved.name}')
                self.fig.canvas.draw_idle();return
            if self.view=='live':
                if not self.live_history:
                    self.status.set_text('관측값이 모인 뒤 저장해 주세요.');self.fig.canvas.draw_idle();return
                from live_plots import live_figure
                from matplotlib.backends.backend_agg import FigureCanvasAgg
                folder.mkdir(parents=True,exist_ok=True)
                history=list(self.live_history)
                fig=live_figure(history,self.live_interface);FigureCanvasAgg(fig)
                fig.savefig(folder/'computer_status.png',dpi=140);fig.clear()
                (folder/'computer_status.json').write_text(json.dumps({'source':'live_operating_system_observation',
                       'selected_interface':self.live_interface,'history':history},ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
                write_csv(folder/'tcp_connections.csv',history[-1].get('connections',[]))
                self.status.set_text(f'실제 상태 저장 완료: exports/{folder.name}')
                self.fig.canvas.draw_idle();return
            saved=export_results(self.result,folder)
            self.status.set_text(f'그래프 3개와 CSV·JSON 저장 완료: exports/{saved.name}')
        except (OSError,ValueError) as error:
            self.status.set_text(f'저장 실패: {error}')
        self.fig.canvas.draw_idle()


def parse_args(argv=None):
    parser=argparse.ArgumentParser(description='Real-time network monitor, DNS/TCP diagnostics and teaching lab')
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--no-gui',action='store_true',help='Export simulated TCP graphs without a window')
    modes.add_argument('--live',action='store_true',help='Open real-time computer status (default)')
    modes.add_argument('--status',action='store_true',help='Print one real computer snapshot as JSON')
    modes.add_argument('--simulate',action='store_true',help='Open the TCP simulator tab')
    modes.add_argument('--diagnostic-ui',action='store_true',help='Open target diagnostics without starting a probe')
    modes.add_argument('--diagnose',metavar='HOST',help='Run DNS/TCP checks once and print JSON without a GUI')
    modes.add_argument('--tcp-test',action='store_true',help='Measure an actual loopback TCP transfer and print JSON')
    modes.add_argument('--socket-ui',action='store_true',help='Open actual TCP socket measurements')
    modes.add_argument('--packet-ui',action='store_true',help='Open actual TCP packet graphs')
    modes.add_argument('--capture',metavar='INTERFACE',help='Capture TCP metadata using existing permissions')
    modes.add_argument('--read-capture',metavar='FILE',type=Path,help='Read PCAP/PCAPNG, or follow a sanitized JSONL stream')
    parser.add_argument('--duration',type=float,default=10,help='Seconds for TCP test (1..30) or capture (1..120)')
    parser.add_argument('--chunk-kib',type=int,default=16,help='Bytes per test chunk in KiB (1..64)')
    parser.add_argument('--max-packets',type=int,default=2000,help='Capture record limit (1..10000)')
    parser.add_argument('--port',type=int,default=443,help='TCP port for --diagnose (1..65535)')
    parser.add_argument('--attempts',type=int,default=5,help='TCP attempts for --diagnose (1..20)')
    parser.add_argument('--timeout',type=float,default=2,help='DNS and per-attempt timeout in seconds (.2..10)')
    parser.add_argument('--export',type=Path,help='Folder for PNG graphs and CSV/JSON data')
    parser.add_argument('--loss',type=float,default=2,help='DATA loss probability, percent')
    parser.add_argument('--rtt',type=float,default=80,help='Propagation RTT, milliseconds')
    parser.add_argument('--bandwidth',type=float,default=2,help='Link rate, Mbps')
    parser.add_argument('--window',type=int,default=16000,help='Fixed receive window, bytes')
    parser.add_argument('--bytes',type=int,default=64000,help='Payload bytes to transfer')
    parser.add_argument('--seed',type=int,default=1,help='Reproducible random seed')
    parser.add_argument('--max-time',type=float,default=60,help='Simulation time limit, seconds')
    args=parser.parse_args(argv)
    if args.export and not (args.no_gui or args.simulate or args.diagnose or args.tcp_test or args.capture or args.read_capture):
        parser.error('--export는 --diagnose, --tcp-test, --capture, --read-capture, --simulate 또는 --no-gui와 함께 사용하세요.')
    return args


def main(argv=None):
    args=parse_args(argv)
    try:
        if args.tcp_test or args.capture or args.read_capture:
            if args.tcp_test:
                from socket_metrics import SocketTestConfig, run_socket_test
                result=run_socket_test(SocketTestConfig(duration=args.duration,chunk_bytes=args.chunk_kib*1024))
            else:
                from packet_capture import CaptureConfig, run_capture, read_capture, follow_metadata
                if args.capture:
                    result=run_capture(CaptureConfig(args.capture,args.duration,args.max_packets))
                elif args.read_capture.suffix.lower()=='.jsonl':
                    result=follow_metadata(args.read_capture,duration=args.duration,max_packets=args.max_packets)
                else:
                    result=read_capture(args.read_capture,max_packets=args.max_packets)
            if args.export: print('Saved:',export_observed(result,args.export,'socket' if args.tcp_test else 'capture'),file=sys.stderr)
            print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
            return 1 if result.get('error') or result.get('status')=='cancelled' else 0
        if args.diagnose is not None:
            from diagnostics import DiagnosticConfig, run_diagnostics
            diagnosis=run_diagnostics(DiagnosticConfig(args.diagnose,args.port,args.attempts,args.timeout))
            if args.export:
                print('Saved:',export_diagnostics(diagnosis,args.export),file=sys.stderr)
            print(json.dumps(diagnosis,ensure_ascii=False,indent=2,allow_nan=False))
            summary=diagnosis['summary']
            return 0 if summary['succeeded']==args.attempts and not diagnosis['cancelled'] else 1
        if args.status:
            from system_monitor import SystemMonitor
            monitor=SystemMonitor();monitor.sample();time.sleep(1)
            print(json.dumps(monitor.sample(),ensure_ascii=False,indent=2,allow_nan=False))
            return 0
        config=Config(loss_pct=args.loss,rtt_ms=args.rtt,bandwidth_mbps=args.bandwidth,
                      receiver_window=args.window,total_bytes=args.bytes,seed=args.seed,max_time=args.max_time)
        if args.export or args.no_gui:
            result=simulate(config)
            print('Saved:',export_results(result,args.export or Path(__file__).resolve().parent/'exports'/'latest'))
            print(json.dumps(result.summary,ensure_ascii=False,indent=2))
        if not args.no_gui:
            app=Lab(config,start_view='overview' if args.simulate else 'diagnostic' if args.diagnostic_ui else 'socket' if args.socket_ui else 'capture' if args.packet_ui else 'live')
            app.plt.show()
        return 0
    except (ValueError,OSError) as error:
        print(f'실행 실패: {error}',file=sys.stderr)
        return 2
    except ImportError as error:
        print(f'필요한 라이브러리가 없습니다: {error}\npython3 -m pip install -r requirements.txt',file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
