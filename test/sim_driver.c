/*
  sim_driver.c - a host-side "driver" for grblHAL, for testing the core off-target

  Part of grblHAL

  Copyright (c) 2026 grblHAL contributors

  grblHAL is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.

  grblHAL is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with grblHAL. If not, see <http://www.gnu.org/licenses/>.
*/

/*
  This implements just enough of the HAL contract to link and run grbl_enter()
  as an ordinary PC program, so the g-code parser, planner, settings and
  reporting layers can be exercised without hardware.

  It is deliberately NOT a machine simulator: steps are counted, not timed, and
  the stepper "interrupt" is driven synchronously from the foreground. That is
  enough for parser/planner/protocol tests and keeps the harness small. Anything
  needing real timing still needs a board.

  Input is read from stdin, output written to stdout. When stdin is exhausted the
  harness injects CMD_EXIT (ctrl-C), which sets sys.flags.exit and unwinds
  grbl_enter() cleanly, so the process terminates with a normal exit code instead
  of spinning in the main loop.
*/

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "../hal.h"
#include "../protocol.h"
#include "../state_machine.h"
#include "../nvs_buffer.h"
#include "../grbllib.h"

/* The core uses the BSD string functions. glibc only gained strlcpy in 2.38,
   so supply it when the host libc does not (CMake decides, see CMakeLists.txt). */
#ifdef SIM_NEED_STRLCPY
size_t strlcpy (char *dst, const char *src, size_t size)
{
    size_t len = strlen(src);

    if(size) {
        size_t n = len < size - 1 ? len : size - 1;
        memcpy(dst, src, n);
        dst[n] = '\0';
    }

    return len;
}
#endif

#ifndef SIM_RX_BUFFER_SIZE
#define SIM_RX_BUFFER_SIZE 4096
#endif

static uint8_t rxbuf[SIM_RX_BUFFER_SIZE];
static size_t rx_len = 0, rx_pos = 0;
static uint32_t starved_reads = 0;
static uint32_t sim_ticks = 0;

/* A test harness must never be able to hang CI. If the core keeps polling for
   input long after the script ran out and the injected CMD_EXIT did not unwind
   it, give up loudly rather than spinning forever. */
#ifndef SIM_MAX_STARVED_READS
#define SIM_MAX_STARVED_READS 2000000
#endif

// Counts of things the core asked the "hardware" to do. Exposed so tests can
// assert that motion actually reached the stepper layer rather than only parsing.
static struct {
    uint32_t pulses;
    uint32_t wake_ups;
    uint32_t go_idles;
} sim_stats;

static enqueue_realtime_command_ptr enqueue_realtime_command = protocol_enqueue_realtime_command;

/* --- stream ------------------------------------------------------------- */

static bool streamIsConnected (void)
{
    return true;
}

static uint16_t streamRxFree (void)
{
    return (uint16_t)(SIM_RX_BUFFER_SIZE - (rx_len - rx_pos));
}

static int32_t streamGetC (void)
{
    // A real driver filters realtime commands out of the receive stream in its RX
    // interrupt handler, so only ordinary characters ever reach the line buffer.
    // Reproduce that here, otherwise CMD_EXIT and friends are handed to the line
    // parser as data and are never acted on.
    while(rx_pos < rx_len) {

        uint8_t c = rxbuf[rx_pos++];

        starved_reads = 0;

        if(!enqueue_realtime_command(c))
            return (int32_t)c;
    }

    if(++starved_reads > SIM_MAX_STARVED_READS) {
        fflush(stdout);
        fprintf(stderr, "grbl_sim: core did not exit after CMD_EXIT - aborting\n");
        exit(2);
    }

    // Input exhausted - ask the core to shut down, then report no data so the main
    // loop reaches its realtime check point and unwinds. Injected on every starved
    // read rather than once: a soft reset re-enters the main loop, and a one-shot
    // injection would leave the core spinning on an empty stream forever.
    // mc_reset() ignores repeats while EXEC_RESET is already pending.
    enqueue_realtime_command(CMD_EXIT);

    return SERIAL_NO_DATA;
}

