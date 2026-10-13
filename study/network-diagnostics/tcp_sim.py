"""Deterministic, educational TCP Reno event simulation (no network access).

Sequence arithmetic follows RFC 9293, congestion control RFC 5681 §§3.1–3.2,
and the estimator/timer RFC 6298 §§2–5. This is a teaching model, not a full
TCP stack. MODEL_NOTES documents deliberately omitted protocol behavior.

Sources: https://www.rfc-editor.org/rfc/rfc9293.html
         https://www.rfc-editor.org/rfc/rfc5681.html
         https://www.rfc-editor.org/rfc/rfc6298.html
"""

from dataclasses import dataclass, field
import heapq
import math
import random


MODEL_NOTES = (
    "교육용 시뮬레이션이며 실제 패킷 캡처나 측정 결과가 아닙니다.",
    "단일 연결, 단방향 데이터, 고정 MSS, 즉시 누적 ACK, TCP Reno를 모델링합니다.",
    "RTT 설정은 왕복 전파 지연입니다. 실제 RTT에는 직렬화 및 송신 대기 시간이 더해집니다.",
    "방향별 링크 대역폭은 같고 독립적입니다. 패킷마다 TCP/IP 헤더 40바이트를 포함합니다.",
    "데이터 패킷에만 독립적인 확률 손실을 적용하며 ACK·SYN·FIN은 손실되지 않습니다.",
    "수신 애플리케이션은 데이터를 즉시 읽습니다. rwnd는 고정이며 0 윈도우는 모델링하지 않습니다.",
    "기본 초기 cwnd=1 MSS는 학습을 위한 설정이며 현대 운영체제의 기본값을 뜻하지 않습니다.",
    "SACK·ECN·CUBIC·지연 ACK·limited transmit·순서번호 순환은 생략합니다.",
    "연결 종료의 TIME-WAIT 상태를 표시하지만 실제 2×MSL 대기는 생략합니다.",
    "goodput은 중복 없는 수신 바이트를 연결 전체 경과 시간으로 나눈 값입니다.",
)


@dataclass(frozen=True)
class Config:
    total_bytes: int = 64000
    mss: int = 1000
    rtt_ms: float = 80
    bandwidth_mbps: float = 2
    loss_pct: float = 2
    receiver_window: int = 16000
    initial_cwnd: int = 1
    initial_ssthresh: int = 16
    seed: int = 7
    max_time: float = 60

    def __post_init__(self):
        integer_bounds = {
            "total_bytes": (1, 10_000_000),
            "mss": (64, 9000),
            "receiver_window": (1, 10_000_000),
            "initial_cwnd": (1, 1000),
            "initial_ssthresh": (2, 10000),
            "seed": (-2**31, 2**31 - 1),
        }
        real_bounds = {
            "rtt_ms": (1, 5000),
            "bandwidth_mbps": (0.01, 1000),
            "loss_pct": (0, 100),
            "max_time": (0.001, 600),
        }
        for name, (low, high) in integer_bounds.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{name}: {low}~{high} 범위의 정수가 필요합니다.")
        for name, (low, high) in real_bounds.items():
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not low <= value <= high or not math.isfinite(value)):
                raise ValueError(f"{name}: {low}~{high} 범위의 유한한 수가 필요합니다.")
        segment_count = math.ceil(self.total_bytes / min(self.mss, self.receiver_window))
        if segment_count > 20000:
            raise ValueError("빠른 그래프 출력을 위해 데이터 세그먼트를 20,000개 이하로 설정해 주세요.")


@dataclass
class Result:
    config: Config
    packets: list = field(default_factory=list)
    samples: list = field(default_factory=list)
    rtt_samples: list = field(default_factory=list)
    events: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    model_notes: list = field(default_factory=lambda: list(MODEL_NOTES))


@dataclass
class _Segment:
    seq: int
    length: int
    first_sent: float
    attempts: int = 0


@dataclass
class _RttEstimator:
    srtt: float | None = None
    rttvar: float | None = None
    rto: float = 1.0

    def observe(self, measured):
        if self.srtt is None:
            self.srtt = measured
            self.rttvar = measured / 2
        else:
            self.rttvar = 0.75 * self.rttvar + 0.25 * abs(self.srtt - measured)
            self.srtt = 0.875 * self.srtt + 0.125 * measured
        self.rto = max(1.0, self.srtt + max(0.001, 4 * self.rttvar))

    def backoff(self):
        self.rto *= 2


