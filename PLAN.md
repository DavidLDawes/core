# grblHAL — build setup and review findings

Working notes covering the two repositories that are compiled together into one
firmware image:

| Repo | Fork we work in | Upstream |
|---|---|---|
| core (this repo) | `DavidLDawes/core` | `grblHAL/core` |
| RP2040/RP2350 driver | `DavidLDawes/RP2040` | `grblHAL/RP2040` |

**All changes are made on the `DavidLDawes` forks. Nothing is ever pushed to
`grblHAL/*`.** Upstream is fetch-only, and both local clones enforce this — the
`upstream` remote has its push URL set to a deliberately invalid value:

```bash
git remote set-url --push upstream DISABLED_no_push_to_upstream
```

so an accidental `git push upstream` fails instead of prompting for credentials.
Contributing anything back is a separate, deliberate decision (see Part 5).

**Branch convention: `main`.** Both forks were renamed from `master` to `main`
and `main` is the default branch on each. Upstream still uses `master`, so
syncing from upstream is explicit:

```bash
git fetch upstream
git merge upstream/master        # or: git rebase upstream/master
```

Changes reach `main` by pull request, with CI required on the PR.

* **Part 1** — getting a compiler working, and CI.
* **Part 2** — code review of the core.
* **Part 3** — code review of the RP2040 driver.
* **Part 4** — findings that only appear when the two are combined.
* **Part 5** — recommended order of work.

---

# Part 1 — Getting things compiling

## 1.0 First, an important clarification: RP2040 is not the Raspberry Pi

These are two different products with confusingly similar branding:

| | What it is | Runs |
|---|---|---|
| **Raspberry Pi** (3 / 4 / 5, Zero) | A single-board **computer**, ARM Cortex-A | Linux |
| **RP2040 / RP2350** | A **microcontroller chip**, dual Cortex-M0+ / M33 | Bare metal firmware |
| **Raspberry Pi Pico / Pico 2** | A small board carrying the RP2040 / RP2350 chip | Bare metal firmware |

