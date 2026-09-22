#!/usr/bin/env python3
#
#  hw_output_commands.py - motion-synchronized output commands, on real hardware
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

"""Exercise M62/M63 output commands that execute with motion, on a board.

    hw_output_commands.py <port> [count]

M62-M65 queue an "output command" that the stepper ISR executes when the next
move starts. The list is allocated by the parser, handed to the planner, and
copied into a stepper block by st_prep_buffer(). Before the fix the planner
block kept ownership: plan_discard_current_block() freed the list as soon as the
block was prepped - while the ISR had yet to execute it - so the ISR walked
freed memory. On an RP2040 that ended in a HardFault inside the stepper ISR,
reliably, within a few thousand moves each carrying one output command.

Three checks:

  1. the output really does switch, and only when the move carrying it runs
     (catches a "fix" that simply stops executing output commands);
  2. `count` moves, each carrying an M62, with the controller still responding
     afterwards - the step that faulted before the fix;
  3. free heap unchanged afterwards (catches the fix leaking the lists instead).

Uses aux output 0 and small X moves; needs nothing attached. Defaults to 3000
moves, which takes a few minutes, mostly USB round trips.
"""

import os
import re
import sys
import time

import serial

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serial_bridge as bridge    # noqa: E402


def line(ser, s, timeout=30.0):
    ser.write(s)
    buf, m = bridge.read_until(ser, bridge.REPLY, timeout)
    return buf.decode(errors="replace"), m.group(1).decode()


def dout0(ser):
    out, _ = line(ser, b"$PINSTATE\n")
    for l in out.splitlines():
        if l.startswith("[PINSTATE:DOUT|P0|"):
            return l.strip().rstrip("]").split("|")[-1]
    return None


def free_k(ser):
    out, _ = line(ser, b"$I\n")
    m = re.search(r"FREE MEMORY:(\d+)K", out)
    return int(m.group(1)) if m else None


def main():
    if len(sys.argv) not in (2, 3):
        sys.stderr.write("usage: hw_output_commands.py <port> [count]\n")
        return 2
    count = int(sys.argv[2]) if len(sys.argv) == 3 else 3000
    checks = []

    with serial.Serial(sys.argv[1], bridge.BAUD, timeout=0.05) as ser:
        time.sleep(0.3)
        bridge.reset(ser)
        if bridge.state(ser) == "Alarm":
            line(ser, b"$X\n")
        line(ser, b"G90 G53 G0 X0 Y0 Z0\n")
        bridge.wait_idle(ser, 60)

        # 1. the output switches, and only with the move that carries it
        line(ser, b"M65 P0\n")
        off = dout0(ser)
        line(ser, b"M62 P0\n")
        queued = dout0(ser)
        line(ser, b"G91 G1 X1 F300\n")
        bridge.wait_idle(ser, 30)
        on = dout0(ser)
        line(ser, b"M63 P0\n")
        line(ser, b"G91 G1 X-1 F300\n")
        bridge.wait_idle(ser, 30)
        off_again = dout0(ser)
        print("aux out 0: M65 %s, M62 queued %s, after move %s, M63 + move %s"
              % (off, queued, on, off_again))
        checks.append(("output switches only with its move",
                       (off, queued, on, off_again) == ("0", "0", "1", "0")))

        # 2. many moves each carrying an output command
        before = free_k(ser)
        line(ser, b"G91 G1 F6000\n")
        done, t0 = 0, time.monotonic()
        try:
            for i in range(count):
                line(ser, b"M62 P0\n")
                line(ser, b"X%s\n" % (b"0.01" if i % 2 == 0 else b"-0.01"))
                done = i + 1
            bridge.wait_idle(ser, 120)
            alive = True
        except bridge.HardwareError as e:
            alive = False
            print("controller stopped responding after %d of %d moves: %s" % (done, count, e))
        print("%d moves carrying an output command in %.0fs" % (done, time.monotonic() - t0))
        checks.append(("controller responsive after %d moves" % count, alive and done == count))

        # 3. no leak
        if alive:
            line(ser, b"G90\n")
            after = free_k(ser)
            print("free heap before / after: %sK / %sK" % (before, after))
            checks.append(("free heap unchanged", before is not None and after == before))

    print()
    for name, ok in checks:
        print("  %-5s %s" % ("ok" if ok else "FAIL", name))
    return 0 if checks and all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
