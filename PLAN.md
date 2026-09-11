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

Last updated 2026-09-10.

| # | Item | Status |
|---|---|---|
| 1 | Local cross-compilation toolchain (§1.2) | **done** |
| 2 | Clean build → `grblHAL.uf2` for Pico 2 (§1.3) | **done** |
| 3 | Working notes: `CLAUDE.md`, `README.md`, `PLAN.md` | **done**, merged in PR #1 |
| 4 | GitHub Actions build matrix on PRs and merges (§1.7) | **done**, all 6 jobs green |
| 5 | Review of the RP2040 driver (Part 3) | **done** |
| 6 | Review of the core + driver combination (Part 4) | **done** |
| 7 | Flash and run on real hardware (§1.4) | **not started** — no board yet |
| 8 | Host-side build so the core can be tested without hardware (§1.8) | **done** — 21 tests, run in CI |
| 9 | Part 5 Step 1 — the two HIGH core safety fixes | **done** — §2.1(1) and §2.1(2) |
| 10 | Part 5 Step 2 — host simulator and regression suite (§1.8) | **done** |
| 11 | Remaining code fixes from Parts 2–4 | **not started** |

The toolchain is installed and a full clean build has been verified on this
machine, producing `play/RP2040/build/grblHAL.uf2` (447 KB, family
`0xE48BFF59` = `rp2350-arm-s`, correct for a Pico 2).

**To build, from Git Bash:**

```bash
cd "/c/Users/David Lyman Dawes/play"
./build.sh            # incremental
./build.sh clean      # wipe build dir first
```

Nothing has been flashed or run. Everything below is verified as *builds
correctly*, not as *works on a machine*.

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

## 1.3 Configuration for a Pico 2

Two files decide what gets built:

* **Board selection is passed on the command line**, not edited into the driver's
  tracked `CMakeLists.txt` — that file keeps its upstream default of `pico` so the
  working tree never diverges from the fork (Part 3 §3.2). `build.sh` passes
  `-DPICO_BOARD=pico2`; override per invocation with `PICO_BOARD=pico ./build.sh`.
  This selects the MCU family, so getting it wrong produces link errors rather than
  a subtly wrong binary. Other valid values: `pico`, `pico_w`, `pico2_w`,
  `pimoroni_pga2350` (RP2350B_5X board).
* **`RP2040/my_machine.h`** — every `BOARD_*` left commented out, so pin
  assignments come from `boards/generic_map.h`. `USB_SERIAL_CDC` is on by
  default. This is the right state for proving the toolchain; pick a real board
  map (`BOARD_PICO_CNC`, `BOARD_PICOBOB`, `BOARD_BTT_SKR_PICO_10`, …) once
  hardware is decided.

## 1.4 Flashing and first contact

1. Hold **BOOTSEL** on the Pico 2 while plugging in USB.
2. It mounts as a mass-storage volume (`RP2350`).
3. Copy `play/RP2040/build/grblHAL.uf2` onto it. The board reboots into grblHAL.

Then open a serial terminal on the Pico's USB CDC port — baud rate is ignored on
native USB. Expect:

```
GrblHAL 1.1f ['$' or '$HELP' for help]
```

Useful first commands: `$I` (build info and enabled options), `$$` (settings),
`$HELP`. An alarm on startup is normal and expected — grblHAL defaults to
normally-closed switches, so with nothing wired it starts in alarm. See the note
at the top of README.md.

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
that was built — here, Pico 2 / generic map / 3 axes.

**If you add a new `.c` file to core, add it to core's `CMakeLists.txt`** or
CMake-based drivers will silently not link it.

### Using this clone of core in the build

`play/RP2040/grbl/` is a git submodule pointing at `grblHAL/core`, and it
checked out `516e5ad` — the exact commit `play/core` is on. The verified build
used the submodule copy, not `play/core`.

To build against `play/core` instead, replace the submodule directory with a
junction, from an **elevated** prompt:

```cmd
rmdir /s /q "C:\Users\David Lyman Dawes\play\RP2040\grbl"
mklink /J "C:\Users\David Lyman Dawes\play\RP2040\grbl" "C:\Users\David Lyman Dawes\play\core"
```

Git will then report the submodule as modified in the driver repo. That is
expected; just don't commit it there.

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

---

# Part 2 — Code review findings (core)

Reviewed at commit `516e5ad` (upstream `master`, now our `main`). All findings originate
upstream rather than as local regressions.