class _Simulator:
    DATA_START = 1001
    SERVER_NEXT = 5001

    def __init__(self, config):
        self.config = config
        self.result = Result(config)
        self.rng = random.Random(config.seed)
        self.queue = []
        self.event_id = 0
        self.now = 0.0
        self.link_free = {"C>S": 0.0, "S>C": 0.0}
        self.cwnd = float(config.initial_cwnd)
        self.ssthresh = float(config.initial_ssthresh)
        self.next_seq = self.DATA_START
        self.acked_seq = self.DATA_START
        self.recv_seq = self.DATA_START
        self.outstanding = {}
        self.recv_buffer = {}
        self.duplicate_acks = 0
        self.recovery = False
        self.timer_generation = 0
        self.timed_out_sequences = set()
        self.rtt = _RttEstimator()
        self.timeouts = 0
        self.fast_retransmits = 0
        self.client_state = "CLOSED"
        self.server_state = "LISTEN"
        self.data_start_time = None
        self.data_acked_time = None
        self.closed = False
        self.stop_reason = ""

    @property
    def inflight(self):
        return self.next_seq - self.acked_seq

    def _schedule(self, at, kind, payload=None):
        self.event_id += 1
        heapq.heappush(self.queue, (at, self.event_id, kind, payload))

    def _event(self, kind, detail, **values):
        self.result.events.append({"time": self.now, "kind": kind, "detail": detail, **values})

    def _state(self, side, state):
        attr = "client_state" if side == "client" else "server_state"
        previous = getattr(self, attr)
        setattr(self, attr, state)
        self._event("state", f"{side}: {previous} → {state}", side=side, state=state)

    def _sample(self):
        self.result.samples.append({
            "time": self.now, "cwnd": self.cwnd, "ssthresh": self.ssthresh,
            "inflight": self.inflight, "rwnd": self.config.receiver_window,
            "delivered": self.recv_seq - self.DATA_START,
            "acknowledged": self.acked_seq - self.DATA_START,
            "rto_ms": self.rtt.rto * 1000,
            "phase": ("fast_recovery" if self.recovery else
                      "slow_start" if self.cwnd < self.ssthresh else "congestion_avoidance"),
            "client_state": self.client_state, "server_state": self.server_state,
        })

    def _packet(self, direction, flags, seq, ack, length, kind, *, lost=False,
                retransmission=False, reason=""):
        wire_time = max(self.now, self.link_free[direction])
        wire_end = wire_time + (length + 40) * 8 / (self.config.bandwidth_mbps * 1_000_000)
        self.link_free[direction] = wire_end
        arrival = wire_end + self.config.rtt_ms / 2000
        packet = {
            "time": self.now, "arrival": None, "direction": direction,
            "flags": flags, "seq": seq, "ack": ack, "length": length,
            "window": self.config.receiver_window, "lost": lost,
            "retransmission": retransmission, "reason": reason,
            "wire_time": wire_time, "scheduled_arrival": None if lost else arrival,
            "received": False, "cwnd": self.cwnd, "inflight": self.inflight,
            "sender_ack": self.acked_seq,
        }
        self.result.packets.append(packet)
        if not lost:
            self._schedule(arrival, kind, packet)
        return packet

    def _restart_timer(self):
        self.timer_generation += 1
        if self.outstanding:
            self._schedule(self.now + self.rtt.rto, "timeout", self.timer_generation)

    def _send_data(self, segment, reason=""):
        segment.attempts += 1
        lost = self.rng.random() * 100 < self.config.loss_pct
        self._packet("C>S", "PSH,ACK", segment.seq, self.SERVER_NEXT,
                     segment.length, "data", lost=lost,
                     retransmission=segment.attempts > 1, reason=reason or "data")
        if lost:
            self._event("drop", f"데이터 손실: seq={segment.seq}, {segment.length} bytes",
                        seq=segment.seq)

    def _fill_window(self):
        end = self.DATA_START + self.config.total_bytes
        while self.next_seq < end:
            length = min(self.config.mss, self.config.receiver_window, end - self.next_seq)
            limit = min(self.cwnd * self.config.mss, self.config.receiver_window)
            if self.inflight + length > limit + 1e-9:
                break
            was_empty = not self.outstanding
            segment = _Segment(self.next_seq, length, self.now)
            self.outstanding[segment.seq] = segment
            self.next_seq += length
            self._send_data(segment)
            if was_empty:
                self._restart_timer()

    def _receive_data(self, packet):
        seq, length = packet["seq"], packet["length"]
        if seq >= self.recv_seq:
            self.recv_buffer[seq] = length
        while self.recv_seq in self.recv_buffer:
            self.recv_seq += self.recv_buffer.pop(self.recv_seq)
        self._packet("S>C", "ACK", self.SERVER_NEXT, self.recv_seq, 0, "ack", reason="cumulative_ack")

    def _receive_ack(self, packet):
        ack = packet["ack"]
        if ack > self.acked_seq:
            newly_acked = [s for s in self.outstanding.values() if s.seq + s.length <= ack]
            acknowledged_bytes = ack - self.acked_seq
            # Conservative Karn exclusion: skip an ACK covering any retransmission.
            if newly_acked and all(s.attempts == 1 for s in newly_acked):
                segment = newly_acked[-1]
                measured = self.now - segment.first_sent
                self.rtt.observe(measured)
                self.result.rtt_samples.append({
                    "time": self.now, "rtt_ms": measured * 1000,
                    "srtt_ms": self.rtt.srtt * 1000, "rto_ms": self.rtt.rto * 1000,
                    "rttvar_ms": self.rtt.rttvar * 1000, "seq": segment.seq,
                })
            for segment in newly_acked:
                del self.outstanding[segment.seq]
            self.acked_seq = ack
            self.duplicate_acks = 0
            if self.recovery:
                self.recovery = False
                self.cwnd = self.ssthresh
                self._event("fast_recovery_exit", "새 누적 ACK: cwnd를 ssthresh로 줄임")
            elif self.cwnd < self.ssthresh:
                self.cwnd += min(acknowledged_bytes, self.config.mss) / self.config.mss
            else:
                # RFC 5681 equation (3): SMSS²/cwnd bytes per advancing ACK.
                self.cwnd += 1 / self.cwnd
            self._restart_timer()
            if self.acked_seq == self.DATA_START + self.config.total_bytes:
                self.data_acked_time = self.now
                self._event("transfer_complete", "모든 데이터에 대한 누적 ACK 수신")
                self._state("client", "FIN-WAIT-1")
                self._packet("C>S", "FIN,ACK", self.acked_seq, self.SERVER_NEXT,
                             0, "fin", reason="active_close")
            else:
                self._fill_window()
        elif ack == self.acked_seq and self.outstanding:
            self.duplicate_acks += 1
            self._event("duplicate_ack", f"중복 ACK {self.duplicate_acks}: ack={ack}",
                        ack=ack, count=self.duplicate_acks)
            if self.duplicate_acks == 3 and not self.recovery:
                self.ssthresh = max(self.inflight / (2 * self.config.mss), 2.0)
                self.cwnd = self.ssthresh + 3
                self.recovery = True
                self.fast_retransmits += 1
                self._event("fast_retransmit", "중복 ACK 3개: 가장 오래된 미확인 세그먼트 재전송",
                            seq=min(self.outstanding), cwnd=self.cwnd,
                            ssthresh=self.ssthresh, flight_bytes=self.inflight)
                self._send_data(self.outstanding[min(self.outstanding)], "fast_retransmit")
                self._restart_timer()
                self._fill_window()
            elif self.recovery and self.duplicate_acks > 3:
                self.cwnd += 1
                self._fill_window()

    def _timeout(self, generation):
        if generation != self.timer_generation or not self.outstanding:
            return
        seq = min(self.outstanding)
        if seq not in self.timed_out_sequences:
            self.ssthresh = max(self.inflight / (2 * self.config.mss), 2.0)
            self.timed_out_sequences.add(seq)
        self.cwnd = 1.0
        self.recovery = False
        self.duplicate_acks = 0
        self.timeouts += 1
        self.rtt.backoff()
        self._event("timeout", "RTO 만료: cwnd=1 MSS, RTO 두 배, 가장 오래된 세그먼트 재전송",
                    seq=seq, cwnd=self.cwnd, ssthresh=self.ssthresh,
                    rto_ms=self.rtt.rto * 1000, flight_bytes=self.inflight)
        self._send_data(self.outstanding[seq], "timeout")
        self._restart_timer()

    def _handle(self, kind, packet):
        if kind == "timeout":
            self._timeout(packet)
            return
        packet["arrival"] = self.now
        packet["received"] = True
        if kind == "syn":
            self._state("server", "SYN-RECEIVED")
            self._packet("S>C", "SYN,ACK", 5000, self.DATA_START, 0, "syn_ack", reason="handshake")
        elif kind == "syn_ack":
            self._state("client", "ESTABLISHED")
            self._packet("C>S", "ACK", self.DATA_START, self.SERVER_NEXT,
                         0, "handshake_ack", reason="handshake")
            self.data_start_time = self.now
            self._fill_window()
        elif kind == "handshake_ack":
            self._state("server", "ESTABLISHED")
        elif kind == "data":
            self._receive_data(packet)
        elif kind == "ack":
            self._receive_ack(packet)
        elif kind == "fin":
            self._state("server", "CLOSE-WAIT")
            self._packet("S>C", "ACK", self.SERVER_NEXT, self.acked_seq + 1,
                         0, "fin_ack", reason="fin_ack")
            self._state("server", "LAST-ACK")
            self._packet("S>C", "FIN,ACK", self.SERVER_NEXT, self.acked_seq + 1,
                         0, "peer_fin", reason="passive_close")
        elif kind == "fin_ack":
            self._state("client", "FIN-WAIT-2")
        elif kind == "peer_fin":
            self._state("client", "TIME-WAIT")
            self._packet("C>S", "ACK", self.acked_seq + 1, self.SERVER_NEXT + 1,
                         0, "close_ack", reason="final_ack")
        elif kind == "close_ack":
            self._state("server", "CLOSED")
            self._event("time_wait_compressed", "교육용 모델: 실제 2×MSL 대기는 생략")
            self._state("client", "CLOSED")
            self.closed = True

    def run(self):
        self._state("client", "SYN-SENT")
        self._packet("C>S", "SYN", 1000, 0, 0, "syn", reason="handshake")
        self._sample()
        while self.queue and not self.closed:
            at, _, kind, payload = heapq.heappop(self.queue)
            # Invalidated timers must not extend the measured connection duration.
            if kind == "timeout" and payload != self.timer_generation:
                continue
            if at > self.config.max_time:
                self.now = self.config.max_time
                self.stop_reason = "max_time"
                self._event("limit", "최대 시뮬레이션 시간 도달")
                self._sample()
                break
            self.now = at
            self._handle(kind, payload)
            self._sample()
            if len(self.result.packets) >= 100000:
                self.stop_reason = "packet_limit"
                self._event("limit", "교육용 출력 한도: 패킷 100,000개 도달")
                break
        delivered = self.recv_seq - self.DATA_START
        data_packets = [p for p in self.result.packets if p["length"]]
        self.result.summary = {
            "delivered_bytes": delivered, "total_bytes": self.config.total_bytes,
            "acknowledged_bytes": self.acked_seq - self.DATA_START,
            "duration": self.now,
            "data_duration": (None if self.data_acked_time is None else
                              self.data_acked_time - self.data_start_time),
            "goodput_mbps": delivered * 8 / max(self.now, 1e-12) / 1_000_000,
            "transmissions": len(data_packets),
            "retransmissions": sum(p["retransmission"] for p in data_packets),
            "drops": sum(p["lost"] for p in data_packets),
            "completed": self.data_acked_time is not None,
            "connection_closed": self.closed,
            "timeout_count": self.timeouts, "fast_retransmits": self.fast_retransmits,
            "stop_reason": self.stop_reason or "completed",
        }
        return self.result


def simulate(config=None):
    """Return generated packets, event samples and transfer statistics.

    Times use seconds; cwnd/ssthresh use MSS units; lengths/windows use bytes.
    ``packets.time`` is submission to the sender link queue; ``wire_time`` is
    serialization start. ``arrival=None`` means lost or still in transit when
    max_time stops the model. Loss-free controls are included in packets but
    summary.transmissions/retransmissions/drops count only data packets.
    summary.completed means every payload byte has been acknowledged;
    summary.connection_closed additionally requires the final close ACK.
    """
    if config is None:
        config = Config()
    if not isinstance(config, Config):
        raise TypeError("config must be a Config instance")
    return _Simulator(config).run()