The [grblHAL/RP2040](https://github.com/grblHAL/RP2040) driver targets the **RP2040/RP2350
microcontroller** — i.e. a **Pico or Pico 2 board**, not a Raspberry Pi SBC. grblHAL is
bare-metal firmware with a hard real-time stepper ISR; it does not run as a Linux process
on a Pi.

* **Raspberry Pi Pico 2** (~$5) — best default. Get the non-W version unless you want WiFi.
* **Pico 2 W** if you want networking.
* A ready-made RP2040/RP2350 CNC board, which saves you wiring level shifters and drivers:
  PicoBOB, PicoCNC, BTT SKR Pico, PicoHAL. These have board maps already in the driver
  (see `my_machine.h`, §1.3).

## 1.1 Status

Last updated 2026-09-22 (item 19: line-rate measurement and planner cache).

| # | Item | Status |
|---|---|---|
| 1 | Local cross-compilation toolchain (§1.2) | **done** |
| 2 | Clean build → `grblHAL.uf2` for RP2040 and Pico 2 (§1.3) | **done** |
| 3 | Working notes: `CLAUDE.md`, `README.md`, `PLAN.md` | **done**, merged in PR #1 |
| 4 | GitHub Actions build matrix on PRs and merges (§1.7) | **done**, all 6 jobs green |
| 5 | Review of the RP2040 driver (Part 3) | **done** |
| 6 | Review of the core + driver combination (Part 4) | **done** |
| 7 | Flash and run on real hardware (§1.4, §1.9) | **done** — RP2040 Pico; 21/21 on the board, stress test passes |
| 8 | Host-side build so the core can be tested without hardware (§1.8) | **done** — 21 tests, run in CI |
| 9 | Part 5 Step 1 — the two HIGH core safety fixes | **done** — §2.1(1) and §2.1(2) |
| 10 | Part 5 Step 2 — host simulator and regression suite (§1.8) | **done** |
| 11 | Part 5 Step 3 — interrupt-nesting contract (§4.1) | **done** — both repos |
| 12 | Part 5 Step 4 — run on hardware (§1.4, §1.9) | **done** |
| 13 | Critical hardware-only bug found + fixed: M62-M65 use-after-free (§2.1(3)) | **done** |
| 14 | Part 5 Step 5 — driver defects, all of Part 3 (§3.1(1-5), §3.2(6-7)) | **done** — DavidLDawes/RP2040#4 |
| 15 | Part 5 Step 6 — performance work, all four §2.3 items | **done** |
| 16 | Part 5 Step 7 — HIGH core bug fixed: late-registered settings-changed hooks (§2.1(7)) | **done** — confirmed on hardware via SWD breakpoint |
| 17 | Remaining code fixes from Parts 2–4 (§2.1(4-6), §2.2(10-12), §2.4) | **done** — 5 fixed, 1 investigated and closed as not-a-bug; fuzzing/Doxyfile/kinematics-CI in §2.4 deliberately left as future work |
| 18 | Hardware smoke test of item 17's fixes | **done** — built, flashed, full regression suite green (sim 26/26, hardware 24/24 + 2 sim-only) |
| 19 | Streaming line rate measured and profiled on hardware; planner per-block cost cut 30% (§2.3(5)) | **done** — 1639 → 1773 lines/s at the recalculation-bound plateau |

The toolchain is installed and a full clean build has been verified on this
machine, producing `play/RP2040/build/grblHAL.uf2`. `build.sh` now defaults to the
RP2040 (UF2 family `0xE48BFF56`) to match the bench board; `PICO_BOARD=pico2`
builds for a Pico 2 (`0xE48BFF59`, `rp2350-arm-s`).

**To build, from Git Bash:**

```bash
cd "/c/Users/David Lyman Dawes/play"
./build.sh            # incremental
./build.sh clean      # wipe build dir first
```

The firmware has been flashed and run on an RP2040 Pico (§1.4). The regression
suite passes on the board as well as in the simulator, and a realtime stress test
passes during motion (§1.9). Nothing has driven real motors or switches yet —
the board has nothing attached.

Everything below documents how that was set up and, more importantly, the three
things that went wrong so they can be recognised again.

## 1.2 What is installed and where

Everything is self-contained under `play/toolchain/`. Nothing was installed
machine-wide and nothing was added to the system PATH — deleting that one
directory removes the lot. `play/toolchain/env.sh` sets the environment;
`play/build.sh` sources it and builds.

| Component | Version | Location | How obtained |
|---|---|---|---|
| Arm GNU Toolchain (target) | 14.2.Rel1 | `toolchain/arm-gnu-14.2/` | portable zip from developer.arm.com |
| mingw-w64 GCC (host tools) | 13.1.0 UCRT | `toolchain/mingw-13.1/` | portable zip from WinLibs |
| CMake | 3.31.8 | `toolchain/cmake-3.31.8-windows-x86_64/` | portable zip from Kitware |
| Ninja | 1.13.2 | `toolchain/ninja.exe` | `winget install Ninja-build.Ninja` |
| Pico SDK | 2.1.1 | `toolchain/pico-sdk/` | `git clone -b 2.1.1 --recursive` |
| OpenOCD (RP2040/RP2350 SWD) | 0.12.0+dev | `toolchain/openocd/` | Raspberry Pi `pico-sdk-tools` release zip |
| Python venv with pyserial | 3.5 | `toolchain/venv/` | `python -m venv` + `pip install pyserial` |
| grblHAL RP2040 driver | main | `play/RP2040/` | `git clone --recursive` |

Portable zips were used over installers deliberately: no elevation, no UAC
prompts, exact version pinning, and trivial removal.

The VS Code **Raspberry Pi Pico** extension (`raspberry-pi.raspberry-pi-pico`)
is also installed and is a perfectly good alternative front end — it manages its
own copy of all of the above under `~/.pico-sdk`. It was not used for the
verified build.

### Version pinning — why these numbers

* **CMake 3.31.8, not 4.x.** Pico SDK 2.1.1 is tested against the 3.31 line
  (the VS Code extension ships 3.31.5). CMake 4 dropped compatibility with
  `cmake_minimum_required` below 3.5, and there is no reason to find out the
  hard way which SDK dependency trips over it.
* **SDK 2.1.1**, because that is what the grblHAL RP2040 driver README pins.
* **Host GCC 13.1**, see §1.5 — this one is not optional.

## 1.3 Board configuration

Two files decide what gets built:

* **Board selection is passed on the command line**, not edited into the driver's
  tracked `CMakeLists.txt` — that file keeps its upstream default of `pico` so the
  working tree never diverges from the fork (Part 3 §3.2). `build.sh` passes
  `-DPICO_BOARD=pico` by default, matching the RP2040 on the bench (§1.4); override
  per invocation with `PICO_BOARD=pico2 ./build.sh` for a Pico 2.
  This selects the MCU family, so getting it wrong produces link errors rather than
  a subtly wrong binary. Other valid values: `pico`, `pico_w`, `pico2_w`,
  `pimoroni_pga2350` (RP2350B_5X board).
* **`RP2040/my_machine.h`** — every `BOARD_*` left commented out, so pin
  assignments come from `boards/generic_map.h`. `USB_SERIAL_CDC` is on by
  default. This is the right state for proving the toolchain; pick a real board
  map (`BOARD_PICO_CNC`, `BOARD_PICOBOB`, `BOARD_BTT_SKR_PICO_10`, …) once
  hardware is decided.

## 1.4 Flashing and first contact

### The bench board is an original Pico (RP2040), not a Pico 2

The board connected on 2026-09-21 turned out to be an **RP2040**, which is worth
knowing because an RP2350 build will not run on it. Two independent tells:

* In BOOTSEL it enumerates as `2E8A:0003` "RP2 Boot" with a drive labelled
  **`RPI-RP2`**, whose `INFO_UF2.TXT` reads `Model: Raspberry Pi RP2`. A Pico 2
  is `2E8A:000F` with a drive labelled `RP2350`.
* Over SWD it reports DPIDR `0x0bc12477` (DPv2) and two **Cortex-M0+** cores. A
  Pico 2 is DPv3 with Cortex-M33 cores — `target/rp2350.cfg` fails on it with
  `ADIv6 requires DPv3`.

`build.sh` now defaults to `PICO_BOARD=pico` accordingly. Use
`PICO_BOARD=pico2` for a Pico 2; CI builds both regardless.

### Wiring

* **The board's own micro-USB must be a data cable.** Charge-only cables are
  common, power the board, and make it invisible to the PC. A blank board shows
  up as a drive immediately, without pressing BOOTSEL. Nothing on a blank board
  lights up, so there is no visual way to tell powered from dead.
* **Raspberry Pi Debug Probe (optional but recommended):** its **D** port to the
  board's 3-pin SWD header. It does *not* power the target. Its own UART shows
  up as a separate COM port (COM5 here), unrelated to grblHAL.

### Flashing

**Over SWD — preferred.** No button presses, and verifies what it wrote:

```bash
cd "/c/Users/David Lyman Dawes/play"
./build.sh && ./flash.sh              # PICO_BOARD=pico2 for a Pico 2
```

`flash.sh` drives OpenOCD (`toolchain/openocd`, Raspberry Pi's build from
`pico-sdk-tools`, which carries the RP2040/RP2350 support upstream OpenOCD
lacks) through `program … verify reset`.

**Over USB — fallback.** Hold BOOTSEL while plugging in, then copy
`RP2040/build/grblHAL.uf2` onto the `RPI-RP2` (or `RP2350`) drive.

The Debug Probe's firmware is 1.0.1, which OpenOCD flags as old and works around
at reduced performance. Updating it is worthwhile but not urgent.

### First contact

grblHAL appears as a USB CDC serial port — `2E8A:000A`, COM6 here. Baud rate is
ignored. `$I` reports, among other things:

```
[VER:1.1f.20260908:]
[DRIVER:RP2040@200MHz]
[DRIVER OPTIONS:SDK_2.1.1]
[NVS STORAGE:*FLASH 4K]
[FREE MEMORY:214K]
```

### Bench settings for a board with nothing wired

It starts in `Alarm` with `Pn:XYZHSEP`: every input reads as active, because
grblHAL assumes normally-closed switches. Invert the ones that are active. The
values below were derived from the pins *this* board reported, not copied from
README's generic `$14=73`, which does not match this pin map:

| Setting | Meaning | Active pins | Value |
|---|---|---|---|
| `$5` | limit invert mask | X, Y, Z | `7` |
| `$6` | probe invert | P | `1` |
| `$14` | control invert mask | H (hold, 2), S (cycle start, 4), E (E-stop, 64) | `70` |

The letter-to-bit mapping for `$14` is the string `"RHSDLTEOFM Q  P "` in
`report.c` `control_signals_tostring()`. After setting these, `$X` clears the
residual alarm and the controller reports `Idle`. **`$RST=$` restores the
defaults**, which will be needed the moment real switches are wired.

### Testing on the board

The same 21-case suite that runs against the simulator runs against the board:

```bash
PYTHON="/c/Users/David Lyman Dawes/play/toolchain/venv/Scripts/python.exe" \
    bash test/run_tests.sh --serial COM6
```

`test/serial_bridge.py` makes each hardware case start the way a simulator case
does. Before every case it soft-resets, unlocks if needed, rapids back to machine
zero, waits for Idle and resets again — because machine position survives a
soft reset and cases such as the arc test depend on their start point. After the
case it waits for motion to finish so the next reset never lands mid-move. A run
takes about a minute, mostly real motion: the arc case alone is 15.7 mm at F100.

`test/hw_rt_stress.py COM6` exercises what the regression suite cannot: realtime
commands arriving in the RX interrupt *while the stepper interrupt is busy*. See
§1.9 for results.

pyserial lives in a venv at `toolchain/venv`, keeping it out of the system
Python like the rest of the toolchain.

## 1.5 The three things that went wrong

All three are upstream problems in the Pico SDK and picotool. **None of them are
grblHAL bugs** — grblHAL itself compiled clean on the first attempt, 250/250
targets, no warnings surfaced in the tail.

### (a) Host GCC 16.1.0 silently produces a broken picotool — the important one

CMake picks the host C++ compiler off `PATH`. This machine has scoop's mingw
GCC **16.1.0**, which got selected. picotool then *builds successfully* and
`picotool version` *runs fine* — but `picotool uf2 convert` and
`picotool coprodis` both segfault (`0xC0000005` / exit 139).

Because uf2 conversion is the final post-link step, the symptom is a build that
compiles all 250 targets, links `grblHAL.elf`, emits `.bin` and `.hex`, and then
dies with an access violation and no useful message.

**Fix:** put a mainstream host compiler first on `PATH`. `toolchain/env.sh`
prepends `mingw-13.1/bin` for exactly this reason. Rebuilding picotool under
GCC 13.1 fixed uf2 conversion.

This is worth remembering generally: a bleeding-edge host compiler can produce
host *tools* that build and run but are subtly wrong, and the failure surfaces
far from the cause.

### (b) Pico SDK 2.1.1 `pioasm` misses `#include <cstdint>`

GCC 13 and newer no longer pull `<cstdint>` in transitively, so building the
host-side `pioasm` tool fails with `'uint8_t' does not name a type`, followed by
a cascade of `'struct program' has no member named 'used_gpio_ranges'` (that
member's declaration is the line that failed to parse).

**Fix applied:** added `#include <cstdint>` to
`toolchain/pico-sdk/tools/pioasm/pio_enums.h` — the common base header, so one
line fixes every consumer. (`output_format.h` also got one; harmless.)

**This patch lives in the SDK, not in a repo we control.** Re-cloning or
updating the SDK will lose it and the error will come back.

### (c) picotool 2.3.2 `coprodis` segfaults

The SDK fetches picotool from its `develop` branch (2.3.1-3-g6b8b68a) rather
than the 2.1.1 tag, and that build's `coprodis` subcommand crashes. It still
crashes when built with GCC 13.1, so unlike (a) this is a genuine picotool bug,
not a compiler artifact. Pinning picotool back to the 2.1.1 tag is not an option
either — 2.1.1's `cli.h` does not compile with any GCC 13+.

**Fix applied:** `-DPICO_NO_COPRO_DIS=1`, a documented SDK option
(`src/cmake/on_device.cmake:31`). `coprodis` only annotates RP2350 coprocessor
instructions in the human-readable `.dis` listing — **it has no effect on the
firmware image**. `build.sh` passes this flag.

## 1.6 What "compiling the core" actually means

There is no way to compile the `core` repository on its own, and no test target —
that is the single biggest practical constraint on working here:

* Core's `CMakeLists.txt` declares an `INTERFACE` library named `grbl` that only
  *lists* source files. The driver does `include(grbl/CMakeLists.txt)` and
  compiles them into its own `grblHAL` executable.
* Nothing in core has a `main()`. `grbl_enter()` is the entry point and the
  driver's `main()` calls it.
* Every file is heavily conditionally compiled. A change that builds for
  `N_AXIS=3`, `COMPATIBILITY_LEVEL=0`, no kinematics can easily break another
  combination.

**Practical consequence:** the edit/verify loop is *edit core → run
`./build.sh`*. Incremental rebuilds after touching one core file take seconds.
But any claim that a core change "compiles" is only true for the one option set
that was built — locally, RP2040 / generic map / 3 axes; CI covers six configurations (§1.7).

**If you add a new `.c` file to core, add it to core's `CMakeLists.txt`** or
CMake-based drivers will silently not link it.

### Which core the local build uses

The driver's `grbl` submodule is pinned by the driver repo to an **upstream**
`grblHAL/core` commit. Building from a plain `--recursive` clone therefore
compiles upstream's core, *without* any of the fixes on our fork.

The local clone gets around that by checking the submodule out at our fork:

```bash
cd RP2040/grbl
git remote add fork https://github.com/DavidLDawes/core.git   # once
git fetch fork main && git checkout --detach fork/main
```

The driver repo then reports `M grbl`. That is expected and must not be
committed from the driver side. Re-run the fetch/checkout after merging core
changes, or `play/core` edits will not reach the build. CI does not have this
problem: both workflows substitute the core fork's `main` explicitly (§1.7).

The durable fix is to repoint the driver fork's submodule at
`DavidLDawes/core` in `.gitmodules` and bump the pinned commit, so a fresh
`--recursive` clone builds our core by default. That is a deliberate
divergence from upstream's driver, so it is listed as a follow-up in Part 5
rather than done silently.

See §2.4 for why standing up a host-side build would still be worth the effort —
none of the above lets the core be tested without hardware in the loop.

---
## 1.7 Continuous integration

`.github/workflows/build.yml` runs on every pull request, every push to
`main`, and on demand via *Actions → build → Run workflow*.

### What it does

Because the core has no `main()` and its `CMakeLists.txt` only declares an
`INTERFACE` library, CI builds it the only way it can be built — as part of a
real driver. Each job:

1. Checks out this repository.
2. Installs Arm GNU Toolchain 14.2.rel1 and the Pico SDK 2.1.1 (both cached
   between runs, so a warm run skips ~500 MB of downloads).
3. Applies the `pioasm` `<cstdint>` patch from §1.5(b).
4. Clones the grblHAL RP2040 driver with its submodules, then **deletes the
   driver's pinned `grbl` submodule and substitutes this checkout** — so what
   gets compiled is the PR's code, not upstream's.
5. Configures and builds, then verifies the firmware image and reports its size.
6. Uploads `grblHAL.uf2` and `grblHAL.elf` as build artifacts (14 day retention).

### The matrix

Six configurations, `fail-fast: false` so one failure doesn't mask the others:

| Job | Board | Extra defines |
|---|---|---|
| `pico2-generic-3axis` | `pico2` | — |
| `pico-generic-3axis` | `pico` | — |
| `pico2-generic-4axis` | `pico2` | `BOARD_GENERIC_4AXIS` |
| `pico2-generic-8axis` | `pico2` | `BOARD_GENERIC_8AXIS` |
| `pico2-compat-level-1` | `pico2` | `COMPATIBILITY_LEVEL=1` |
| `pico2-compat-level-2` | `pico2` | `COMPATIBILITY_LEVEL=2` |

The matrix is the point. Every file in the core is heavily conditionally
compiled, so a change that builds for `N_AXIS=3` / `COMPATIBILITY_LEVEL=0` can
easily break another combination — and that is precisely the class of breakage
this catches. It covers both MCU families (RP2040 and RP2350), the axis-count
variations, and the compatibility levels that gate the protocol extensions.

### How options are injected

Two mechanisms, both verified locally before being committed:

* `-DPICO_BOARD=<board>` — a cache variable in the driver's `CMakeLists.txt`,
  so it overrides cleanly on a fresh configure.
* `-DCMAKE_PROJECT_grblHAL_INCLUDE=inject.cmake`, where `inject.cmake` contains
  `add_compile_definitions(...)`. This puts a real `-D` on every compile line.

Two approaches that **do not** work, recorded so they aren't retried:

* `-DCMAKE_C_FLAGS="-DN_AXIS=5"` clobbers the SDK's architecture flags
  (`-mcpu=cortex-m33 -mthumb -march=armv8-m.main+fp+dsp ...`), and the build
  then dies deep inside the SDK with
  `#error no SW_SPIN_TRY_LOCK available for PICO_USE_SW_SPIN_LOCK`.
* Appending `#define N_AXIS 5` to `my_machine.h` gives a redefinition error —
  `my_machine.h` is not included before `config.h` in every translation unit.

Note also that `N_AXIS` cannot simply be raised on the default map:
`generic_map.h` rejects it with `#error "Axis configuration is not supported!"`.
Use the dedicated `BOARD_GENERIC_4AXIS` / `BOARD_GENERIC_8AXIS` maps, as the
matrix does.

### What it does *not* do

**There are no tests, so CI does not run any.** This is a build matrix plus an
image sanity check (UF2 magic, block alignment, `arm-none-eabi-size` output) —
nothing exercises the parser, the planner, or the stepper logic.

That gap is not something CI configuration can close: there is no host-side
build, so there is nothing to run on a runner. Item 6 in the §1.1 status table
and §2.4 are the prerequisite. Until then, CI answers "does it still compile
everywhere?" and nothing more.

## 1.8 The host simulator and regression suite

`test/` builds the core as an ordinary PC program and runs a regression suite
against it. This is the only place core code is actually **executed** rather than
just compiled, and it needs no hardware.

```bash
cmake -G Ninja -S test -B build-sim
cmake --build build-sim
bash test/run_tests.sh build-sim/grbl_sim
```

CI runs exactly this as the `host-tests` job on every PR and merge.

### What it is

* `test/sim_driver.c` — a host "driver" implementing enough of the HAL contract to
  link and run `grbl_enter()`. Input comes from stdin, output goes to stdout.
* `test/CMakeLists.txt` — reuses the core's own `CMakeLists.txt` source list, so a
  file added there is picked up automatically.
* `test/run_tests.sh` — 21 cases covering startup, g-code parsing, modal state,
  reporting and setting descriptions.

It is deliberately **not** a machine simulator. Steps are counted, not timed, and
nothing runs from an interrupt. That is enough for the parser, planner, settings
and protocol layers, and it keeps the harness small. Anything about timing, step
generation or interrupt behaviour still needs a board — so Part 4's findings in
particular remain untestable here.

### Things that had to be got right

Each of these cost a debugging cycle and would cost another if the harness is
ever rewritten.

**The stream must filter realtime commands.** A real driver calls
`enqueue_realtime_command()` on every received byte in its RX interrupt handler
and only passes the rest to the line buffer. The first version of the harness
returned raw bytes, so `CMD_EXIT` reached the line parser as data, was stripped as
a control character, and the process spun forever.

**The harness must never be able to hang CI.** `streamGetC()` injects `CMD_EXIT`
on *every* starved read, not once — a soft reset re-enters the main loop, and a
one-shot injection leaves the core spinning on an empty stream. On top of that a
counter aborts with a diagnostic and exit code 2 after `SIM_MAX_STARVED_READS`
polls, so a future regression fails loudly instead of burning a CI runner.
`run_tests.sh` also wraps every case in `timeout`.

**Driver capabilities are checked at startup.** `grbl_enter()` raises ALARM:16
(`Alarm_SelftestFailed`) unless the driver claims everything the core was built to
expect. Two matter here: `amass_level` must equal `MAX_AMASS_LEVEL`, and
`step_pulse_delay` must be set because `config.h` always defines
`DEFAULT_STEP_PULSE_DELAY`.

**One case per process.** With `COMPATIBILITY_LEVEL 0` the core stops parsing
after a failed block and simply repeats the last error until reset, so cases
sharing a process contaminate each other.

**glibc before 2.38 has no `strlcpy`.** The core uses it; CMake probes for it and
the harness supplies it when missing.

### A portability note, not a bug

The harness does not link with MinGW on Windows: `mc_rigid_tapping` is declared
`__attribute__((weak))` and PE/COFF does not resolve weak symbols the way ELF
does. It links and runs correctly on Linux, which is where CI runs it. To run it
locally from Windows, use a container:

```bash
docker run --rm -v "/c/Users/David Lyman Dawes/play/core:/src" -w /src \
    grblsim-dev bash -c 'cmake -G Ninja -S test -B /tmp/b && ninja -C /tmp/b \
                         && bash test/run_tests.sh /tmp/b/grbl_sim'
```

where `grblsim-dev` is `debian:bookworm-slim` plus `build-essential cmake
ninja-build`.

### What it does and does not cover

It covers the parser, modal state, settings, reporting and the protocol loop —
the layers where most regressions land, and where nothing could previously be
checked at all.

It does **not** cover the two fixes from Step 1 in the way one would hope:

* The `free()`-in-ISR path (§2.1(2)) only triggers when the task pool is
  exhausted, which the harness has no way to force.
* The `realloc` failure path (§2.1(1)) needs an allocation failure *and* an
  incrementing setting whose description contains a `?` placeholder. Only one core
  description contains `?` and it is not an incrementing setting, so **that branch
  is unreachable in a core-only build** — it exists for drivers and plugins that
  register such settings.

What the suite does do for those fixes is guard the *ordinary* path: the
`$SED=100/101/102` cases would catch a rewrite that broke normal description
lookup. Fault injection for the failure paths would need a malloc shim and a
test-only setting, which is a reasonable next increment but was not built here.

## 1.9 Hardware results

Run on 2026-09-21 against the RP2040 bench board, firmware built from `main` of
both forks — so including every fix in Parts 2–4 marked FIXED.

### Regression suite: 21 / 21

The same cases and assertions as the simulator, via `serial_bridge.py`. A
negative control — an impossible expectation added to a copy of the runner —
correctly failed and exited 1 against the board, so a green run is meaningful.

### Realtime stress during motion: pass

`hw_rt_stress.py` sends a status query every ~5 ms through a 4 s move, with a
feed hold and resume in the middle:

```
status queries sent     : 923 over 5.0s
status reports received : 923
state sequence          : Run -> Hold:1 -> Hold:0 -> Run -> Idle
hold / resume at        : 1.50s / 2.08s
final report            : <Idle|MPos:20.000,0.000,0.000|Bf:100,1023|FS:0,0>
```

Every query answered, no alarm or error, and the move finished on exactly the
commanded position. That is roughly 150 realtime interrupts a second running
through the §4.1 PRIMASK code concurrently with the stepper interrupt.

**What that does and does not show.** It shows the §4.1 critical-section code is
*not broken* under realistic interrupt load. It does **not** show the fix was
*needed*: the original bug only bites when those calls nest inside an outer
critical section, which this traffic is unlikely to produce, so the old code
would probably have passed too. Demonstrating the bug itself would take a
deliberately nested caller.

### A false alarm worth recording

The first version of the stress test sent the resume ~250 ms after the hold and
the controller then sat in `Hold:0` indefinitely, ignoring line commands. That
was the test, not the firmware. Cycle start is only accepted once a hold has
**completed** (`Hold:0`); sent during deceleration (`Hold:1`) it is ignored by
design, and at F300 with the default 10 mm/s² deceleration takes ~0.5 s. Line
commands are deliberately not processed during a hold — they wait in the RX
buffer. A resume sent after `Hold:0` finished the move correctly. The test now
waits for `Hold:0`.

---

# Part 2 — Code review findings (core)

Reviewed at commit `516e5ad` (upstream `master`, now our `main`). All findings originate
upstream rather than as local regressions.

Findings marked **FIXED** have been addressed on this fork; the rest are still open.
Line numbers are as of the original review, so they may have shifted slightly in fixed
files.

**Most findings below are from reading, not from running** - the original review pass -
except findings 3 and 7, both found later while doing hardware work rather than by
reading, and both confirmed on hardware rather than read into existence: finding 3 only
surfaces under real interrupt timing the simulator cannot reproduce, and finding 7 was
confirmed by reading its actual state directly out of RAM over SWD. All fixes are verified
as *compiling* across the six CI configurations; findings 1 and 2 are additionally verified
with the host simulator (§1.8), finding 3 with a dedicated hardware regression test (§1.4,
§1.9), and finding 7 is not fixed at all (§2.3(1) sidesteps it; see Part 5 Step 7).

## 2.1 Confirmed defects

### 1. `settings.c:3227` — persistent NULL deref after one failed `realloc` — HIGH — **FIXED**

In `setting_get_description()`:

```c
if(len < buflen || (buf = realloc(buf, (buflen = len)))) {
    *buf = '\0';
```

`buflen = len` is evaluated *before* `realloc` returns. A failed `realloc` therefore leaves
`buf == NULL` with `buflen` already grown — and leaks the old block. Every later call that
takes the `len < buflen` branch then does `*buf = '\0'` on NULL. Both are `static`, so a
single OOM poisons the function for the rest of the boot.

Fix: assign to a temp, only commit `buflen` on success.

### 2. `stepper.c:524` — `free()` called from the stepper ISR — HIGH — **FIXED**

Inside `stepper_driver_interrupt_handler()` (`ISR_CODE`, line 453, runs at up to ~300 kHz),
on the path where `task_add_immediate()` fails because the task pool is full:

```c
if(!task_add_immediate((foreground_task_ptr)gc_output_message, st.exec_block->message))
    free(st.exec_block->message);
```

With newlib-nano and a no-op `__malloc_lock` — the common bare-metal configuration — an ISR
landing mid-`malloc()` in the foreground corrupts the heap. Leaking the message would be
strictly safer than freeing it here.

### 3. `gcode.c` / `planner.c` / `stepper.c` — use-after-free of M62-M65 output command lists — HIGH — **FIXED, found on hardware**

Discovered while bringing up hardware for Part 5 Step 5, not by static review — the
simulator's simplified stepper model can't reproduce it, since it has no real ISR/foreground
concurrency. On real RP2040 hardware it reliably HardFaults inside the stepper ISR within a
few thousand moves that each carry a synchronized output command (M62/M63/M64/M65).

The output command list for a queued M62-style command is allocated by the parser
(`gcode.c`), handed to the planner block, then copied - as a bare pointer - into the
stepper block by `st_prep_buffer()` (`stepper.c:975`, before the fix). The **planner
block** kept ownership: `plan_discard_current_block()` -> `plan_cleanup()` frees the list
as soon as the block has been prepped into a stepper block, which can happen *before* the
stepper ISR has executed that stepper block. The ISR then walks a freed linked list
(`stepper.c:516-522`) - a use-after-free from interrupt context, which on RP2040 shows up
as a HardFault with a corrupted `next` pointer.

A second, milder instance: `gcode.c:729`'s `gc_at_exit()` cleared the global
`output_commands` list on a soft reset or parse error, but never reset the module-level
`output_commands` pointer itself (fixed separately, `gcode.c` + `planner.c` - see the
regression tests `queued output survives a soft reset` / `...a parse error`). That variant
is exercised by the simulator and was the first thing caught; the ISR-facing variant above
needed real hardware timing to surface.

**Fix** - give the *stepper block* real ownership instead of copying a bare pointer:

* `stepper.h`: `st_block_t` gains `output_commands_head`, the pointer the block actually
  owns and must free. `output_commands` is still what the ISR walks as it executes items.
* `stepper.c`: `st_prep_buffer()` now frees whatever list is still attached to the ring
  buffer entry being recycled *before* overwriting it (an entry being reused is guaranteed
  no longer in use by the ISR - that's what makes the ring buffer safe), takes ownership of
  the planner block's list, and clears the planner block's pointer so
  `plan_discard_current_block()` has nothing left to double-free.
* `st_reset()` frees every block's list on reset (steppers are idle, so nothing is
  ISR-owned at that point) and clears the aliased `st_hold_block` copy so it's never
  "restored" after being freed.

**Verified on hardware**, not just compiled: `test/hw_output_commands.py` runs 3000 moves
each carrying an M62, before the fix HardFaulting the board partway through and after the
fix completing cleanly with the heap unchanged (214K free, before == after). Confirmed the
*unfixed* code reliably reproduces the fault (halted the core over SWD mid-fault: PC in
`stepper_driver_interrupt_handler`, walking a linked list pointing into freed heap) and the
fixed code does not, across a full run. Also confirmed the output actually still executes -
correct polarity, and only on the move that carries it, not before or after - so the fix
doesn't just paper over the crash by dropping the feature.

### 4. `grbllib.c:489` — `plan_reset()` return value ignored — MEDIUM — **FIXED**

`plan_reset()` returns `false` and leaves `block_buffer.blocks == NULL` when the planner
buffer can't be allocated (`planner.c:230-246`), returning *before* `head`/`tail` are
initialised. The caller ignores this. `plan_buffer_line()` then dereferences a NULL
`block_buffer.head` on the first motion.

**Fixed.** `grbl_enter()`'s re-init loop now checks the return value and calls
`system_raise_alarm(Alarm_BufferOverflow)` when it's false, putting the system into
`STATE_ALARM` with `sys.blocking_event` set (requires a real reset/power cycle to clear,
since retrying won't succeed - the allocation already failed at the smallest fallback size)
instead of silently continuing into the NULL-deref hazard. `Alarm_BufferOverflow` (defined,
value 22, but previously never raised anywhere in core) was reused for this rather than
adding a new alarm code, and added to `alarm_is_critical()`'s list so `sys.blocking_event`
is actually set; its description text was extended to also cover an allocation failure, not
just an overflow.

**Not independently verified with its own test** - this requires genuinely exhausting
allocatable memory at boot, not reproducible from the host simulator's or the bench
hardware's external protocol. Verified instead by: code inspection of the fixed call site,
the regression suite (unaffected, since this path is never exercised by it), and a full
build + flash + hardware smoke test (§1.1 item 18) confirming the change introduces no
regression to normal boot/motion.

### 5. `grbllib.c:577` — use-after-free in the systick task walk — MEDIUM — **FIXED**

```c
if((task = tasks.systick)) do {
    task->fn(task->data);
} while((task = task->next));
```

`task->next` is re-read *after* `fn()` ran. If the callback deletes itself,
`task_free()` NULLs `->next` and the remaining systick tasks are silently skipped for that
tick. Worse: `task_free()` pushes the slot onto `tasks.free_list`, so any `task_add_*()`
call inside that same callback pops the identical slot straight back off the free list and
relinks it — the systick walk then continues into the immediate or delayed list and runs
those callbacks in the wrong context.

**Fixed** exactly as prescribed: `next` is cached into a local before `fn()` runs, so the
walk's traversal no longer depends on `task->next` still being meaningful afterward.

**Not independently verified with its own test** - reproducing the failure needs a systick
task that deletes itself and re-registers a new one from inside its own callback, which
nothing in this tree currently does deliberately. Verified instead by: the fix is a small,
mechanical, well-understood pattern (cache-before-mutate); the regression suite (unaffected);
and a hardware smoke test - `task_execute()`'s systick path runs continuously in normal
operation (it's what drives `auto_realtime_report`), so any gross breakage would have shown
up immediately as a hang or missing status reports, and didn't.

### 6. `protocol.c:76` — unbounded `strcpy` on a public API — MEDIUM — **FIXED**

`protocol_enqueue_gcode()` is exposed to plugins as `grbl.enqueue_gcode` and copies
caller-supplied text into `xcommand[LINE_BUFFER_SIZE]` with no length check. `strlcpy` is
already used elsewhere in the tree.

**Fixed.** Added `strlen(gcode) < sizeof(xcommand)` to the existing `ok` gate - a too-long
command is rejected outright (returns `false`, `xcommand` untouched) rather than silently
truncated into a different, shorter, still-valid-looking command. Switched the copy itself
to `strlcpy` for defense in depth once that guard is in place. Verified compiling clean and
against the regression suite (unaffected - no existing test sends an oversized command
through this path); not independently exercised with a new test, since `protocol_enqueue_gcode`
has no G-code/`$`-command surface to drive it from the sim's or hardware's external ASCII
protocol - it's a plugin-facing C API called directly by driver/plugin code.

### 7. `settings.c` — a late-registered `grbl.on_settings_changed` hook is silently never called for a single-setting write — HIGH — **FIXED (Step 7)**

Not in the original review; found while implementing the `steps_per_mm` reciprocal cache
(§2.3(1)) and confirmed directly on hardware, not just by reading.

`settings_store_setting()` - the function behind every `$nnn=value` command - does not call
`grbl.on_settings_changed()` for the setting it just wrote. It calls `set->on_changed(...)`,
where `set` is the `setting_details_t` group the setting belongs to and `set->on_changed` is a
**snapshot** of `grbl.on_settings_changed`, taken **once**:

```c
if(set == &global_settings && set->on_changed == NULL)
    set->on_changed = grbl.on_settings_changed;
```

and unconditionally again at the very end of `settings_init()`:

```c
global_settings.on_changed = grbl.on_settings_changed;
```

`settings_init()` runs once, early in `grbl_enter()`, before the `while(looping)` loop. Any
module that installs its hook by chaining onto `grbl.on_settings_changed` from a function called
**inside** that loop - `gc_init()`, `plan_reset()`, and by extension anything following the
exact pattern this file's own CLAUDE.md documents for plugins - installs itself **after**
`global_settings.on_changed` has already been snapshotted. `grbl.on_settings_changed` genuinely
gets updated to point at the new hook; `global_settings.on_changed` does not, permanently. A
runtime `$nnn=value` write to any core, `global_settings`-scoped setting (which covers most
built-in settings, including every axis setting) then never reaches that hook - only a full
settings reload or restore does, since those call sites (`settings_init()`, `settings_restore()`)
use `grbl.on_settings_changed()` directly rather than going through `set->on_changed`.

Modules that instead register their **own** `setting_details_t` group via `settings_register()`
(spindle plugins, kinematics modules with their own settings) are unaffected - their group's
`.on_changed` is assigned explicitly in the group's own initializer, not lazily snapshotted from
the shared chain. The bug is specific to hooking the *shared* `grbl.on_settings_changed` chain to
react to *built-in* `global_settings` changes, from *after* boot's settings load.

**Confirmed on hardware** for planner.c's hook (installed exactly per the documented plugin
pattern, from `plan_reset()`): after registering, `global_settings.on_changed` still pointed at
whichever module's hook had already been live at the snapshot moment (`ioports.c`'s, confirmed
by reading its function pointer directly out of RAM over SWD) - not the newly-installed one - so
a `$100=...` write never reached it. `ioports.c`'s own hook is unaffected because
`ioports_init()` runs during `driver_init()`, before `settings_init()`.

`gcode.c`'s `onSettingsChanged` (`gc_init()`, called from the same loop position as
`plan_reset()`) has the identical registration-timing pattern and is very likely affected the
same way - not independently verified with its own test, since what it recomputes was not
traced, but the mechanism is the same call site with the same timing.

**Fixed (Step 7).** `settings_store_setting()` now dispatches through the live
`grbl.on_settings_changed` chain for `global_settings`, instead of the frozen `set->on_changed`
snapshot - the same thing `settings_restore()` and the initial NVS load already did. The
now-unused `global_settings.on_changed = grbl.on_settings_changed;` snapshot assignment at the
end of `settings_init()` was removed. Modules with their own registered `setting_details_t`
group (spindle plugins, kinematics modules) are unaffected - their group's `.on_changed` was
never part of this snapshot mechanism.

**Confirmed on hardware.** Built and flashed to the bench board, then verified with a hardware
breakpoint set on `gcode.c`'s `onSettingsChanged` (the exact late-registered hook this bug
affected, installed by `gc_init()`) via OpenOCD/GDB over SWD:

