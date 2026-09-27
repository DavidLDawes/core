#!/usr/bin/env python3
#
#  hw_file_run.py - upload a G-code file to littlefs and run it, on real hardware
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

"""Run G-code from a file stored on the controller, on a board.

    hw_file_run.py <port>

Needs a build with LITTLEFS_ENABLE=2 (littlefs in onboard flash, mounted as /),
which brings in the file stream plugin ($F commands) and YModem upload. The file
is uploaded with YModem over the same USB stream grblHAL uses for G-code, then:

  1. `$F` lists it and `$F<=` reads back exactly what was uploaded;
  2. `$F=` runs it: the machine ends at the position the file's moves add up to
     and reports program end;
  3. an error inside a file aborts the job, is reported with its line number,
     and hands control back to the serial stream, where the usual error lock
     applies: G-code is refused until a $ command clears it;
  4. `$FD=` deletes the files.

Moves are small relative X/Y/Z moves; needs nothing attached.
"""

import binascii
import os
import re
import sys
import time

import serial

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serial_bridge as bridge    # noqa: E402

SOH, EOT, ACK, NAK, CAN = b"\x01", b"\x04", b"\x06", b"\x15", b"\x18"

JOB = "/hw_test_job.nc"
JOB_TEXT = (
    "G21 G91\n"
    "G1 X1 Y2 F1000\n"
    "G1 Z-0.5\n"
    "G1 X-0.25\n"
    "G90\n"
    "M30\n"
)
JOB_DELTA = (0.75, 2.0, -0.5)

BAD = "/hw_test_bad.nc"
BAD_TEXT = (
    "G21 G91\n"
    "G1 X0.5 F1000\n"
    "G999\n"            # unsupported command: must stop the job at line 3
    "G1 X0.5\n"
    "M30\n"
)

MPOS = re.compile(rb"<([A-Za-z]+)[^>]*MPos:(-?\d+\.\d+),(-?\d+\.\d+),(-?\d+\.\d+)")


def line(ser, s, timeout=10.0):
    ser.write(s)
    buf, m = bridge.read_until(ser, bridge.REPLY, timeout)
    return buf.decode(errors="replace"), m.group(1).decode()


def mpos(ser):
    ser.write(b"?")
    _, m = bridge.read_until(ser, MPOS, 3.0)
    bridge.read_quiet(ser, 0.1, 0.5)
    return tuple(float(v) for v in m.groups()[1:])


def expect(ser, want, timeout=3.0):
    got = ser.read(len(want))
    deadline = time.monotonic() + timeout
    while len(got) < len(want) and time.monotonic() < deadline:
        got += ser.read(len(want) - len(got))
    if got != want:
        raise bridge.HardwareError("YModem: wanted %r, got %r" % (want, got))


def packet(num, payload):
    payload = payload.ljust(128, b"\x1a" if num else b"\0")
    crc = binascii.crc_hqx(payload, 0)
    return SOH + bytes((num & 0xFF, 0xFF - (num & 0xFF))) + payload + crc.to_bytes(2, "big")


def ymodem_send(ser, name, data):
    """Send one file. grblHAL's receiver starts on the sender's first packet
    rather than sending the usual initial 'C'."""
    ser.reset_input_buffer()
    ser.write(packet(0, name.encode() + b"\0" + str(len(data)).encode() + b"\0"))
    expect(ser, ACK + b"C")
    for i in range(0, len(data), 128):
        ser.write(packet(i // 128 + 1, data[i:i + 128]))
        expect(ser, ACK)
    ser.write(EOT)
    expect(ser, ACK + b"C")
    ser.write(packet(0, b""))    # empty file name ends the batch
    expect(ser, ACK)


def run_file(ser, name, limit=30.0):
    """$F=<name>, then collect output until the job is over and the machine idle."""
    ser.write(b"$F=%s\n" % name.encode())
    buf, m = bridge.read_until(ser, bridge.REPLY, 5.0)
    if m.group(1) != b"ok":
        raise bridge.HardwareError("$F=%s was rejected with %s" % (name, m.group(1).decode()))
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        buf += bridge.read_quiet(ser, 0.2, 1.0)
        if b"Pgm End" in buf or b"in SD file" in buf:
            break
    bridge.wait_idle(ser, limit)
    return buf.decode(errors="replace")


def main():
    if len(sys.argv) != 2:
        sys.stderr.write("usage: hw_file_run.py <port>\n")
        return 2
    checks = []

    with serial.Serial(sys.argv[1], bridge.BAUD, timeout=0.05) as ser:
        time.sleep(0.3)
        bridge.reset(ser)
        bridge.command(ser, b"$X\n")

        out, reply = line(ser, b"$FI\n")
        print(out.strip())
        if reply != "ok":
            print("no file system: build with LITTLEFS_ENABLE=2")
            return 1

        # 1. upload, list, read back
        for name, text in ((JOB, JOB_TEXT), (BAD, BAD_TEXT)):
            ymodem_send(ser, name, text.encode())
        out, _ = line(ser, b"$F\n")
        checks.append(("uploaded files listed by $F",
                       JOB.lstrip("/") in out and BAD.lstrip("/") in out))
        out, _ = line(ser, b"$F<=%s\n" % JOB.encode())
        checks.append(("$F<= reads back the uploaded content",
                       out.replace("\r\n", "\n").startswith(JOB_TEXT)))

        # 2. run it
        before = mpos(ser)
        out = run_file(ser, JOB)
        after = mpos(ser)
        delta = tuple(round(a - b, 3) for a, b in zip(after, before))
        print("$F=%s moved %s (want %s)" % (JOB, delta, JOB_DELTA))
        checks.append(("job reported program end", "Pgm End" in out))
        checks.append(("job ended at the expected position",
                       all(abs(d - w) < 0.005 for d, w in zip(delta, JOB_DELTA))))

        # 3. error inside a file
        before = mpos(ser)
        out = run_file(ser, BAD)
        after = mpos(ser)
        err = re.search(r"error:(\d+) in SD file at line (\d+)", out)
        print("%s: %s, moved X %.3f" % (BAD, err.group(0) if err else "no error reported",
                                        after[0] - before[0]))
        checks.append(("file error reported with its line number",
                       err is not None and err.group(2) == "3"))
        checks.append(("job stopped at the bad line", abs(after[0] - before[0] - 0.5) < 0.005))
        # With COMPATIBILITY_LEVEL 0 the error locks out further G-code until a $ command
        # (or reset) clears it - same as after an error on a line sent over serial.
        _, locked = line(ser, b"G4 P0\n")
        _, cleared = line(ser, b"$I\n")
        _, reply = line(ser, b"G4 P0\n")
        checks.append(("serial stream takes commands again after the aborted job",
                       (locked, cleared, reply) == ("error:20", "ok", "ok")))

        # 4. delete
        for name in (JOB, BAD):
            line(ser, b"$FD=%s\n" % name.encode())
        out, _ = line(ser, b"$F\n")
        checks.append(("$FD= deletes the files",
                       JOB.lstrip("/") not in out and BAD.lstrip("/") not in out))

    print()
    for name, ok in checks:
        print("  %-5s %s" % ("ok" if ok else "FAIL", name))
    return 0 if checks and all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
