#!/usr/bin/env python3
#
#  hw_file_rate.py - G-code line rate when running from a file, on real hardware
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
#

"""Measure lines/sec when G-code runs from a file on the controller ($F=).

    hw_file_rate.py <port> [count] [max_rate] [accel] [blocks]

The file-run counterpart of hw_line_rate.py's "same" profile: `count` lines of
X0.01, all in the same direction, so the planner's look-ahead keeps one
continuous cruise and the rate reflects per-line foreground cost rather than
acceleration. Needs a LITTLEFS_ENABLE=2 build. The file is uploaded by YModem,
run with $F=, and deleted afterwards.

max_rate (mm/min), accel (mm/s^2) and blocks ($398, planner blocks) are
optional; when given, they are set for the run and restored afterwards.

Two numbers are reported:

  * overall: count / time from the $F= ok to the program end message, which
    includes accelerating at the start and draining the planner at the end;
  * steady state: the X velocity over the middle of the run, from status
    reports every 100 ms, divided by 0.01 mm per line. When the planner is
    starved (Bf: planner free stays high), that is the rate lines are read,
    parsed and planned; when it is full, motion ($110/$120) is the limit.
"""

import os
import re
import sys
import time

import serial

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serial_bridge as bridge                  # noqa: E402
from hw_file_run import ymodem_send, line       # noqa: E402
from hw_line_rate import FEED, expected_x, get_settings    # noqa: E402

NAME = "/hw_rate.nc"
STATUS = re.compile(rb"<([A-Za-z]+)\|MPos:(-?\d+\.\d+),[^|>]*(?:\|Bf:(\d+),(\d+))?")


def main():
    if not 2 <= len(sys.argv) <= 6:
        sys.stderr.write("usage: hw_file_rate.py <port> [count] [max_rate] [accel] [blocks]\n")
        return 2
    count = int(sys.argv[2]) if len(sys.argv) >= 3 else 5000
    max_rate = int(sys.argv[3]) if len(sys.argv) >= 4 else None
    accel = int(sys.argv[4]) if len(sys.argv) >= 5 else None
    blocks = int(sys.argv[5]) if len(sys.argv) >= 6 else None

    text = b"G91 G1 F%d\n" % FEED + b"X0.01\n" * count + b"G90\nM30\n"

    with serial.Serial(sys.argv[1], bridge.BAUD, timeout=0.05) as ser:
        time.sleep(0.3)
        bridge.reset(ser)
        if bridge.state(ser) == "Alarm":
            bridge.command(ser, b"$X\n")
        bridge.command(ser, b"G90 G53 G0 X0 Y0 Z0\n")
        bridge.wait_idle(ser, 60)

        saved = get_settings(ser, {110, 111, 120, 121, 398})
        try:
            if max_rate is not None:
                bridge.command(ser, b"$110=%d\n" % max_rate)
                bridge.command(ser, b"$111=%d\n" % max_rate)
            if accel is not None:
                bridge.command(ser, b"$120=%d\n" % accel)
                bridge.command(ser, b"$121=%d\n" % accel)
            if blocks is not None:
                bridge.command(ser, b"$398=%d\n" % blocks)
                bridge.reset(ser)    # planner size takes effect on reset
            active = get_settings(ser, {110, 120, 398})

            t0 = time.monotonic()
            ymodem_send(ser, NAME, text)
            upload = time.monotonic() - t0

            ser.write(b"$F=%s\n" % NAME.encode())
            buf, m = bridge.read_until(ser, bridge.REPLY, 5.0)
            if m.group(1) != b"ok":
                raise bridge.HardwareError("$F= rejected with %s" % m.group(1).decode())
            t_start = time.monotonic()
            buf = buf[m.end():]

            samples = []    # (time, x, planner blocks free)
            t_end = None
            next_poll = t_start
            while t_end is None and time.monotonic() - t_start < 300:
                now = time.monotonic()
                if now >= next_poll:
                    ser.write(b"?")
                    next_poll = now + 0.1
                chunk = ser.read(ser.in_waiting or 1)
                if not chunk:
                    continue
                buf += chunk
                if b"Pgm End" in buf or b"in SD file" in buf:
                    t_end = time.monotonic()
                    break
                done = buf.rfind(b">") + 1    # only parse complete status reports, once each
                for s in STATUS.finditer(buf[:done]):
                    samples.append((time.monotonic(), float(s.group(2)),
                                    int(s.group(3)) if s.group(3) else None))
                buf = buf[done:]
            if t_end is None:
                raise bridge.HardwareError("no program end after 300s")
            if b"in SD file" in buf:
                raise bridge.HardwareError("file run failed: %r" % buf[-120:])

            bridge.wait_idle(ser, 60)
            ser.write(b"?")
            _, m = bridge.read_until(ser, STATUS, 3)
            final_x = float(m.group(2))

            line(ser, b"$FD=%s\n" % NAME.encode())
            bridge.command(ser, b"G90 G53 G0 X0 Y0 Z0\n")
            bridge.wait_idle(ser, 60)
        finally:
            for num, val in saved.items():
                bridge.command(ser, b"$%d=%s\n" % (num, val.encode()))
            if blocks is not None:
                bridge.reset(ser)

    elapsed = t_end - t_start
    # steady state: middle 60% of the run by time
    mid = [s for s in samples if t_start + 0.2 * elapsed <= s[0] <= t_start + 0.8 * elapsed]
    steady = None
    if len(mid) >= 2:
        steady = (mid[-1][1] - mid[0][1]) / (mid[-1][0] - mid[0][0]) / 0.01
    free = [s[2] for s in mid if s[2] is not None]

    print("settings: $110=%s mm/min  $120=%s mm/s^2  $398=%s blocks"
          % (active.get(110), active.get(120), active.get(398)))
    print("upload  : %d bytes in %.2fs" % (len(text), upload))
    print("overall : %d lines in %.2fs -> %.0f lines/s (includes accel and final drain)"
          % (count, elapsed, count / elapsed))
    if steady is not None:
        print("steady  : %.0f lines/s (X velocity over the middle 60%%, %d status samples)"
              % (steady, len(mid)))
    if free:
        print("planner blocks free, middle of run: min %d  max %d" % (min(free), max(free)))
    want = expected_x(count, "same")
    print("final X : %.4f (want %.4f)" % (final_x, want))

    return 0 if abs(final_x - want) < 0.0005 else 1


if __name__ == "__main__":
    sys.exit(main())