Findings marked **FIXED** have been addressed on this fork; the rest are still open.
Line numbers are as of the original review, so they may have shifted slightly in fixed
files.

**Findings are from reading, not from running.** Fixes are verified as *compiling*
across the six CI configurations — nothing has been executed on hardware.

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

### 3. `grbllib.c:489` — `plan_reset()` return value ignored — MEDIUM

`plan_reset()` returns `false` and leaves `block_buffer.blocks == NULL` when the planner
buffer can't be allocated (`planner.c:230-246`), returning *before* `head`/`tail` are
initialised. The caller ignores this. `plan_buffer_line()` then dereferences a NULL
`block_buffer.head` on the first motion.

### 4. `grbllib.c:577` — use-after-free in the systick task walk — MEDIUM

```c
if((task = tasks.systick)) do {
    task->fn(task->data);
} while((task = task->next));
```

`task->next` is re-read *after* `fn()` ran. If the callback deletes itself,
`task_free()` NULLs `->next` and the remaining systick tasks are silently skipped for that
tick. Worse: `task_free()` sets `tasks.last_freed`, so any `task_add_*()` call inside that
same callback hands the identical slot straight back and relinks it — the systick walk then
continues into the immediate or delayed list and runs those callbacks in the wrong context.

Fix: cache `next` before invoking `fn`.

### 5. `protocol.c:76` — unbounded `strcpy` on a public API — MEDIUM

`protocol_enqueue_gcode()` is exposed to plugins as `grbl.enqueue_gcode` and copies
caller-supplied text into `xcommand[LINE_BUFFER_SIZE]` with no length check. `strlcpy` is
already used elsewhere in the tree.

## 2.2 Design and documentation

### 6. `hal.h:638-641` — Doxygen comments swapped — LOW but public

`irq_enable` is documented as "Optional handler to **disable** global interrupts" and
`irq_disable` as "...**enable**...". This is driver-author-facing generated API docs.

### 7. `hal.irq_disable()` / `irq_enable()` don't save and restore the mask — MEDIUM

They are unconditional, so they don't nest: an inner pair re-enables interrupts for the
outer critical section too. Functions marked `ISR_CODE` and documented ISR-callable
(`task_add_immediate`, `task_add_delayed`) end with an unconditional `irq_enable()`, so
calling them from an ISR clears PRIMASK *inside* that ISR. The contract isn't stated
anywhere in `hal.h`.

### 8. Aux I/O driven from the stepper ISR — MEDIUM

`ioport_digital_out()` / `ioport_analog_out()` are called from the stepper ISR
(`stepper.c:513-519`) for M62–M65 motion-synchronised output. An aux port backed by an I²C
or Modbus expander blocks the ISR for milliseconds. Partly known — `on_port_out` says
"might be called from interrupt context" — but nothing prevents a slow port being bound.

### 9. `stepper.c:815` — unguarded `exec_segment` deref — LOW / uncertain

The experimental fast-hold path dereferences `st.exec_segment->n_step` with no NULL check,
while every other use of `exec_segment` in the file is NULL-guarded. Marked experimental.

### 10. `planner.c:401` — hidden state across reset — LOW

`static axes_signals_t direction` persists across soft reset and is only updated for axes
with a non-zero step delta. Likely deliberate, but `plan_reset()` doesn't clear it.

### 11. No CI, no tests, no host-buildable target

For a codebase that moves a machine, this is the largest structural gap. See §2.3.

## 2.3 Optimisation opportunities

* **Cache `1.0f / steps_per_mm` on settings change.** 52 sites divide by
  `settings.axis[i].steps_per_mm`; `plan_buffer_line()` alone does `N_AXIS` divisions per
  block on the queuing path.
* **Hoist the AMASS shift loop out of the ISR** (`stepper.c:563`). It recomputes `N_AXIS`
  shifts on every segment load, but the inputs only change when the block or AMASS level
  changes.
* **Give the task pool a real free list.** `task_alloc()` is an O(40) linear scan with
  interrupts disabled, reachable from ISR context; the single-entry `last_freed` cache only
  helps the immediately-repeated case. `task_add_delayed()` then walks the delayed list for
  ordered insertion, also with interrupts off.
* **`report_bitfield()`** (`report.c:1646`) mallocs, copies, `strtok`s and frees per call
  purely to make a flash string mutable — it can iterate in place.

## 2.4 Extension opportunities