- Before the fix: sending `$0=8` over serial returned `ok` immediately and the breakpoint never
  fired - `gc_init()`'s hook was confirmed live (`grbl.on_settings_changed` read via SWD pointed
  at it) but unreachable from this call path, exactly as diagnosed.
- After the fix, same test: the write hung (no `ok` until resumed) and the breakpoint hit, with
  a backtrace confirming the full path -
  `onSettingsChanged (gcode.c:759)` ← `settings_store_setting (settings.c:3660)` ←
  `system_execute_line` (handling `$0`) ← `protocol_main_loop` ← `grbl_enter` ← `main`.
- The regression suite (24/24) still passes unchanged on the host simulator.

This is a real, previously-undiscovered core bug independent of anything added this session: it
silently affected `gc_init()`'s own hook (which calls `gc_spindle_off()` on a spindle-setting
change) on every grblHAL board, for every `$nnn=value` write, since that hook has always
installed itself the same way `plan_reset()`'s did.

## 2.2 Design and documentation

### 8. `hal.h:638-641` — Doxygen comments swapped — LOW but public — **FIXED**

`irq_enable` is documented as "Optional handler to **disable** global interrupts" and
`irq_disable` as "...**enable**...". This is driver-author-facing generated API docs.

### 9. `hal.irq_disable()` / `irq_enable()` don't save and restore the mask — MEDIUM — **FIXED**

