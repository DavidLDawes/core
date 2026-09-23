#!/usr/bin/env python3
#
#  hw_line_rate.py - measure sustained G-code line throughput, on real hardware
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

"""Measure how many G-code lines/sec the board actually sustains.

    hw_line_rate.py <port> [count] [profile] [max_rate] [accel]

    profile: "same" (default) - every line moves +0.01mm in the same direction,
             so the planner's look-ahead merges consecutive short blocks into one
             continuous cruise instead of re-accelerating from zero each line.
             "alt" - alternating +0.01/-0.01mm, reversing direction every line
             (the hw_output_commands.py profile), which never reaches cruise.
    max_rate, accel: optional, mm/min and mm/s^2. When given, $110/$111 and/or
             $120/$121 are temporarily raised to these values for the duration of
             the run (to find the point past which motion stops being the limit)
             and restored to whatever the board had before, once done - including
             on error. The commanded feed word is always far above any max_rate
             tested, so $110/$111 is always the binding cruise-speed cap, not F.

"Lines per second" is not one number - it depends on how the sender talks to
the board, and on whether the motion itself is the bottleneck. This measures
both ends people mean when they ask the question:

  1. naive send-and-wait: write one line, block for its ok/error, write the
     next. Bound by USB round-trip latency, not by the firmware. This is what
     test/hw_output_commands.py's line() helper does, and its own docstring
     already calls this "mostly USB round trips" - included here to quantify
     that, not to represent firmware capability.

  2. streamed (character-counting protocol): keep RX_BUFFER_SIZE (stream.h)
     bytes of unacked line data in flight, exactly like a real sender (UGS,
     bCNC, ...) does, and read ok/error responses as they arrive rather than
     waiting for each one. This is bound by gc_execute_line()/plan_buffer_line()
     foreground cost and, once segments start finishing faster than they can be
     re-filled, by stepper ISR consumption - i.e. it is the number that matters
     for "can this sender keep the board fed".

With profile "alt", (2) turned out to be bound by the motion's own kinematics:
each move fully decelerates to zero and re-accelerates, so its duration is set
by $120/$121 (acceleration), not by parsing or USB. Profile "same" removes that
by keeping every move in the same direction, letting the planner's look-ahead
build one continuous cruise across all of them - so the measured rate reflects
gc_execute_line()/plan_buffer_line() foreground cost and RX buffer size, i.e.
the number a real sender running normal (non-reversing) G-code would see as
its ceiling. Needs nothing attached; travels count * 0.01mm and returns to
X0 Y0 Z0 afterward.
"""

import os
import re
import struct
import sys
import time
from collections import deque

import serial

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serial_bridge as bridge    # noqa: E402

RX_BUFFER_SIZE = 1024    # stream.h default; must not exceed the real board's value
SAFETY_MARGIN = 32       # stay clear of the edge rather than prove it exactly
MAX_OUTSTANDING = RX_BUFFER_SIZE - SAFETY_MARGIN

FEED = 1000000    # mm/min - deliberately absurd, so $110/$111 always does the clamping


def get_settings(ser, numbers):
    ser.write(b"$$\n")
    out = bridge.read_quiet(ser, 0.3, 3)
    found = {}
    for line in out.decode(errors="replace").splitlines():
        m = re.match(r"\$(\d+)=([\d.]+)", line.strip())
        if m and int(m.group(1)) in numbers:
            found[int(m.group(1))] = m.group(2)
    return found


def expected_x(count, profile, steps_per_mm=250.0):
    """Where the board should end up. G91 targets accumulate in float32
    (plan_buffer_line() then lroundf()s the absolute target), so after enough
    +0.01 lines the true answer is a step off the exact decimal one."""
    if profile != "same":
        return 0.0
    f = lambda x: struct.unpack("f", struct.pack("f", x))[0]
    pos, inc = 0.0, f(0.01)
    for _ in range(count):
        pos = f(pos + inc)
    return round(f(pos * f(steps_per_mm))) / steps_per_mm


def moves(count, profile):
    if profile == "same":
        return [b"X0.01\n"] * count
    return [b"X%s\n" % (b"0.01" if i % 2 == 0 else b"-0.01") for i in range(count)]


def naive_rate(ser, count, profile):
    """Send-and-wait: one line, block for its ok, repeat."""
    lines = moves(count, profile)
    t0 = time.monotonic()
    for l in lines:
        bridge.command(ser, l)
    elapsed = time.monotonic() - t0
    return count / elapsed, elapsed