* **A POSIX host driver.** `planner.c:285` already references "the grblHAL simulator". A
  stub implementing the `hal` contract would make the parser, planner and NGC layers
  unit-testable, give CI something to run, and remove the §1.10 constraint that nothing can
  be verified without hardware. Highest-leverage item on this list.
* **Fuzz `gcode.c` and `ngc_expr.c`.** Both are effectively pure functions of an untrusted
  string and are the natural first target once a host build exists.
* **Add a `Doxyfile`.** There are 290 `__DOXYGEN__` guards across the headers and no config
  to consume them.
* **A build matrix** over `N_AXIS` × `COMPATIBILITY_LEVEL` × kinematics, which is where
  option-combination breakage actually lives.

---

# Part 3 — Code review findings (RP2040 driver)

Reviewed at `DavidLDawes/RP2040` commit `e34f9b6`, which matches upstream. As
with Part 2, these are from reading, not from running. File:line references are
relative to the driver repo.

## 3.1 Confirmed defects

### 1. `ioports_analog.c:227` — `pwm_values` is allocated by PWM count but indexed by port ordinal — MEDIUM

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

### 2. `ioports_analog.c:83` — `pwm_data` NULL check missing on the hot path — LOW/MEDIUM

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

### 3. `driver.c:2478` — unbounded busy-wait with no escape — LOW (optional feature)

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

### 4. `i2c.c:152` — `// TODO: add timeout handling` — LOW

The author's own note, and it is a real gap: `i2c_send()` has no timeout, so a
stuck or absent I²C device blocks. The `stream_blocking_callback()` pump means
the machine stays responsive, so this is a hang rather than a lockup — but a
Modbus/expander fault should fail the operation, not wait forever.

## 3.2 Quality and simplicity

### 5. Board selection requires editing a tracked file

`CMakeLists.txt:29` — `set(PICO_BOARD pico CACHE STRING "Board type")`, with the
comment *"Select the correct board in VSCode, the following line will be updated
accordingly"*. Selecting a board means editing a tracked file, so every working
tree permanently diverges from upstream and every `git pull` risks a conflict on
that line. Our own clone carries exactly this diff today.

It is already a `CACHE` variable, so `-DPICO_BOARD=pico2` on the command line
works and was verified — CI relies on it. Leaving the tracked default alone and
passing `-D` (or a CMake preset) removes the divergence entirely. A
`CMakePresets.json` with one preset per board would be the tidy version and would
also give the VS Code extension something to select.

### 6. `driver.c:3286` — declaration directly after a `case` label

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

## 4.1 `hal.irq_disable()` / `irq_enable()` do not nest, and ISR-callable core code calls them — HIGH

Part 2 §2.2(7) flagged that the HAL contract has no save/restore. The driver
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
that these do not nest (it currently documents them backwards; Part 2 §2.2(6)).

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

### Step 3 — Fix the interrupt-nesting contract (§4.1) — **NEXT**

Needs both repos, which is why it comes after the harness exists.

1. In core, change `hal.h` so the contract is a save/restore pair rather than two
   independent voids, and fix the swapped Doxygen comments (Part 2 §2.2(6)).
2. In the driver, implement it with `__get_PRIMASK()` / `__set_PRIMASK()` and fix
   the three `*Atomic` helpers the same way.
3. Note it in `changelog.md` — it is an ABI change for every other driver.

This is the change most likely to need real hardware to trust, which is the next
argument for Step 4.

### Step 4 — Get a Pico 2 and actually run it

Everything so far is verified as *builds correctly*, never as *works*. A $5 board
converts the whole exercise from static review to something testable, and is a
prerequisite for trusting Step 3. §1.4 has the flashing procedure.

### Step 5 — The driver defects (Part 3)

`ioports_analog.c`'s `pwm_values` indexing (§3.1(1)) is the real one; it needs a
board with a non-PWM analog output to bite, so pair it with a CI matrix entry for
such a board rather than fixing it blind. The NeoPixel busy-wait and the I²C
timeout TODO are reliability polish.

### Step 6 — Performance work (Part 2 §2.3)

Deliberately last. Every item — the `steps_per_mm` reciprocal cache, hoisting the
AMASS shift out of the ISR, the task-pool free list — is a change to hot,
hard-real-time code, and none should be attempted without the harness from Step 2
and hardware from Step 4 to measure against. Optimising a 300 kHz ISR on the
strength of code reading alone is how jitter bugs get introduced.

### Not recommended yet

Upstreaming anything to `grblHAL/*`. Build the case with tests and hardware
results first; a PR to a maintained project lands far better with a reproduction
than with a code-reading argument.