They are unconditional, so they don't nest: an inner pair re-enables interrupts for the
outer critical section too. Functions marked `ISR_CODE` and documented ISR-callable
(`task_add_immediate`, `task_add_delayed`) end with an unconditional `irq_enable()`, so
calling them from an ISR clears PRIMASK *inside* that ISR. The contract isn't stated
anywhere in `hal.h`.

### 10. Aux I/O driven from the stepper ISR — MEDIUM — **FIXED, scoped to what core controls**

`ioport_digital_out()` / `ioport_analog_out()` are called from the stepper ISR
(`stepper.c:513-519`) for M62–M65 motion-synchronised output. An aux port backed by an I²C
or Modbus expander blocks the ISR for milliseconds. Partly known — `on_port_out` says
"might be called from interrupt context" — but nothing prevents a slow port being bound.

Same neighbourhood as §2.1(3)'s use-after-free - both are about the output-command list
that this same ISR code walks. §2.1(3) fixes the list's memory safety; this finding about
blocking on a slow port is a separate, until-now-open concern.

**Fixed the part core can actually fix.** Core has no way to know from here which of a
driver's or plugin's aux ports are I²C/Modbus-backed and slow - that lives entirely in
driver/plugin code (`i2c_leds.c`, `mcp23017.c`, `pca9654e.c`, `hc595.c`, etc., none of which
are in this repo). What core *can* do is give those port implementations a way to say so, and
refuse to bind a marked port to motion-synchronised output:

