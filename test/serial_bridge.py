#!/usr/bin/env python3
#
#  serial_bridge.py - run one regression case against grblHAL on real hardware
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

"""Run one regression case against grblHAL on a board, over a serial port.

    serial_bridge.py <port> < case-input

Behaves like the host simulator as far as run_tests.sh is concerned: reads the
case from stdin, writes what the controller said to stdout, exits 0. That is what
lets the same cases and assertions run against both.

The simulator gets a fresh controller for free, because every case is a new
process. A board does not, so before each case this silently:

  1. soft-resets it (ctrl-X), which also clears the parser's error lockout;
  2. unlocks it with $X if it is in alarm;
  3. rapids back to machine zero and waits for Idle - machine position survives
     a soft reset, and cases such as the arc test depend on where they start;
  4. soft-resets again, so parser and modal state are exactly power-on default.

The case is then streamed the way a real sender does it - see stream() - and
only the banner from the final reset plus the response to the case are written
out. After the case it waits for any motion to finish, so the next
case's reset never lands mid-move (which would raise an alarm).

Needs pyserial. Set BRIDGE_VERBOSE=1 to log setup steps to stderr - note that
run_tests.sh merges stderr into what it asserts on.
"""

import os
import re
import sys
import time

import serial

BAUD = 115200    # ignored by native USB CDC, but the API wants one
CAN = b"\x18"    # ctrl-X, soft reset

BANNER = re.compile(rb"GrblHAL [^\r\n]*for help\]")
STATUS = re.compile(rb"<([A-Za-z]+)[|:>]")
REPLY = re.compile(rb"^(ok|error:\d+)", re.M)

# Bytes the controller treats as realtime commands (plus 0x80-0xBF).
REALTIME = {CAN, b"?", b"!", b"~"}

# States in which the machine is still moving and must not be reset.
MOVING = {"Run", "Jog", "Home"}

VERBOSE = bool(os.environ.get("BRIDGE_VERBOSE"))


class HardwareError(Exception):
    pass


def log(msg):
    if VERBOSE:
        sys.stderr.write("serial_bridge: %s\n" % msg)


def read_until(ser, pattern, timeout):
    """Read until pattern matches the data received during this call."""
    buf = b""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            buf += chunk
            m = pattern.search(buf)
            if m:
                return buf, m
    raise HardwareError("timed out waiting for %r, last received %r"
                        % (pattern.pattern, buf[-120:]))


def read_quiet(ser, quiet, limit):
    """Read until nothing has arrived for `quiet` seconds, or `limit` elapses."""
    buf = b""
    start = last = time.monotonic()
    while True:
        chunk = ser.read(ser.in_waiting or 1)
        now = time.monotonic()
        if chunk:
            buf += chunk
            last = now
        elif now - last >= quiet or now - start >= limit:
            return buf


def reset(ser):
    """Soft-reset and return everything from the banner onward."""
    ser.reset_input_buffer()
    ser.write(CAN)
    buf, m = read_until(ser, BANNER, 5.0)
    return buf[m.start():] + read_quiet(ser, 0.3, 2.0)


def state(ser):
    ser.write(b"?")
    _, m = read_until(ser, STATUS, 3.0)
    read_quiet(ser, 0.1, 0.5)    # swallow the rest of the report
    return m.group(1).decode()


def command(ser, line):
    ser.write(line)
    _, m = read_until(ser, REPLY, 5.0)
    if m.group(1) != b"ok":
        raise HardwareError("%r was rejected with %s" % (line, m.group(1).decode()))


def wait_idle(ser, limit):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        st = state(ser)
        if st not in MOVING:
            return st
        time.sleep(0.1)
    raise HardwareError("machine still moving after %ss" % limit)


def tokens(case):
    """Split a case into realtime bytes and newline-terminated lines.

    A realtime byte is only recognised at the start of a line, which is all the
    test cases need and avoids mistaking one inside a comment for a command.
    """
    line = b""
    for i in range(len(case)):
        c = case[i:i + 1]
        if not line and (c in REALTIME or 0x80 <= c[0] <= 0xBF):
            yield "rt", c
        else:
            line += c
            if c == b"\n":
                yield "line", line
                line = b""
    if line:
        yield "line", line


def stream(ser, case):
    """Send a case the way a real sender does, and return everything received.

    Writing the whole case at once is not equivalent: the board handles realtime
    bytes in its receive interrupt the moment they arrive, and a soft reset then
    flushes the receive buffer - so lines sent before a ctrl-X but not yet parsed
    are silently dropped, and those after it arrive mangled. That is correct
    firmware behaviour, and exactly why senders use the send-response protocol:
    wait for each line's ok/error, and for the banner after a reset.
    """
    out = b""
    for kind, data in tokens(case):
        ser.write(data)
        if kind == "line":
            buf, _ = read_until(ser, REPLY, 30.0)
            out += buf
        elif data == CAN:
            buf, _ = read_until(ser, BANNER, 5.0)
            out += buf
        # other realtime bytes (e.g. '?') are answered asynchronously; their
        # output is picked up by the next read or the final read_quiet()
    return out + read_quiet(ser, 0.5, 5.0)


def main():
    if len(sys.argv) != 2:
        sys.stderr.write("usage: serial_bridge.py <port> < case-input\n")
        return 2

    case = sys.stdin.buffer.read()

    try:
        with serial.Serial(sys.argv[1], BAUD, timeout=0.05) as ser:

            reset(ser)
            if state(ser) == "Alarm":
                log("unlocking")
                command(ser, b"$X\n")

            log("returning to machine zero")
            command(ser, b"G90 G53 G0 X0 Y0 Z0\n")
            wait_idle(ser, 60)

            preamble = reset(ser)

            output = stream(ser, case) if case else b""

            log("waiting for motion to finish")
            wait_idle(ser, 120)

    except (serial.SerialException, HardwareError) as e:
        sys.stderr.write("serial_bridge: %s\n" % e)
        return 3

    sys.stdout.buffer.write(preamble + output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