static bool streamPutC (const uint8_t c)
{
    fputc(c, stdout);

    return true;
}

static void streamWriteS (const char *s)
{
    fputs(s, stdout);
}

static void streamWriteN (const uint8_t *s, uint16_t len)
{
    fwrite(s, 1, len, stdout);
}

static void streamFlushRx (void)
{
    // Discarding queued input would drop the rest of the test script, which is
    // never what a test wants. Deliberately a no-op.
}

static void streamCancelRx (void)
{
    streamFlushRx();
}

static enqueue_realtime_command_ptr streamSetRtHandler (enqueue_realtime_command_ptr handler)
{
    enqueue_realtime_command_ptr prev = enqueue_realtime_command;

    if(handler)
        enqueue_realtime_command = handler;

    return prev;
}

/* --- steppers ----------------------------------------------------------- */

static void stepperWakeUp (void)
{
    sim_stats.wake_ups++;
}

static void stepperGoIdle (bool clear_signals)
{
    sim_stats.go_idles++;
}

static void stepperEnable (axes_signals_t enable, bool hold)
{
}

static void stepperCyclesPerTick (uint32_t cycles_per_tick)
{
}

static void stepperPulseStart (stepper_t *stepper)
{
    sim_stats.pulses++;
}

/* --- inputs ------------------------------------------------------------- */

static void limitsEnable (bool on, axes_signals_t homing_cycle)
{
}

static limit_signals_t limitsGetState (void)
{
    limit_signals_t signals = {0};

    return signals;
}

static control_signals_t systemGetState (void)
{
    control_signals_t signals = {0};

    // Nothing is wired, so report everything inactive. Without this the core
    // would start in alarm because grblHAL defaults to normally-closed inputs.
    return signals;
}

/* --- coolant ------------------------------------------------------------ */

static void coolantSetState (coolant_state_t mode)
{
}

static coolant_state_t coolantGetState (void)
{
    coolant_state_t state = {0};

    return state;
}

/* --- auxiliary digital outputs ----------------------------------------------

   A few fake digital outputs, so M62-M65 are accepted and the output command
   paths in the parser, planner and stepper can be tested. Output state is just
   recorded; nothing is driven.
*/

#ifndef SIM_N_AUX_OUT
#define SIM_N_AUX_OUT 4
#endif

static io_ports_data_t digital;
static bool aux_out_state[SIM_N_AUX_OUT];
static const char *aux_out_description[SIM_N_AUX_OUT];

static void digitalOut (uint8_t port, bool on)
{
    if(port < digital.out.n_ports)
        aux_out_state[port] = on;
}

static float digitalOutState (xbar_t *output)
{
    return output->id < digital.out.n_ports ? (float)aux_out_state[output->id] : -1.0f;
}

static xbar_t *getPinInfo (io_port_direction_t dir, uint8_t port)
{
    static xbar_t pin;

    if(dir != Port_Output || port >= digital.out.n_ports)
        return NULL;

    // Mirrors what XBAR_SET_DOUT_INFO() fills in for a real driver.
    memset(&pin, 0, sizeof(xbar_t));
    pin.id = port;
    pin.mode.output = On;
    pin.cap.mask = pin.mode.mask;
    pin.cap.invert = On;
    pin.cap.claimable = On;
    pin.function = (pin_function_t)(Output_Aux0 + port);
    pin.group = PinGroup_AuxOutput;
    pin.pin = port;
    pin.description = aux_out_description[port];
    pin.get_value = digitalOutState;

    return &pin;
}

static void setPinDescription (io_port_direction_t dir, uint8_t port, const char *description)
{
    if(dir == Port_Output && port < digital.out.n_ports)
        aux_out_description[port] = description;
}

static void simIoportsInit (void)
{
    io_digital_t ports = {
        .ports = &digital,
        .digital_out = digitalOut,
        .get_pin_info = getPinInfo,
        .set_pin_description = setPinDescription
    };

    digital.out.n_ports = SIM_N_AUX_OUT;

    ioports_add_digital(&ports);
}

