#!/usr/bin/env bash
#
# Regression tests for the grblHAL core, run against the host simulator or
# against real hardware over a serial port. The same cases and assertions are
# used for both.
#
#   ./run_tests.sh [path-to-grbl_sim]
#   PYTHON=python3 ./run_tests.sh --serial <port>     e.g. COM6, /dev/ttyACM0
#
# Each case starts from a fresh controller. For the simulator that is a fresh
# process; on hardware serial_bridge.py soft-resets the board and returns it to
# the origin first. That is deliberate: with COMPATIBILITY_LEVEL 0 the core
# stops parsing after a failed block and simply repeats the last error, so cases
# sharing state would contaminate each other.

set -uo pipefail

if [ "${1:-}" = "--serial" ]; then
    port="${2:?usage: run_tests.sh --serial <port>}"
    TARGET=("${PYTHON:-python3}" "$(dirname "$0")/serial_bridge.py" "$port")
    LABEL="hardware on $port"
    # Real moves take real time, and each case resets and re-homes to the origin.
    TIMEOUT="${TIMEOUT:-90}"
else
    SIM="${1:-build/grbl_sim}"
    if [ ! -x "$SIM" ]; then
        echo "run_tests.sh: no simulator at '$SIM'" >&2
        echo "build it with: cmake -G Ninja -S . -B build && ninja -C build" >&2
        exit 2
    fi
    TARGET=("$SIM")
    LABEL="$SIM"
    TIMEOUT="${TIMEOUT:-20}"
fi

pass=0
fail=0

# check <name> <input> <expected-regex> [more-regexes...]
check () {
    local name="$1" input="$2"; shift 2
    local out rc problem=""

    out=$(printf '%b' "$input" | timeout "$TIMEOUT" "${TARGET[@]}" 2>&1)
    rc=$?

    if [ $rc -eq 124 ]; then
        problem="timed out after ${TIMEOUT}s"
    elif [ $rc -ne 0 ]; then
        problem="exit code $rc"
    else
        local re
        for re in "$@"; do
            if ! printf '%s' "$out" | grep -qE -- "$re"; then
                problem="missing /$re/"
                break
            fi
        done
    fi

    if [ -z "$problem" ]; then
        printf '  ok    %s\n' "$name"
        pass=$((pass + 1))
    else
        printf '  FAIL  %s (%s)\n' "$name" "$problem"
        printf '%s\n' "$out" | sed 's/^/          | /'
        fail=$((fail + 1))
    fi
}

# check_absent <name> <input> <regex-that-must-not-appear>
check_absent () {
    local name="$1" input="$2" re="$3"
    local out rc

    out=$(printf '%b' "$input" | timeout "$TIMEOUT" "${TARGET[@]}" 2>&1)
    rc=$?

    if [ $rc -eq 124 ]; then
        printf '  FAIL  %s (timed out after %ss)\n' "$name" "$TIMEOUT"
        fail=$((fail + 1))
    elif printf '%s' "$out" | grep -qE -- "$re"; then
        printf '  FAIL  %s (unexpected /%s/)\n' "$name" "$re"
        printf '%s\n' "$out" | sed 's/^/          | /'
        fail=$((fail + 1))
    else
        printf '  ok    %s\n' "$name"
        pass=$((pass + 1))
    fi
}

echo "grblHAL core regression tests ($LABEL)"
echo

echo "startup"
check        "banner is printed"            ""  "GrblHAL .* for help"
check_absent "boots without an alarm"       ""  "ALARM:"
check_absent "boots without an error"       ""  "error:"

echo
echo "g-code parsing"
check "rapid is accepted"                   "G0X10Y5\n"                 "^ok"
check "feed move with F is accepted"        "G1X10F100\n"               "^ok"
check "feed move without F is rejected"     "G1X10\n"                   "error:22"
check "several blocks all accepted"         "G21\nG90\nG0X1\nG0Y2\n"    "ok" "ok" "ok" "ok"
check "arc is accepted"                     "G17\nG2X10Y0I5J0F100\n"    "^ok"

echo
echo "modal state"
check "G21/G90 are reported by \$G"         "G21\nG90\n\$G\n"           "\[GC:.*G21.*G90"
check "feed rate is retained"               "G1X1F250\n\$G\n"           "\[GC:.*F250"
check "G20 switches to inches"              "G20\n\$G\n"                "\[GC:.*G20"

echo
echo "reporting"
check "status report responds"              "?\n"                       "<Idle\|MPos:0.000,0.000,0.000"
check "settings dump includes \$0"          "\$\$\n"                    "^\\\$0="
check "settings dump includes axis steps"   "\$\$\n"                    "^\\\$100="
check "build info responds"                 "\$I\n"                     "\[VER:"

# setting_get_description() rebuilds per-axis descriptions through a realloc'd
# static buffer. These guard the rewrite of that failure path (PLAN.md 2.1(1))
# against breaking the ordinary path.
echo
echo "setting descriptions"
check "description for \$100"               "\$SED=100\n"               "\[SETTINGDESCR:100\|"
check "description for \$101"               "\$SED=101\n"               "\[SETTINGDESCR:101\|"
check "description for \$102"               "\$SED=102\n"               "\[SETTINGDESCR:102\|"
check "description for a non-axis setting"  "\$SED=110\n"               "\[SETTINGDESCR:110\|"

# M62-M65 queue "output commands" to be executed with the next move. Before the
# fix, a soft reset (or a parse error) freed that queue but left the parser's
# pointer to it dangling. The next M6x then reused the freed block and linked it
# to itself, and the one after that looped forever in the foreground - taking
# realtime commands and soft reset with it. Both cases hung (or double-freed)
# before the fix. The blank line after the ctrl-X (\0030) is sacrificial: a reset
# that arrives mid-stream discards the line being read when it takes effect.
echo
echo "synchronized outputs (M62-M65)"
check "M62-M65 accepted on aux outputs"         "M62 P0\nM63 P1\nM64 P2\nM65 P3\n"   "ok" "ok" "ok" "ok"
check "queued output survives a soft reset"     "M62 P0\n\0030\nM62 P0\nM62 P0\n\$G\n"          "\[GC:"
check "queued output survives a parse error"    "M62 P0\nG1 X1\n\0030\nM62 P0\nM62 P0\n\$G\n"   "error:22" "\[GC:"

# Aux port 3 in the simulator is flagged async (see sim_driver.c getPinInfo()) to stand in for a
# slow, e.g. I2C/Modbus-backed, aux output. M62/M63 run from the stepper ISR when their move
# starts (PLAN.md 2.2(10)) and must refuse to bind to such a port; M64/M65 run immediately, in
# the parser's own foreground context, so they're unaffected by the same port's async flag.
echo
echo "async-flagged aux port rejected for synced output (2.2(10))"
check "M62 on an async port is rejected"        "M62 P3\n"   "error:39"
check "M64 on the same async port still works"  "M64 P3\n"   "^ok"

echo
echo "shutdown"
check "exits cleanly on end of input"       ""                          "GrblHAL"
check "exits cleanly after g-code"          "G0X1\n"                    "^ok"

echo
printf '%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