* `pin_cap_t` (`crossbar.h`) already had an unused, undocumented `async :1` bit. Documented
  it: "output may block or take non-negligible time - unsafe to call from interrupt context."
* Added `ioport_out_is_async()` (`ioports.c`/`.h`), a thin wrapper reading that bit off
  `ioport_get_info()`.
* `gcode.c`'s M62/M63 (digital) and M67 (analog) validation - the only paths that queue a
  motion-synchronised output for the stepper ISR to execute - now reject a port with that bit
  set (`Status_GcodeValueOutOfRange` / `Status_GcodeRPMOutOfRange`, reusing the existing
  out-of-range codes for this block rather than adding new ones). M64/M65/M68 (the immediate
  variants, which execute in the parser's own foreground context, never the ISR) are
  unaffected.

**Honestly scoped**: no port shipped in this repo or the RP2040 driver currently sets
`cap.async`, so this is currently inert in practice - it establishes the mechanism a slow
port implementation needs to opt into, not a fix that changes today's behaviour on any real
board. Adopting it in the actual slow-port plugins is out of scope here (separate repo).

**Verified**: a new host-simulator regression test (`test/sim_driver.c` flags aux output
port 3 async; `test/run_tests.sh`, sim-only) confirms `M62 P3` is rejected with `error:39`
while `M64 P3` still succeeds. Confirmed on the bench hardware that this introduces no
regression to the real (non-async) aux ports - the existing M62-M65 hardware tests still pass
unchanged, and (correctly) a real board's port 3 is not flagged, so `M62 P3` there still
succeeds - there's no async-flagged port on this bench to reject.

### 11. `stepper.c:815` — unguarded `exec_segment` deref — LOW / uncertain — **FIXED**

The experimental fast-hold path dereferences `st.exec_segment->n_step` with no NULL check,
while every other use of `exec_segment` in the file is NULL-guarded. Marked experimental.

**Fixed** with a NULL guard matching the rest of the file's pattern:
`st.step_count < 3 || (st.exec_segment && st.step_count < (st.exec_segment->n_step >> 3))`.
When `exec_segment` is NULL there's no "current segment's step count" to compare against, so
the guard falls back to just the `step_count < 3` half of the original condition rather than
guessing new semantics. Compiles clean; not independently exercised with a new test - this is
explicitly experimental, ISR-adjacent code (`st_update_plan_block_parameters(fast_hold)`,
called from `planner_recalculate()` under `hal.irq_disable()`) reached only via a feed-hold
recalculation timing window that isn't reliably reproducible from either harness. Verified by
code inspection (the guard matches every other `exec_segment` use in this file) and the
regression suite / hardware smoke test showing no regression to normal feed-hold behaviour.

### 12. `planner.c:401` — hidden state across reset — LOW — **investigated, not a bug**

`static axes_signals_t direction` persists across soft reset and is only updated for axes
with a non-zero step delta. Likely deliberate, but `plan_reset()` doesn't clear it.

**Investigated, closing without a code change.** Traced every consumer of the bit this
finding worried about: `stepper.c`'s Bresenham loop only reads `st.dir_out`'s bit for an axis,
and only updates `sys.position` for that axis, inside the `if(st.counter.x > st.step_event_count)`
branch - which structurally cannot execute for an axis with `steps.value[idx] == 0`, since
`st.counter.x` is only ever incremented by `st.steps.x`. A stale `direction` bit for a
non-stepping axis is therefore never read into position tracking, and never gates an actual
step pulse - it's provably inert, both within a session and across a reset. This also matches
real hardware behaviour: an axis that isn't stepping shouldn't have its DIR pin's meaning
change either. Forcibly clearing it in `plan_reset()` would be a change with no benefit and a
small risk of its own (a spurious DIR transition on that axis's first move after reset, itself
harmless but unnecessary). No code change made.

### 13. No CI, no tests, no host-buildable target — **RESOLVED**

For a codebase that moves a machine, this was the largest structural gap when this review
was written.

**Resolved since**: `test/sim_driver.c` (a minimal HAL letting core run as a PC process) and
`test/run_tests.sh` (a growing regression suite, now 24 cases plus 2 simulator-only ones) were
added as part of Part 5 Step 2, and GitHub Actions CI (§1.7) builds both the simulator and a
6-way driver matrix on every push and PR. This is also §2.4's first extension-opportunity
bullet, delivered rather than left as a suggestion - see the note there.

## 2.3 Optimisation opportunities

All four items below are **FIXED**, landed together as PLAN.md Part 5 Step 6. All are
foreground/planning-time or task-pool changes except the AMASS item, which is the one that
actually touches the stepper ISR. See §2.1(7) for a significant, previously undocumented core
bug found while implementing the `steps_per_mm` cache - not a performance item, a correctness
one, and not folded into this section because of that.

### 1. Cache `1.0f / steps_per_mm` on use — **FIXED**

52 sites divide by `settings.axis[i].steps_per_mm`; `plan_buffer_line()` alone does `N_AXIS`
divisions per block on the queuing path - foreground, not the stepper ISR, but the hot path
for G-code streaming throughput.

**Fix:** `planner.c` gained `get_steps_per_mm_inv(idx)`, a self-contained memoized reciprocal
- it checks the live `settings.axis[idx].steps_per_mm` on every call and only redoes the
division when it has actually changed since the last call, for that axis. `plan_buffer_line()`'s
`unit_vec[]` calculation, its constant-surface-speed RPM calculation, and `plan_get_position()`
(kinematics builds only) all now call it instead of dividing directly.

This is deliberately **not** invalidated via a `grbl.on_settings_changed` hook, despite being
the obvious, idiomatic-looking approach and despite this file's own doc comment initially saying
exactly that. See §2.1(7): that mechanism turned out not to fire on a runtime `$100=...` write
for a hook installed this late in boot, which the memoized-on-use design sidesteps entirely by
not depending on any notification firing at all.

**Verified on hardware**, decisively: `steps_per_mm_inv[0]` and its cache-tracking companion
`steps_per_mm_cached[0]` were read directly out of RAM over SWD before and after a `$100=333`
runtime change followed by a real move. Before: matched the old value. After: `333.0` and
`1/333 = 0.003003...` exactly, both correct. The regression suite and both hardware stress
tests (§1.4, §1.9) also pass with the change in place.

### 2. Hoist the AMASS shift loop out of the ISR — **FIXED, with a correction to the original framing**

`stepper.c:563` (as reviewed) recomputes `N_AXIS` shifts on every segment load. The original
framing - "the inputs only change when the block or AMASS level changes" - undersold a real
constraint worth stating precisely: `amass_level` is chosen **per segment** from that segment's
instantaneous step rate (`st_prep_segment()`), and genuinely varies during a single block's
acceleration/deceleration ramp. It is not a per-block constant, so the fix cannot cache the
shifted values across a whole block - only skip recomputing them when neither the block nor the
AMASS level actually changed since the previous segment, which is the common case (most
segments in a block share both).

**Fix:** a `new_segment_block` bool, captured before the existing new-block-detection branch
overwrites `st.exec_block`, gates the AMASS recompute alongside an `amass_level` comparison
against what was set on the previous segment load.

**Verified on hardware** with unusual care, since this is the one item that touches the
stepper ISR's step-count-affecting path directly, and a wrong version would not error or hang -
it would silently lose or gain steps. Two tests, chosen to stress opposite ends of the
condition: `hw_output_commands.py`'s 3000 alternating +0.01/-0.01mm moves are each entirely
within their own accel/decel ramp, never reaching a cruise speed - close to the worst case for
AMASS-level churn, and now additionally asserts the net position is exactly `X=0.0000` after all
3000 (added specifically for this fix; a single mis-shifted segment would show up here).
`hw_rt_stress.py`'s 20mm move has a genuine cruise phase (exercising the skip path many times in
a row) plus a feed-hold-and-resume mid-flight, and still ends at exactly `X=20.000`. Both pass.