def streamed_rate(ser, count, profile):
    """Character-counting streaming: flow-controlled by RX_BUFFER_SIZE, acks
    read as they arrive rather than waited on one at a time."""
    lines = moves(count, profile)
    outstanding = deque()    # byte length of each line still unacked, in send order
    outstanding_bytes = 0
    sent = acked = errors = 0
    inbuf = b""
    t0 = time.monotonic()

    while acked < count:
        while sent < count and outstanding_bytes + len(lines[sent]) <= MAX_OUTSTANDING:
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
                outstanding_bytes -= outstanding.popleft()

    elapsed = time.monotonic() - t0
    return count / elapsed, elapsed, errors


def main():
    if len(sys.argv) not in (2, 3, 4, 5, 6):
        sys.stderr.write("usage: hw_line_rate.py <port> [count] [same|alt] [max_rate] [accel]\n")
        return 2
    count = int(sys.argv[2]) if len(sys.argv) >= 3 else 3000
    profile = sys.argv[3] if len(sys.argv) >= 4 else "same"
    max_rate = int(sys.argv[4]) if len(sys.argv) >= 5 else None
    accel = int(sys.argv[5]) if len(sys.argv) >= 6 else None
    naive_count = min(count, 200)    # naive is round-trip-bound; 200 is plenty to time it
    expect_x = expected_x(count, profile)

    with serial.Serial(sys.argv[1], bridge.BAUD, timeout=0.05) as ser:
        time.sleep(0.3)
        bridge.reset(ser)
        if bridge.state(ser) == "Alarm":
            bridge.command(ser, b"$X\n")
        bridge.command(ser, b"G90 G53 G0 X0 Y0 Z0\n")
        bridge.wait_idle(ser, 60)
        bridge.reset(ser)

        saved = {}
        try:
            if max_rate is not None or accel is not None:
                saved = get_settings(ser, {110, 111, 120, 121})
            if max_rate is not None:
                bridge.command(ser, b"$110=%d\n" % max_rate)
                bridge.command(ser, b"$111=%d\n" % max_rate)
            if accel is not None:
                bridge.command(ser, b"$120=%d\n" % accel)
                bridge.command(ser, b"$121=%d\n" % accel)

            bridge.command(ser, b"G91 G1 F%d\n" % FEED)

            rate1, t1 = naive_rate(ser, naive_count, profile)
            bridge.wait_idle(ser, 60)
            if profile == "same":
                bridge.command(ser, b"G90 G53 G0 X0 Y0 Z0\n")
                bridge.wait_idle(ser, 60)
                bridge.command(ser, b"G91 G1 F%d\n" % FEED)

            rate2, t2, errors = streamed_rate(ser, count, profile)
            wait_t0 = time.monotonic()
            bridge.wait_idle(ser, 120)
            drain = time.monotonic() - wait_t0

            ser.write(b"?")
            _, m = bridge.read_until(ser, re.compile(rb"<[A-Za-z]+\|MPos:(-?\d+\.\d+)"), 3)
            final_x = float(m.group(1))

            bridge.command(ser, b"G90\n")
            bridge.command(ser, b"G53 G0 X0 Y0 Z0\n")
            bridge.wait_idle(ser, 60)
        finally:
            for num, val in saved.items():
                bridge.command(ser, b"$%d=%s\n" % (num, val.encode()))

    print("profile: %s   max_rate override: %s   accel override: %s"
          % (profile, max_rate, accel))
    print("naive send-and-wait : %d lines in %.2fs -> %.1f lines/s"
          % (naive_count, t1, rate1))
    print("streamed (RX-buffer flow control, %d bytes) : %d lines in %.2fs -> %.1f lines/s"
          % (MAX_OUTSTANDING, count, t2, rate2))
    print("  + %.2fs after last ok for queued motion to finish (planner/segment drain)"
          % drain)
    print("errors during streamed run : %d" % errors)
    print("final X after %d '%s' moves: %.4f (want %.4f)" % (count, profile, final_x, expect_x))
    if saved:
        print("settings restored: %s" % saved)

    return 0 if errors == 0 and abs(final_x - expect_x) < 0.0005 else 1


if __name__ == "__main__":
    sys.exit(main())
