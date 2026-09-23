#!/usr/bin/env python3
#
#  hw_planner_sweep.py - line throughput vs planner buffer depth ($398), on real hardware
#
#  Part of grblHAL
#
#  Copyright (c) 2026 grblHAL contributors
#
#  grblHAL is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  grblHAL is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with grblHAL. If not, see <http://www.gnu.org/licenses/>.

"""Sweep $398 (planner buffer blocks) and measure steady-state line throughput.

    hw_planner_sweep.py <port> [count]

A deeper planner buffer gives more look-ahead distance, so short-segment moves
can run faster (v = sqrt(2 * a * d)), but planner_recalculate() may walk most of
the buffer for every new line when the buffer is the limit - on an FPU-less
RP2040 that cost grows with depth. This finds where one overtakes the other.

$398 only takes effect at boot (plan_reset() allocates once), so every depth
change is followed by $REBOOT and a reconnect. The depth actually allocated is
read back from $I's [OPT:...] line, since allocation silently falls back to a
smaller buffer when RAM runs short.

Each run streams `count` same-direction +0.01mm lines. Rate is measured over
steady state only: acks for the first (depth + 200) lines are skipped, because
those land in an empty buffer at parse speed and would flatter deep buffers.

Original $398/$110/$111/$120/$121 are restored, and the board rebooted, at the
end - including on error. Needs nothing attached.
"""

import os
import re
import sys
import time
from collections import deque

import serial

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serial_bridge as bridge    # noqa: E402
import hw_line_rate as lr         # noqa: E402

DEPTHS = [100, 200, 400, 800, 1000]
PROFILES = [
    # name, $110/$111 mm/min, $120/$121 mm/s^2
    ("default", 500, 10),
    ("a100", 60000, 100),
]
SETTINGS = {110, 111, 120, 121, 398}


def connect(port, limit=20.0):
    deadline = time.monotonic() + limit
    while True:
        try:
            ser = serial.Serial(port, bridge.BAUD, timeout=0.05)
            time.sleep(0.3)
            bridge.reset(ser)
            if bridge.state(ser) == "Alarm":
                bridge.command(ser, b"$X\n")
            return ser
        except (serial.SerialException, bridge.HardwareError):
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


def reboot(ser, port):
    ser.write(b"$REBOOT\n")
    time.sleep(0.2)
    ser.close()
    time.sleep(2.0)    # let USB CDC drop and re-enumerate
    return connect(port)


def board_info(ser):
    ser.write(b"$I\n")
    out = bridge.read_quiet(ser, 0.3, 3)
    m = re.search(rb"\[OPT:[^,\]]*,(\d+),(\d+)", out)
    mem = re.search(rb"FREE MEMORY:(\d+)K", out)
    return (int(m.group(1)) if m else None,
            int(mem.group(1)) if mem else None)


def steady_rate(ser, count, skip):
    lines = lr.moves(count, "same")
    outstanding = deque()
    outstanding_bytes = sent = acked = errors = 0
    ack_t = []
    inbuf = b""

    while acked < count:
        while sent < count and outstanding_bytes + len(lines[sent]) <= lr.MAX_OUTSTANDING:
            ser.write(lines[sent])
            outstanding.append(len(lines[sent]))
            outstanding_bytes += len(lines[sent])
            sent += 1
        chunk = ser.read(ser.in_waiting or 1)
        if not chunk:
            continue
        inbuf += chunk
        while b"\n" in inbuf:
            resp, inbuf = inbuf.split(b"\n", 1)
            resp = resp.strip(b"\r")
            if resp == b"ok" or resp.startswith(b"error"):
                if resp != b"ok":
                    errors += 1
                acked += 1
                ack_t.append(time.monotonic())
                outstanding_bytes -= outstanding.popleft()

    return (count - 1 - skip) / (ack_t[-1] - ack_t[skip]), errors


def run_one(ser, count, depth):
    bridge.command(ser, b"G90 G53 G0 X0 Y0 Z0\n")
    bridge.wait_idle(ser, 60)
    bridge.command(ser, b"G91 G1 F%d\n" % lr.FEED)
    rate, errors = steady_rate(ser, count, depth + 200)
    bridge.wait_idle(ser, 120)
    ser.write(b"?")
    _, m = bridge.read_until(ser, re.compile(rb"<[A-Za-z]+\|MPos:(-?\d+\.\d+)"), 3)
    final_x = float(m.group(1))
    bridge.command(ser, b"G90\n")
    bridge.command(ser, b"G53 G0 X0 Y0 Z0\n")
    bridge.wait_idle(ser, 60)
    return rate, errors, final_x


def main():
    if len(sys.argv) not in (2, 3):
        sys.stderr.write("usage: hw_planner_sweep.py <port> [count]\n")
        return 2
    port = sys.argv[1]
    count = int(sys.argv[2]) if len(sys.argv) == 3 else 5000
    expect_x = lr.expected_x(count, "same")
    results = []

    ser = connect(port)
    saved = lr.get_settings(ser, SETTINGS)
    print("original settings: %s" % saved, flush=True)
    try:
        for depth in DEPTHS:
            bridge.command(ser, b"$398=%d\n" % depth)
            ser = reboot(ser, port)
            actual, free_k = board_info(ser)
            for name, max_rate, accel in PROFILES:
                for n in (110, 111):
                    bridge.command(ser, b"$%d=%d\n" % (n, max_rate))
                for n in (120, 121):
                    bridge.command(ser, b"$%d=%d\n" % (n, accel))
                rate, errors, final_x = run_one(ser, count, actual or depth)
                row = (depth, actual, free_k, name, rate, errors, final_x)
                results.append(row)
                print("$398=%-4d allocated=%-4s free=%sK  %-7s  %7.1f lines/s  errors=%d  X=%.4f"
                      % row, flush=True)
    finally:
        for num, val in saved.items():
            bridge.command(ser, b"$%d=%s\n" % (num, val.encode()))
        ser = reboot(ser, port)
        actual, free_k = board_info(ser)
        print("restored %s; planner blocks now %s, free %sK" % (saved, actual, free_k))
        ser.close()

    bad = [r for r in results if r[5] or abs(r[6] - expect_x) >= 0.0005]
    if bad:
        print("\nposition/error mismatches (want X=%.4f):" % expect_x)
        for r in bad:
            print("  $398=%d %s: errors=%d X=%.4f" % (r[0], r[3], r[5], r[6]))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