/* --- misc --------------------------------------------------------------- */

static void simDelayMs (uint32_t ms, delay_callback_ptr callback)
{
    // Time is not simulated; advance the tick counter so timeouts still expire
    // and anything polling elapsed time makes progress.
    sim_ticks += ms;

    if(callback)
        callback();
}

static uint32_t simGetElapsedTicks (void)
{
    return ++sim_ticks;
}

static uint64_t simGetMicros (void)
{
    return (uint64_t)sim_ticks * 1000;
}

static void simSetBitsAtomic (volatile uint_fast16_t *ptr, uint_fast16_t bits)
{
    *ptr |= bits;
}

static uint_fast16_t simClearBitsAtomic (volatile uint_fast16_t *ptr, uint_fast16_t bits)
{
    uint_fast16_t prev = *ptr;

    *ptr &= ~bits;

    return prev;
}

static uint_fast16_t simSetValueAtomic (volatile uint_fast16_t *ptr, uint_fast16_t value)
{
    uint_fast16_t prev = *ptr;

    *ptr = value;

    return prev;
}

static bool simDriverSetup (settings_t *settings)
{
    return true;
}

static bool simDriverRelease (void)
{
    // false ends the outer re-initialisation loop in grbl_enter(), so the
    // process exits instead of restarting the controller.
    return false;
}

/* --- entry point -------------------------------------------------------- */

bool driver_init (void)
{
    hal.info = "grblHAL simulator";
    hal.driver_version = "20260910";
    hal.driver_setup = simDriverSetup;
    hal.driver_release = simDriverRelease;

    hal.rx_buffer_size = SIM_RX_BUFFER_SIZE;
    hal.max_step_rate = 100000;

    hal.delay_ms = simDelayMs;
    hal.get_elapsed_ticks = simGetElapsedTicks;
    hal.get_micros = simGetMicros;

    hal.stepper.wake_up = stepperWakeUp;
    hal.stepper.go_idle = stepperGoIdle;
    hal.stepper.enable = stepperEnable;
    hal.stepper.cycles_per_tick = stepperCyclesPerTick;
    hal.stepper.pulse_start = stepperPulseStart;

    hal.limits.enable = limitsEnable;
    hal.limits.get_state = limitsGetState;

    hal.control.get_state = systemGetState;

    hal.coolant.set_state = coolantSetState;
    hal.coolant.get_state = coolantGetState;

    hal.set_bits_atomic = simSetBitsAtomic;
    hal.clear_bits_atomic = simClearBitsAtomic;
    hal.set_value_atomic = simSetValueAtomic;

    hal.stream.type = StreamType_Serial;
    hal.stream.is_connected = streamIsConnected;
    hal.stream.get_rx_buffer_free = streamRxFree;
    hal.stream.read = streamGetC;
    hal.stream.write = streamWriteS;
    hal.stream.write_all = streamWriteS;
    hal.stream.write_char = streamPutC;
    hal.stream.write_n = streamWriteN;
    hal.stream.reset_read_buffer = streamFlushRx;
    hal.stream.cancel_read_buffer = streamCancelRx;
    hal.stream.set_enqueue_rt_handler = streamSetRtHandler;

    // grbl_enter() runs a driver self-test and raises ALARM:16 (Alarm_SelftestFailed)
    // unless every capability the core was built to expect is claimed here. AMASS must
    // match MAX_AMASS_LEVEL, and step_pulse_delay is required because config.h always
    // defines DEFAULT_STEP_PULSE_DELAY.
    hal.driver_cap.amass_level = MAX_AMASS_LEVEL;
    hal.driver_cap.step_pulse_delay = On;

    simIoportsInit();

    return hal.version == HAL_VERSION;
}

int main (int argc, char **argv)
{
    // Unbuffered so output ordering matches the order the core produced it,
    // which matters when a test greps for a response to a specific line.
    setvbuf(stdout, NULL, _IONBF, 0);

    rx_len = fread(rxbuf, 1, sizeof(rxbuf), stdin);

    return grbl_enter();
}