### 3. Give the task pool a real free list — **FIXED**

`task_alloc()` (`grbllib.c`) was an O(`CORE_TASK_POOL_SIZE`) linear scan with interrupts
disabled, reachable from ISR context; the single-entry `last_freed` cache only helped when
exactly one task had been freed since the last allocation - freeing a second task before the
next alloc dropped it from the cache entirely, back onto the slow path.

**Fix:** `task_free()`/`task_alloc()` now push/pop a genuine singly-linked free list, reusing
each task's own `->next` field as the link - safe once a task is off every active list, since
every `task_add_*()` already overwrites `->next` before putting a task back to work, confirmed
by checking every `->next` reference in the file. O(1) on every free and every allocation, not
just the lucky case. The pool is seeded into the free list once, at cold boot, right after the
existing `memset(&tasks, 0, sizeof(tasks))` - required, since a bare memset leaves 40 good slots
with no chain at all; the old scan didn't need this because it found unused slots by their
zeroed `fn` field directly.

**Verified on hardware**: `hw_output_commands.py`'s 3000 moves each carry an `M62`, which is
3000 real interrupt-driven task allocate/free cycles through `task_add_immediate()` -
`controller responsive after 3000 moves` and `free heap unchanged` both pass. Could not obtain a
cycle-level latency measurement - the RP2040's Cortex-M0+ has no DWT cycle counter - so this is
verified for correctness under real interrupt load, not for a measured jitter improvement.

### 4. `report_bitfield()` avoids a per-call malloc/free — **FIXED**

`report.c:1646` mallocs, copies, `strtok`s and frees a flash-resident format string on every
call, purely so `strtok()` has a mutable buffer to tokenize in place.

**Fix:** a small fixed-size stack buffer (96 bytes, comfortably over the longest bit label in
the tree today at 44 characters) holds one token at a time; a hand-rolled scan replaces
`strtok()`, matching its exact semantics (a run of consecutive commas is one delimiter, never an
empty token) so this is a pure allocation-avoidance change with no parsing behaviour difference.

**Verified by direct byte-for-byte diff**, not just the regression suite: `$$=22` and `$$=65`
(two real bitfield settings, one of them exercising the "N/A" reserved-bit skip) produce
byte-identical output before and after the change, in the simulator.

### 5. Streaming line rate: measured, profiled, and `planner_recalculate()` made cheaper — **FIXED (partly)**

Measured on the bench RP2040 Pico (200 MHz, USB CDC, nothing attached) by streaming `+0.01mm`
`G1` lines with the character-counting protocol real senders use (`test/hw_line_rate.py`,
`test/hw_planner_sweep.py`; rates are steady-state, after the planner buffer has filled).

**What limits the rate depends on the regime:**

| Regime | Limit | Measured |
|---|---|---|
| Direction reverses every line | full stop/restart per line at `$120` | ~12 lines/s at 10 mm/s² |
| Buffer shorter than the stopping distance | look-ahead: v = √(2·a·`$398`·d) | 447 / 635 lines/s at 100 / 200 blocks, 10 mm/s² (predicted 447 / 632) |
| Stopping distance fits in the buffer | `$110` | 833 lines/s at 500 mm/min (predicted 833) |
| Realistic accel (100 mm/s²), buffer ≥ 200 | `planner_recalculate()` | ~1700 lines/s, **flat from 200 to 1000 blocks** |
| Very high accel (5000 mm/s²) | fixed per-line foreground cost | ~3200 lines/s |

**Why the plateau is flat in `$398`.** A throwaway `PROFILE_LINE_RATE` build (local branch
`perf/line-rate-profile`, not merged; charges every foreground microsecond to exactly one section,
so the sections sum to the window) showed `planner_recalculate()` taking 56% of each line on the
plateau. Every new block re-plans all blocks in the final deceleration ramp - the `planned` pointer
cannot pass them while blocks keep arriving - and that ramp is v²/2a long. Faster streaming means a
longer ramp and a costlier line, so the rate settles at an equilibrium that no longer depends on
buffer depth once the buffer is longer than the ramp. At 1639 lines/s and 100 mm/s² the ramp is
~134 blocks × ~2.5 µs ≈ 335 µs; measured 341 µs.

Per-line foreground cost, plateau / high-accel: recalc 341 / 31 µs, parse (`gc_execute_block`,
exclusive) 66 / 68, `st_prep_buffer` 62 / 64, `protocol_execute_realtime` (exclusive) 52 / 52,
`ok` output 45 / 53, `plan_buffer_line` (exclusive) 30 / 29, stream read 15 / 14, stepper ISR 4 / 3.

**Fix:** `plan_block_t` caches `max_delta_speed_sqr = 2 · acceleration · millimeters`, set in
`plan_buffer_line()` and refreshed in `st_prep_buffer()` where the executing block's
`millimeters` shrinks - the only two writers of either field. `planner_recalculate()` reads it
instead of multiplying twice per block per pass. Same operands, same order of operations, so plans
are bit-identical. Costs 4 bytes per planner block. Per-block recalculation cost 2.49 → 1.74 µs
(-30%); plateau 1639 → 1773 lines/s (+8% - in this regime rate scales roughly with the cube root
of per-block cost, because a faster line lengthens the ramp).

**Tried and rejected:** running `planner_recalculate()` from RAM (`ISR_FUNC`) - no gain (341 →
349 µs, within noise). The loop is bound by software floating point on the FPU-less M0+, not by
XIP fetches. Note that a rebuild alone moves unrelated sections by ±10-15% (code layout vs. the
16 KB XIP cache), so single-build differences smaller than that are not significant.

**Verified:** simulator 26/26, hardware 24/24 (+2 sim-only), and exact final position after 5000
same-direction moves at 60000 mm/min / 5000 mm/s² and after 1000 alternating ±0.01 moves.

**Not a bug, but worth knowing:** after 5000 `G91` `+0.01` moves the board reports X=49.9960, one
step short of 50. The parser accumulates incremental targets in float32 and `plan_buffer_line()`
`lroundf()`s the absolute target (`planner.c`), so the sum is 49.9976 → 12499 steps. A float32
simulation reproduces both 3000 → 30.0000 and 5000 → 49.9960 exactly; it is independent of speed
and settings, and upstream behaviour. `hw_line_rate.py` computes its expected position the same way.

**Still open:**
* `ok` output costs 45-53 µs per line: the RP2040 driver's `usb_out_chars()` runs `tud_task()`
  and `tud_cdc_write_flush()` on every write, so each 4-byte response is its own USB packet.
  17% of each line at high acceleration. Driver change.
* An RP2350 (Pico 2, hardware single-precision FPU) should cut the recalc cost far more than any
  code change here. Not measured.

## 2.4 Extension opportunities

* **A POSIX host driver — DONE.** `planner.c:285` already referenced "the grblHAL simulator"
  when this was written. `test/sim_driver.c` (a `hal` contract stub) and `test/run_tests.sh`
  (the regression suite it runs) delivered exactly this - see §2.2(13) and §1.8. This was the
  highest-leverage item on the list and was worth doing rather than leaving as a suggestion.
* **Fuzz `gcode.c` and `ngc_expr.c`.** Both are effectively pure functions of an untrusted
  string and are the natural first target now that a host build exists. Not attempted in this
  pass - setting up a real fuzzing harness (corpus, libFuzzer/AFL integration, a CI job to run
  it) is substantial, separately-scoped work, not a fix to fold into a findings sweep.
* **Add a `Doxyfile`.** There are 290 `__DOXYGEN__` guards across the headers and no config
  to consume them. Not attempted in this pass, for the same reason.
* **A build matrix** over `N_AXIS` × `COMPATIBILITY_LEVEL` × kinematics, which is where
  option-combination breakage actually lives. The existing CI matrix (§1.7) covers `N_AXIS`
  (4/8) and `COMPATIBILITY_LEVEL` (1/2) already; it does not yet build any of the
  `KINEMATICS_API` variants (`COREXY`, `WALL_PLOTTER`, `DELTA_ROBOT`, `POLAR_ROBOT`,
  `RTCP_AC`, `ASYMMETRIC_GANGING`, `ASYMMETRIC_AUTO_SQUARE`). Not attempted in this pass -
  worth its own step, since it means auditing what board config each kinematics variant
  actually needs to compile standalone, not just adding a matrix row.

---

# Part 3 — Code review findings (RP2040 driver)

Reviewed at `DavidLDawes/RP2040` commit `e34f9b6`, which matches upstream. As
with Part 2, these are from reading, not from running. File:line references are
relative to the driver repo.

## 3.1 Confirmed defects

### 1. `ioports_analog.c:227` — `pwm_values` is allocated by PWM count but indexed by port ordinal — MEDIUM — **FIXED**

The array is allocated with one slot per *PWM-capable* output:

```c
pwm_values = calloc(n_pwm, sizeof(float));
```

but it is indexed by *port ordinal* in both places it is used:

```c
:75   return pwm_values && output->id < analog.out.n_ports ? pwm_values[output->id] : -1.0f;
:82   pwm_values[aux_out_analog[port].id - Output_Analog_Aux0] = value;
```

