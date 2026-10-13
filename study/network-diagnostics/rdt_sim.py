"""Deterministic teaching model of Stop-and-Wait, GBN, and Selective Repeat.

This is a simulation, not a packet capture or a TCP implementation. Packet numbers
are unbounded teaching labels. A real SR protocol needs a sequence-number space
of at least 2 * window to distinguish new packets from old duplicates.

DATA packets are 1,000 bytes on a 1 Mbps serialized forward link (8 ms each).
RTT is propagation only; ACK serialization/processing takes zero time. ACKs are
reliable. The selected DATA packet is lost on its first attempt only. All timers
are fixed at one second. Timing uses an event queue, never sleep or networking.
"""

from collections import deque
from heapq import heappop, heappush
from itertools import count
from math import isfinite


SERIALIZATION_SECONDS = 0.008
TIMEOUT_SECONDS = 1.0


def compare_rdt(packet_count=12, window=4, lost_packet=3, rtt_ms=80, *, loss=True):
    """Return comparable protocol results with times in seconds.

    Each result has name, events, completed, duration, transmissions,
    retransmissions, peak_buffer_packets, and delivered_packets (a count).
    Events record wire departure/arrival time, direction, packet number, kind
    (DATA or ACK), lost, and retransmission. Lost DATA arrival is the nominal
    arrival time, useful for drawing its interrupted path. ACK packet numbers
    mean the last in-order packet for GBN/SW, or the individual packet for SR.
    Set loss=False to use the same model without deliberate packet loss.
    """
    for label, value in (("packet_count", packet_count), ("window", window)):
        if type(value) is not int or value < 1:
            raise ValueError(f"{label} must be a positive integer")
    if type(loss) is not bool:
        raise ValueError("loss must be a boolean")
    if loss and (type(lost_packet) is not int or not 1 <= lost_packet <= packet_count):
        raise ValueError("lost_packet must be between 1 and packet_count")
    if isinstance(rtt_ms, bool) or not isinstance(rtt_ms, (int, float)):
        raise ValueError("rtt_ms must be a finite non-negative number")
    if not isfinite(rtt_ms) or rtt_ms < 0:
        raise ValueError("rtt_ms must be a finite non-negative number")
    return [
        _simulate(name, packet_count, 1 if name == "Stop-and-Wait" else window,
                  lost_packet if loss else None, rtt_ms / 2000.0)
        for name in ("Stop-and-Wait", "GBN", "SR")
    ]


def _simulate(name, packet_count, window, lost_packet, propagation):
    selective = name == "SR"
    queue, events = [], []
    serial = count()
    pending, queued = deque(), set()
    outstanding, attempts = {}, {}
    buffered = set()
    base = next_packet = expected = 1
    now = link_free = 0.0
    timer_token = peak_buffer = 0
    timer_active = tx_scheduled = False

    def schedule(time, kind, payload=None):
        # Process ACKs before a timer expiring at exactly the same instant.
        priority = {"data": 0, "ack": 1, "tx": 2, "timeout": 3}[kind]
        heappush(queue, (time, priority, next(serial), kind, payload))

    def request_send(packet, *, front=False):
        nonlocal tx_scheduled
        if packet not in queued:
            (pending.appendleft if front else pending.append)(packet)
            queued.add(packet)
        if not tx_scheduled:
            schedule(max(now, link_free), "tx")
            tx_scheduled = True

    def fill_window():
        nonlocal next_packet
        while next_packet <= packet_count and next_packet < base + window:
            outstanding[next_packet] = 0
            request_send(next_packet)
            next_packet += 1

    def start_base_timer():
        nonlocal timer_token, timer_active
        if not timer_active and outstanding.get(base, 0):
            timer_token += 1
            timer_active = True
            schedule(now + TIMEOUT_SECONDS, "timeout", (base, timer_token))

    fill_window()
    while queue:
        now, _, _, kind, payload = heappop(queue)
        if kind == "tx":
            tx_scheduled = False
            while pending:
                packet = pending.popleft()
                queued.remove(packet)
                if packet in outstanding:
                    break
            else:
                continue
            attempts[packet] = attempts.get(packet, 0) + 1
            attempt = attempts[packet]
            outstanding[packet] = attempt
            lost = packet == lost_packet and attempt == 1
            arrival = now + SERIALIZATION_SECONDS + propagation
            events.append(dict(time=now, arrival=arrival, direction="sender→receiver",
                               packet=packet, kind="DATA", lost=lost,
                               retransmission=attempt > 1))
            if not lost:
                schedule(arrival, "data", packet)
            if selective:
                schedule(now + TIMEOUT_SECONDS, "timeout", (packet, attempt))
            elif packet == base:
                start_base_timer()
            link_free = now + SERIALIZATION_SECONDS
            if pending:
                schedule(link_free, "tx")
                tx_scheduled = True
        elif kind == "data":
            packet = payload
            if packet == expected:
                expected += 1
                while expected in buffered:
                    buffered.remove(expected)
                    expected += 1
            elif selective and expected < packet < expected + window:
                buffered.add(packet)
                peak_buffer = max(peak_buffer, len(buffered))
            ack = packet if selective else expected - 1
            events.append(dict(time=now, arrival=now + propagation,
                               direction="receiver→sender", packet=ack,
                               kind="ACK", lost=False, retransmission=False))
            schedule(now + propagation, "ack", ack)
        elif kind == "ack":
            if selective:
                outstanding.pop(payload, None)
            else:
                for packet in list(outstanding):
                    if packet <= payload:
                        del outstanding[packet]
            previous_base = base
            while base < next_packet and base not in outstanding:
                base += 1
            if not selective and base != previous_base:
                timer_token += 1
                timer_active = False
                start_base_timer()
            fill_window()
            if base > packet_count:
                break
        else:
            packet, token = payload
            if selective:
                if outstanding.get(packet) == token:
                    request_send(packet, front=True)
            elif packet == base and token == timer_token and timer_active:
                timer_active = False
                timer_token += 1
                # Reserved but not yet serialized packets are first sends,
                # so only packets already on the wire count as retries.
                for packet in sorted(outstanding, reverse=True):
                    if outstanding[packet]:
                        request_send(packet, front=True)

    transmissions = sum(attempts.values())
    return dict(name=name, events=events, completed=base > packet_count,
                duration=now, transmissions=transmissions,
                retransmissions=transmissions - len(attempts),
                peak_buffer_packets=peak_buffer, delivered_packets=expected - 1)
