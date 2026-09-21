#!/usr/bin/env python3
#
#  hw_rt_stress.py - realtime command stress test during motion, on real hardware
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

"""Stress realtime command handling while the stepper interrupt is busy.

    hw_rt_stress.py <port>

Runs a 4 s move and, throughout it, sends a status query every ~5 ms. Each one
is handled by the driver's RX interrupt, which calls into the core's realtime
command intake and from there the HAL's atomic helpers - the code changed by
PLAN.md 4.1 - concurrently with the stepper interrupt. A feed hold and resume
are thrown in mid-move.

Passes if the hold completes, the resume is accepted, the move ends at exactly
the commanded position, no alarm or error is raised, and the controller is still
responsive afterwards.

What this does and does not show: it demonstrates the critical-section code is
not broken under realistic interrupt load. It does NOT demonstrate that the 4.1
fix was needed - that bug only bites when those calls nest inside an outer
critical section, which this traffic is unlikely to produce.

Note that cycle start is only accepted once a hold has *completed* (Hold:0).
Sent during deceleration (Hold:1) it is ignored by design, and the machine then
waits in hold indefinitely - so the resume here waits for Hold:0.
"""

import os
import re
import sys
import time

import serial

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serial_bridge as bridge    # noqa: E402

TARGET_X = 20.0
FEED = 300          # mm/min, so the move lasts ~4 s at the default acceleration
HOLD_AT = 1.5       # seconds into the move
LIMIT = 30.0


def main():
    if len(sys.argv) != 2:
        sys.stderr.write("usage: hw_rt_stress.py <port>\n")
        return 2

    with serial.Serial(sys.argv[1], bridge.BAUD, timeout=0.05) as ser:
        time.sleep(0.3)
        bridge.reset(ser)
        if bridge.state(ser) == "Alarm":
            bridge.command(ser, b"$X\n")
        bridge.command(ser, b"G90 G53 G0 X0 Y0 Z0\n")
        bridge.wait_idle(ser, 60)
        bridge.reset(ser)

        ser.write(b"G1 X%.3f F%d\n" % (TARGET_X, FEED))

        got, sent, held, resumed = b"", 0, None, None
        done = re.compile(rb"<Idle\|MPos:%.3f" % TARGET_X)
        t0 = time.monotonic()

        while time.monotonic() - t0 < LIMIT:
            ser.write(b"?")
            sent += 1
            got += ser.read(ser.in_waiting or 0)
            t = time.monotonic() - t0
            if held is None and t > HOLD_AT:
                ser.write(b"!")
                held = t
            if held is not None and resumed is None and b"Hold:0" in got:
                ser.write(b"~")
                resumed = t
            if resumed is not None and done.search(got):
                break
            time.sleep(0.005)

        elapsed = time.monotonic() - t0
        got += bridge.read_quiet(ser, 0.5, 5)
        ser.write(b"$I\n")
        info = bridge.read_quiet(ser, 0.5, 5)

    states = [s.decode() for s in re.findall(rb"<([A-Za-z]+(?::\d)?)", got)]
    sequence = [s for i, s in enumerate(states) if i == 0 or s != states[i - 1]]
    reports = re.findall(rb"<[^>]*>", got)
    final = reports[-1].decode() if reports else "none"
    faults = re.findall(rb"ALARM:\d+|error:\d+", got)

    checks = [
        ("hold completed (Hold:0 seen)", "Hold:0" in states),
        ("resume accepted", resumed is not None),
        ("ended at X=%.3f and Idle" % TARGET_X, bool(done.search(got))),
        ("no alarm or error", not faults),
        ("responsive afterwards ($I)", b"[FIRMWARE:grblHAL]" in info),
    ]

    print("status queries sent     : %d over %.1fs" % (sent, elapsed))
    print("status reports received : %d" % len(states))
    print("state sequence          : %s" % " -> ".join(sequence))
    print("hold / resume at        : %s / %s" % (
        "%.2fs" % held if held is not None else "never",
        "%.2fs" % resumed if resumed is not None else "never"))
    print("final report            : %s" % final)
    if faults:
        print("faults                  : %s" % b" ".join(faults).decode())
    print()

    for name, ok in checks:
        print("  %-5s %s" % ("ok" if ok else "FAIL", name))

    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