The bound checked is `analog.out.n_ports` (all analog outputs), not `n_pwm`. As
soon as a board has an analog output that is not PWM-backed, `n_pwm <
analog.out.n_ports` and line 82 writes past the end of the allocation — a heap
overflow — while line 75 reads past it.

The sibling array `pwm_data` gets this right, indexing through the dedicated
`aux_out_analog[i].pwm_idx` (lines 83, 158) that is assigned at line 232.
`pwm_values` should use `pwm_idx` too, or be sized `analog.out.n_ports`.

Config-dependent: harmless on a board where every analog output is PWM, which is
why it has survived.

**Fix (DavidLDawes/RP2040#4):** both functions now index through `pwm_idx` and
gate on `mode.pwm || mode.servo_pwm`, matching `pwm_data`'s existing pattern.
Still config-dependent in the opposite direction now: no board defines a
non-PWM analog output, so there is nothing to reproduce the overflow against.
Verified on hardware only that the fix doesn't regress the one config that does
exist - `M67`/`M68` write-then-readback on PICO_CNC's single PWM analog output,
both correct. This is a defensive fix for a latent bug, not a demonstrated
crash-to-fixed pair like §2.1(3).

### 2. `ioports_analog.c:83` — `pwm_data` NULL check missing on the hot path — LOW/MEDIUM — **FIXED**

`analog_out()` guards `pwm_values` before writing:

```c
:81   if(pwm_values)
:82       pwm_values[...] = value;
:83   pwm_set_gpio_level(..., ioports_compute_pwm_value(&pwm_data[...], value));
```

but line 83 dereferences `pwm_data` unguarded. The init path *does* check it
(`if(aux_out_analog[i].mode.pwm && !!pwm_data)`, line 231), so the author was
aware it can be NULL. Asymmetric guard: if the `pwm_data` calloc at line 226
fails, the first `M67` writes through NULL.

Line 83 also runs for ports where `mode.pwm` is false, using a `pwm_idx` that was
never assigned for that port.

### 3. `driver.c:2478` — unbounded busy-wait with no escape — LOW (optional feature) — **FIXED**

```c
static void _write (void)
{
    while(neop.busy);
    ...
}
```

No timeout and, unlike the I²C path, no `hal.stream_blocking_callback()` pump. If
the NeoPixel DMA completion never lands, the controller wedges permanently and
cannot even be soft-reset, because realtime commands stop being serviced. Only
compiled when `NEOPIXELS_PIN` is defined.

Compare `i2c.c:159`, which does the same wait correctly:

```c
while(tx.busy) {
    if(!hal.stream_blocking_callback())
        return false;
}
```

### 4. `i2c.c:152` — `// TODO: add timeout handling` — LOW — **FIXED**

The author's own note, and it is a real gap: `i2c_send()` has no timeout, so a
stuck or absent I²C device blocks. The `stream_blocking_callback()` pump means
the machine stays responsive, so this is a hang rather than a lockup — but a
Modbus/expander fault should fail the operation, not wait forever.

**Fix (DavidLDawes/RP2040#4):** every blocking pico-sdk I2C call in the file -
not just `i2c_send()`, also `i2c_probe()`, `i2c_receive()`, `i2c_get_keycode()`
and `i2c_transfer()` - swapped for its `*_timeout_us()` equivalent at a shared
50ms timeout. Auditing every call site for this turned up two more real bugs in
`i2c_transfer()`, both fixed alongside it and recorded as finding 7 below.
Compile-verified with `I2C_ENABLE=1`. **Not exercised on hardware** - no I2C
device is attached to the bench board, and proving the timeout actually fires
would need a genuinely stuck bus to test against.

### 5. `i2c.c` `i2c_transfer()` — two logic bugs found while fixing finding 4 — MEDIUM — **FIXED, found while fixing #4**

Not in the original review; surfaced while auditing every blocking I2C call
site for the timeout fix above.

**Unchecked address write before a dependent read.** The read branch fired
`i2c_read_blocking()` unconditionally after `i2c_write_blocking()` had sent the
word address, with the write's result never checked at all. A failed or
timed-out address write leaves the bus in an undefined state for the read that
follows it in the same transaction. Fixed: the read now only happens if the
write succeeded.

**`bool`/`int` type confusion always swallowed non-blocking send failures.**
The non-blocking write branch did:

```c
ok = i2c_send(i2c->address, txbuf, i2c->count + i2c->word_addr_bytes, false) != PICO_ERROR_GENERIC;
```

`i2c_send()` returns `bool` (0 or 1). `PICO_ERROR_GENERIC` is `-1`, from a
completely different family of pico-sdk `int` return codes. Neither `0` nor `1`
ever equals `-1`, so this comparison was **always true** regardless of whether
the send actually succeeded - every failure on this path was silently
swallowed. Fixed: uses `i2c_send()`'s own `bool` result directly.

Same session also fixed the sibling `!= PICO_ERROR_GENERIC` loose-check bug in
`i2c_probe()` and `i2c_receive()` (would have treated `PICO_ERROR_TIMEOUT`, -2,
as success - undermining finding 4's fix the moment a timeout actually fired).
Compile-verified only, same as finding 4.

## 3.2 Quality and simplicity

### 6. Board selection requires editing a tracked file — **FIXED** (practice, not a diff)

`CMakeLists.txt:29` — `set(PICO_BOARD pico CACHE STRING "Board type")`, with the
comment *"Select the correct board in VSCode, the following line will be updated
accordingly"*. Selecting a board means editing a tracked file, so every working
tree permanently diverges from upstream and every `git pull` risks a conflict on
that line. Our own clone carried exactly this diff at review time.

It is already a `CACHE` variable, so `-DPICO_BOARD=pico2` on the command line
works and was verified — CI relies on it. `build.sh`/`flash.sh` (§1.3, §1.4) now
pass `-DPICO_BOARD` on every invocation instead of editing this line, so the
tracked default stays at upstream's `pico` and the working tree no longer
diverges - confirmed: `CMakeLists.txt:29` is untouched. A `CMakePresets.json`
with one preset per board remains a nice-to-have for giving the VS Code
extension something to select, but the actual divergence this finding
described is resolved.

### 7. `driver.c:3286` — declaration directly after a `case` label — **FIXED**

```c
case PinGroup_SpindleIndex:
    uint32_t rpm_count = encoder_ovf | pwm_get_counter(encoder_pwm);
```

A label must be followed by a statement, not a declaration, before C23. GCC
accepts it, but it is gratuitously non-portable in a codebase that targets 15+
toolchains. Braces around the case body fix it.

## 3.3 What the driver gets right

Worth recording so it does not get "fixed":

* Hot paths are correctly decorated with `__not_in_flash_func()` — the stepper
  ISR, `stepperPulseStart`, `stepperCyclesPerTick`, the GPIO and systick ISRs all
  run from RAM rather than XIP flash. This matters a great deal on RP2040/RP2350.
* `stepperPulseStart()` is genuinely minimal: two branches and two port writes.
* Step/dir output is done with `gpio_put_masked()` in a single write where the
  pin map allows, rather than per-pin toggling.
* Pin debounce is pushed to the foreground via `task_add_delayed()` rather than
  being done with delays in the ISR.

---

# Part 4 — Findings that only appear when the two are combined

This is the part that neither repo's own review surfaces.

## 4.1 `hal.irq_disable()` / `irq_enable()` do not nest, and ISR-callable core code calls them — HIGH — **FIXED**

Part 2 §2.2(9) flagged that the HAL contract has no save/restore. The driver
confirms it concretely:

```c
driver.c:2835   hal.irq_enable  = __enable_irq;
driver.c:2836   hal.irq_disable = __disable_irq;
```

These are the raw CMSIS intrinsics — they clear and set `PRIMASK`
unconditionally. There is no "restore previous state" anywhere in the chain, and
`hal.h` does not document the constraint. The driver's own `bitsSetAtomic()`,
`bitsClearAtomic()` and `valueSetAtomic()` (`driver.c:1976-2001`) are built the
same way.

The consequence is that the "atomic" helpers are **not atomic when nested**, and
any core code that calls them from inside an outer critical section silently ends
that section early.

Concrete, verified call paths into these from interrupt context:

| Path | Where |
|---|---|
| serial/USB RX ISR → `protocol_enqueue_realtime_command()` → `system_set_exec_state_flag()` → `hal.set_bits_atomic` | core `protocol.c:823` (`ISR_CODE`), `system.h:343` |
| control signal ISR → `control_interrupt_handler()` → `system_set_exec_state_flag()` | core `system.c:84` (`ISR_CODE`) |
| limit switch ISR → `limit_interrupt_handler()` | core `machine_limits.c:84` (`ISR_CODE`) |
| GPIO ISR → `task_add_delayed()` → `hal.irq_disable()` / `hal.irq_enable()` | driver `driver.c:3263`, core `grbllib.c` |

To be precise about severity: on Cortex-M, `__enable_irq()` inside an ISR does
**not** re-enter the same interrupt — the NVIC blocks that until the handler
returns. So this is not an instant crash, which is why it has never been noticed.
The real exposure is nesting: an outer `__disable_irq()` region that calls any of
the above loses its protection at the inner `__enable_irq()`, with no diagnostic.

**Fix** — cheap and local, on the driver side:

```c
static void bitsSetAtomic (volatile uint_fast16_t *ptr, uint_fast16_t bits)
{
    uint32_t prim = __get_PRIMASK();
    __disable_irq();
    *ptr |= bits;
    __set_PRIMASK(prim);
}
```

and correspondingly for `hal.irq_enable`/`hal.irq_disable`, which need a
save/restore pair in the HAL contract rather than two independent void functions.
That is an API change in core's `hal.h`, so it touches both repos — which is
exactly why it belongs in Part 4. At minimum, core's `hal.h` should *document*
that these do not nest (it currently documents them backwards; Part 2 §2.2(8)).

## 4.2 Pin activity can drive the stepper ISR into calling `free()` — HIGH

Two findings that look survivable alone compound into something that is not.

* Core allocates deferred work from a **fixed pool of 40** slots
  (`CORE_TASK_POOL_SIZE`, `grbllib.c`). Allocation can fail.
* The driver consumes pool slots **from the GPIO ISR** on every debounced pin
  edge (`driver.c:3263`, `task_add_delayed(pin_debounce, input, 40)`).
* Core's stepper ISR, when `task_add_immediate()` fails because the pool is
  exhausted, calls **`free()` from interrupt context** (`stepper.c:524`,
  Part 2 §2.1(2)).

So the chain is: a chattering limit switch or a noisy aux input floods the GPIO
ISR → the 40-slot pool drains → the next motion-synchronised message in the
stepper ISR fails to enqueue → `free()` runs inside a ~300 kHz interrupt handler
→ heap corruption if the foreground happened to be inside `malloc`/`free`.

Electrical noise on an input is exactly the condition under which you least want
heap corruption. Neither repo's code looks wrong on its own; the coupling is
invisible unless you read both.

Mitigations, cheapest first:

1. Make `stepper.c:524` leak the message instead of freeing it in the ISR. One
   line, removes the dangerous outcome entirely.
2. Have the driver degrade more gracefully when `task_add_delayed()` fails —
   currently it silently falls through to handling the edge inline *without*
   debounce and without disabling the IRQ, which under a pin storm is the worst
   moment to stop debouncing.
3. Consider whether 40 slots is right when a single noisy input can consume them,
   and/or reserve a slot class for the stepper path.

## 4.3 CI now builds the fork, not upstream

`.github/workflows/build.yml` clones `DavidLDawes/RP2040` (`DRIVER_REPO`), so
driver-side fixes are exercised by core CI before they go anywhere near upstream.
Both halves of the firmware are therefore under test together.

The matrix does not yet build the driver's *own* PRs — that would need a matching
workflow in the driver repo pointing back at `DavidLDawes/core`. Worth adding
once driver changes actually start.

---

# Part 5 — Where to start, and how to progress

Ordered so that each step makes the next one cheaper or safer. Everything in
Parts 2–4 is still open.

### Step 1 — Land the two one-line safety fixes — **DONE**

Do these first because they are small, independently verifiable, and remove the
two worst outcomes in the whole list.

1. **`stepper.c:524`** — stop calling `free()` in the stepper ISR; leak the
   message instead. Kills §4.2's bad ending outright.
2. **`settings.c:3227`** — fix the `realloc` failure path so one OOM cannot
   permanently poison `setting_get_description()` into a NULL dereference
   (Part 2 §2.1(1)).

Both are in core, both are contained, and CI already proves they compile across
six configurations. This is also a good calibration exercise for the workflow:
branch on `DavidLDawes/core`, PR, watch CI, merge.

### Step 2 — Stand up the host-side build (the unlock) — **DONE**, see §1.8

The single highest-leverage item on the list, and the reason to do it before the
larger fixes. `planner.c:285` already refers to "the grblHAL simulator", so the
idea has precedent.

A stub implementing the `hal` contract — enough to link `grbl_enter()` on a PC —
would:

* turn the CI you now have from a *compile* check into an actual *test* suite;
* let every remaining finding be demonstrated with a failing test before the fix
  and a passing one after, instead of argued from code reading;
* make the parser and NGC expression evaluator fuzzable (Part 2 §2.4), which is
  where malformed-input bugs will be;
* remove the "verified as builds, not as runs" caveat that currently applies to
  everything here.

Scope it small: no motion, no timers, a null stream, and enough of `hal` to get
`gc_execute_block()` and `plan_buffer_line()` callable. Everything else can grow
later.

### Step 3 — Fix the interrupt-nesting contract (§4.1) — **DONE**

Needs both repos, which is why it comes after the harness exists.

1. In core, change `hal.h` so the contract is a save/restore pair rather than two
   independent voids, and fix the swapped Doxygen comments (Part 2 §2.2(8)).
2. In the driver, implement it with `__get_PRIMASK()` / `__set_PRIMASK()` and fix
   the three `*Atomic` helpers the same way.
3. Note it in `changelog.md` — it is an ABI change for every other driver.

This is the change most likely to need real hardware to trust, which is the next
argument for Step 4.

### Step 4 — Get a board and actually run it — **DONE**, see §1.4 and §1.9

Done on an original Pico (RP2040) rather than a Pico 2. 21/21 on the board and a
realtime stress test during motion pass. The board has nothing attached, so this
exercises the firmware, not a machine.

### Step 4.5 — Unplanned: a critical hardware-only bug in core, found while starting Step 5 — **DONE**

Bringing up M62/M63 output-command testing on hardware (in service of Step 5's
`ioports_analog.c` work) surfaced a HardFault-on-hardware use-after-free in core's
output-command list ownership (§2.1(3)) - a bug the simulator cannot reproduce, since it
has no real ISR/foreground concurrency. Fixed and verified with a dedicated hardware
regression test (`test/hw_output_commands.py`) that reproduces the fault on unfixed code
and passes 3000 moves cleanly on fixed code. This was higher priority than continuing
Step 5 once found, since it is a HIGH-severity crash reachable by ordinary use of M62-M65
with motion.

### Step 5 — The driver defects (Part 3) — **DONE**

All of Part 3 fixed in DavidLDawes/RP2040#4: the `ioports_analog.c` `pwm_values`
indexing bug (§3.1(1), §3.1(2)) - a defensive fix, since no board configuration
exists that makes `n_pwm < n_ports` to reproduce the overflow against - the
NeoPixel busy-wait (§3.1(3)) and the I2C timeout TODOs (§3.1(4)), plus two
additional `i2c_transfer()` logic bugs found while auditing every blocking I2C
call for the timeout fix (§3.1(5)), and the case-label portability fix
(§3.2(7)). Board selection (§3.2(6)) was already resolved in practice via
`build.sh`/`flash.sh` passing `-DPICO_BOARD` rather than editing the tracked
default.

Verified: a full default-configuration build was flashed and the 24-test suite
passed on hardware; the analog-output and NeoPixel fixes were additionally
verified against real hardware behaviour (write/readback round-trip, M150
timing), not just compiled. The I2C fixes and the overflow-shaped
`ioports_analog.c` bug are compile-verified only - no I2C device is attached to
the bench, and no board configuration reaches the overflow path.

### Step 6 — Performance work (Part 2 §2.3) — **DONE**

All four items fixed: the `steps_per_mm` reciprocal cache (§2.3(1)), the AMASS shift-loop
change-detection guard (§2.3(2)), the task-pool free list (§2.3(3)), and `report_bitfield()`'s
malloc-free elimination (§2.3(4)). Used exactly the caution this entry originally called for -
the harness from Step 2 and hardware from Step 4 to measure against, not code reading alone -
and it paid off: the "obvious," idiomatic-looking approach for the `steps_per_mm` cache (a
`grbl.on_settings_changed` hook, following this codebase's own documented plugin pattern)
turned out to be silently broken by a pre-existing core bug (§2.1(7), found and confirmed on
hardware, not fixed - out of scope for a performance pass). The shipped fix uses a
self-contained memoized-on-use design instead, verified correct by reading the cache's actual
value out of RAM over SWD before and after a runtime settings change.

The AMASS fix - the one item that genuinely touches the stepper ISR's step-count-affecting path
- got the most scrutiny: verified with a 3000-move adversarial test (each move entirely within
its own accel/decel ramp, maximizing AMASS-level churn) asserting the net position lands on
exactly zero, plus a separate cruise-phase move exercising the skip path many segments in a row.
Both hold exactly.

Two items (task-pool free list, AMASS loop) were verified for **correctness under real hardware
interrupt load**, not for a measured cycle-level improvement - the RP2040's Cortex-M0+ has no
DWT cycle counter, so a precise jitter/latency number was not obtainable on this hardware.

### Step 7 — The late-registered settings-changed hook bug (§2.1(7)) — **DONE**

Fixed by making `settings_store_setting()` dispatch `global_settings` changes through the live
`grbl.on_settings_changed` chain directly, dropping the frozen `set->on_changed` snapshot -
option (b) from this entry's original write-up, chosen because it fixes the mechanism itself
rather than narrowing the window until the next late hook. Audited every `set->on_changed`
reader/writer in settings.c first to confirm no other call site depends on the old
snapshot-at-init timing; the only reader was the one call site being changed, and non-global
setting groups (their own dedicated `on_changed`, set directly by the registering plugin) don't
go through this mechanism at all, so they're unaffected.

Verified decisively on hardware, not just by reading: built and flashed the fix, then used a
hardware breakpoint on `gcode.c`'s `onSettingsChanged` (installed by `gc_init()`, the same
registration-timing pattern documented in this finding) over SWD/GDB. Before the fix, `$0=8`
returned `ok` immediately and the breakpoint never fired. After the fix, the identical write
hung until resumed, with a backtrace confirming `system_execute_line` → `settings_store_setting`
→ the live `grbl.on_settings_changed` → `onSettingsChanged`. Full write-up and backtrace in
§2.1(7). Host regression suite (24/24) unaffected.

### Follow-ups from running on hardware

* **Repoint the driver fork's `grbl` submodule at `DavidLDawes/core`.** A fresh
  `--recursive` clone of the driver currently builds *upstream* core, without our
  fixes; local builds work around it by hand (§1.6). Small change, but a
  deliberate divergence from upstream's driver.
* **Demonstrate §4.1, not just its absence of harm.** The stress test (§1.9)
  shows the critical-section code survives load; showing the original bug needs a
  deliberately nested caller — e.g. a test build that enters `hal.irq_disable()`,
  triggers a path that calls `task_add_immediate()`, and checks PRIMASK after.
* **Update the Debug Probe firmware** (1.0.1 → current). OpenOCD works around
  the old version at reduced speed.
* **`$RST=$` before wiring real switches.** The bench settings in §1.4 invert
  every input; with real normally-closed switches attached they would be wrong.

### Not recommended yet

Upstreaming anything to `grblHAL/*`. Build the case with tests and hardware
results first; a PR to a maintained project lands far better with a reproduction
than with a code-reading argument.
