/*
 * mpe-fx-host — the effects insert on the trunk, before the fan-out.
 *
 * Surge's output fans out to what you hear and to every loop input. The live
 * monitor sits on one branch of that fan-out; this sits upstream of it, so the
 * loops record what you hear:
 *
 *     Surge XT:out_N ─> [mpe-fx-host:in_N ─ out_N] ─> fan-out ─┬─> [mpe-live-monitor:in_N ─ out_N] ─> system:playback_N
 *                                                              └─> mpe-looper:loopM_in_N
 *
 * JACK runs clients in graph order inside one period, so this adds no buffer
 * latency. The DSP — the effect table, the chain, depth smoothing, the enable
 * crossfades, bypass and the output clamp — is in fx_dsp.c, which has no JACK in it so the
 * self-test can run anywhere.
 *
 * Which effects and how much is not decided here. This process holds no
 * policy: it is told an enable mask, one depth, a level per effect, the
 * bass bypass's crossover and a stage gain over UDP and applies them. Python
 * decides (the FX policy the effects, patch normalization the stage gain);
 * the datagram format has one definition on that side,
 * `patch_browser/fx_host_protocol.py`.
 *
 * Where the audio goes is not decided here either.
 * `patch_browser/audio_routing.py` is the authority; this file cannot import
 * it, so the names and the two rules it needs (`trunk_insert_carrying`,
 * `fanout_source`) are mirrored below, and `tests/test_audio_routing.py`
 * compiles this file against a fake JACK and checks it agrees.
 *
 * RT callback: fx_engine_process only — no syscalls, locks, or allocation.
 * Control thread: one blocking recvfrom with a timeout, storing atomics.
 * Wiring thread: woken by JACK's port-connect and port-registration callbacks
 * (and every 2 s as a backstop); never touches audio.
 *
 * Safety properties, the live monitor's plus one:
 *
 *   1. **The effects add at most FX_MAKEUP_DB (1 dB) over the stage output,
 *      scaled by depth.** The stage gain comes first (fx_dsp.h, "Stage
 *      gain"): it applies whatever gain it is commanded, up to
 *      FX_GAIN_MAX_DB, and starts at unity, or at the last gain a previous
 *      process was told (fx_gain_state.h). Everything after it is held to
 *      this bound against its output. Depth mixes toward the wet signal, never on top of it, and
 *      every effect's wet signal is held to its input's level (each says how
 *      in fx_dsp.c; fx_selftest.c measures it at every knob position). What a
 *      mix loses, a static gain per knob position gives back short of the
 *      input's level (fx_dsp.h, "Level curve"), and the chain of enabled
 *      effects is held to the same bound. The chain then ends in one static
 *      makeup gain, +1 dB times the loudest stage's depth, once per chain
 *      however many effects are on (fx_dsp.h, "Makeup"). Every output
 *      sample is clamped to ±1.0 and NaN becomes silence. Depth 0 or no effect
 *      enabled at unity stage gain — the startup state — is the input, bit
 *      for bit.
 *
 *   2. **It takes the trunk only once it is carrying audio, and gives it back
 *      when it leaves.** Carrying is the authority's rule: Surge reaches our
 *      input on EVERY channel. Only then does our output feed the fan-out —
 *      every loop input, and the live monitor's input or playback — our edge
 *      made before Surge's direct one is broken, so there is never a moment
 *      with no path. The wirers and repairers never disconnect; taking the
 *      direct path away is this insert's job, as it is the monitor's on its
 *      branch. SIGTERM puts Surge back on every destination we fed. A crash
 *      cannot run that code, so the unit's ExecStopPost runs
 *      restore-direct-monitor-path.sh, which asks the authority: our ports are
 *      gone, so the source is Surge again, and every unreached destination gets
 *      it.
 *
 *   3. **It exits non-zero when jackd goes away**, so Restart=on-failure fires.
 */

#define _GNU_SOURCE
#include "fx_dsp.h"
#include "fx_gain_state.h"

#include <arpa/inet.h>
#include <errno.h>
#include <jack/jack.h>
#include <math.h>
#include <netinet/in.h>
#include <pthread.h>
#include <semaphore.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define CONTROL_PORT_DEFAULT 9958
#define CONTROL_TIMEOUT_MS 200
#define WIRING_BACKSTOP_S 2

/* Depth follows the knob with a one-pole lag. CC has 128 steps, so unsmoothed
 * it zippers; much longer and the knob feels disconnected. A patch change with
 * the knob already raised jumps the target, and this is what ramps it. */
#define DEPTH_MS 40.0
/* Enable/disable crossfade: long enough to hide the change of character, short
 * enough that a toggle on a patch change lands before the next note matters. */
#define XFADE_MS 30.0

/* --------------------------------------------------------------------------
 * Names — the authority's (patch_browser/audio_routing.py), mirrored. The only
 * place this process spells a JACK client name; tests/test_audio_routing.py
 * pins each against the Python constant.
 * -------------------------------------------------------------------------- */
#define CLIENT_NAME "mpe-fx-host"            /* audio_routing.TRUNK_INSERT */
#define TRUNK_INSERT CLIENT_NAME
#define MONITOR_INSERT "mpe-live-monitor"    /* audio_routing.MONITOR_INSERT */
#define SURGE_CLIENT_DEFAULT "Surge XT"      /* MPE_SL_SURGE_CLIENT */
#define LOOPER_CLIENT_DEFAULT "mpe-looper"   /* MPE_SL_JACK_CLIENT */
#define LOOPS_DEFAULT 15                     /* MPE_SL_LOOPS */
#define LOOPS_MAX 64
#define CHANNELS FX_CHANNELS
#define PORT_MAX 192

static char g_surge_client[128];
static char g_looper_client[128];
static int g_loops = LOOPS_DEFAULT;

static void surge_out(int ch, char *buf, size_t len)
{
    snprintf(buf, len, "%s:out_%d", g_surge_client, ch);
}

static void playback(int ch, char *buf, size_t len)
{
    snprintf(buf, len, "system:playback_%d", ch);
}

static void loop_in(int loop, int ch, char *buf, size_t len)
{
    snprintf(buf, len, "%s:loop%d_in_%d", g_looper_client, loop, ch);
}

static void monitor_in(int ch, char *buf, size_t len)
{
    snprintf(buf, len, MONITOR_INSERT ":in_%d", ch);
}

static void monitor_out(int ch, char *buf, size_t len)
{
    snprintf(buf, len, MONITOR_INSERT ":out_%d", ch);
}

/* ------------------------------------------------------------------------ */

static jack_client_t *g_client;
static jack_port_t *g_in_ports[CHANNELS];
static jack_port_t *g_out_ports[CHANNELS];
static volatile sig_atomic_t g_running = 1;
static volatile sig_atomic_t g_jack_shutdown = 0;

static fx_engine g_engine;

/* Targets set by the control thread, followed by the RT callback. No effect
 * at depth 0 until something says otherwise: an unconfigured insert must be
 * inaudible, not silent. */
static atomic_uint g_target_mask = 0;
static _Atomic float g_target_depth = 0.0f;
/* Each effect's level, 0..1, by effect id (fx_dsp.h, "Per-effect levels").
 * 1.0 until a `chainl` says otherwise, so `chain` alone plays as it always
 * has; set to 1.0 in main before the client is activated. Each is stored on
 * its own: a period that reads a half-updated set ramps to it all the same. */
static _Atomic float g_target_level[FX_MAX_EFFECTS];
/* The bass bypass's crossover, Hz (fx_dsp.h, "Bass bypass"). Off until a
 * `bass` datagram says otherwise: a sender that predates it gets the chain
 * as it always was. */
static _Atomic float g_target_bass = 0.0f;
/* The stage gain, linear (fx_dsp.h, "Stage gain"). Unity until a `gain`
 * datagram says otherwise, so an unconfigured insert is transparent; but a
 * restarted one starts at the last gain its predecessor was told, read from
 * g_gain_state_path in main before activation (fx_gain_state.h). The control
 * thread writes that file on every change: never the audio callback. */
static _Atomic float g_target_gain = 1.0f;
static char g_gain_state_path[4096] = FX_GAIN_STATE_DEFAULT;
static _Atomic float g_rate = 0.0f;

static int g_control_port = CONTROL_PORT_DEFAULT;

/* Wake the wiring thread on a graph change. Coalesced: a wiring script making
 * thirty connections costs one or two passes, not thirty. */
static sem_t g_wake;
static atomic_int g_wake_pending = 0;

static int process(jack_nframes_t nframes, void *arg)
{
    (void)arg;
    const uint32_t mask = atomic_load_explicit(&g_target_mask, memory_order_relaxed);
    float depth = atomic_load_explicit(&g_target_depth, memory_order_relaxed);
    float levels[FX_MAX_EFFECTS];
    for (int k = 0; k < FX_MAX_EFFECTS; k++) {
        levels[k] = atomic_load_explicit(&g_target_level[k], memory_order_relaxed);
    }
    const float rate = atomic_load_explicit(&g_rate, memory_order_relaxed);

    /* A rate change re-derives coefficients and clears history: no allocation,
     * a memset of the delay lines. A rate the delay lines cannot hold leaves
     * the old voicing in place and bypasses, rather than reading past a buffer. */
    if (rate != g_engine.rate && rate > 0.0f) {
        if (fx_engine_set_rate(&g_engine, rate) != 0) {
            depth = 0.0f;
        }
    }

    jack_default_audio_sample_t *in[FX_CHANNELS];
    jack_default_audio_sample_t *out[FX_CHANNELS];
    for (int ch = 0; ch < FX_CHANNELS; ch++) {
        in[ch] = (jack_default_audio_sample_t *)jack_port_get_buffer(g_in_ports[ch], nframes);
        out[ch] = (jack_default_audio_sample_t *)jack_port_get_buffer(g_out_ports[ch], nframes);
    }
    /* NULL input reads as silence and NULL output is skipped, inside the
     * engine — never an unwritten buffer left at whatever the last period had. */
    const float *const cin[FX_CHANNELS] = { in[0], in[1] };
    float *const cout[FX_CHANNELS] = { out[0], out[1] };
    fx_engine_set_bass(&g_engine, atomic_load_explicit(&g_target_bass, memory_order_relaxed));
    fx_engine_set_gain(&g_engine, atomic_load_explicit(&g_target_gain, memory_order_relaxed));
    fx_engine_process_levels(&g_engine, cin, cout, nframes, mask, depth, levels);
    return 0;
}

static int on_samplerate(jack_nframes_t rate, void *arg)
{
    (void)arg;
    atomic_store_explicit(&g_rate, (float)rate, memory_order_relaxed);
    return 0;
}

static void wake_wiring(void)
{
    if (atomic_exchange_explicit(&g_wake_pending, 1, memory_order_relaxed) == 0) {
        sem_post(&g_wake);
    }
}

/* JACK notification thread, not RT — but no JACK calls are allowed from here,
 * so it only wakes the thread that may make them. */
static void on_port_connect(jack_port_id_t a, jack_port_id_t b, int connect, void *arg)
{
    (void)a;
    (void)b;
    (void)connect;
    (void)arg;
    wake_wiring();
}

static void on_port_registration(jack_port_id_t port, int registered, void *arg)
{
    (void)port;
    (void)registered;
    (void)arg;
    wake_wiring();
}

static void on_shutdown(void *arg)
{
    (void)arg;
    g_jack_shutdown = 1;
    g_running = 0;
    sem_post(&g_wake);
}

static void handle_signal(int sig)
{
    (void)sig;
    g_running = 0;
    sem_post(&g_wake);   /* async-signal-safe */
}

static int port_connected_to(jack_port_t *port, const char *target)
{
    const char **connections = jack_port_get_all_connections(g_client, port);
    if (connections == NULL) {
        return 0;
    }
    int found = 0;
    for (int i = 0; connections[i] != NULL; i++) {
        if (strcmp(connections[i], target) == 0) {
            found = 1;
            break;
        }
    }
    jack_free(connections);
    return found;
}

static int connect_if_absent(const char *source, const char *dest)
{
    jack_port_t *src_port = jack_port_by_name(g_client, source);
    if (src_port != NULL && port_connected_to(src_port, dest)) {
        return 0;
    }
    int rc = jack_connect(g_client, source, dest);
    return (rc == 0 || rc == EEXIST) ? 0 : rc;
}

static int named_port_fed_by(const char *port_name, const char *source)
{
    jack_port_t *port = jack_port_by_name(g_client, port_name);
    return port != NULL && port_connected_to(port, source);
}

/* Mirror of audio_routing.trunk_insert_carrying(): our input fed by Surge on
 * EVERY channel. One channel wet and the other dry is an L/R fault, not a
 * partial success. */
static int trunk_insert_carrying(void)
{
    for (int ch = 1; ch <= CHANNELS; ch++) {
        char src[PORT_MAX];
        surge_out(ch, src, sizeof(src));
        if (!port_connected_to(g_in_ports[ch - 1], src)) {
            return 0;
        }
    }
    return 1;
}

/* Mirror of audio_routing.fanout_source() (ch is 1-based): us while we carry,
 * otherwise Surge. */
static void fanout_source(int ch, char *buf, size_t size)
{
    if (trunk_insert_carrying()) {
        snprintf(buf, size, "%s", jack_port_name(g_out_ports[ch - 1]));
    } else {
        surge_out(ch, buf, size);
    }
}

/* Mirror of audio_routing.monitor_insert_carrying(): the monitor's input fed by
 * the fan-out source and its output on playback, every channel. */
static int monitor_insert_carrying(void)
{
    for (int ch = 1; ch <= CHANNELS; ch++) {
        char src[PORT_MAX], min[PORT_MAX], mout[PORT_MAX], pb[PORT_MAX];
        fanout_source(ch, src, sizeof(src));
        monitor_in(ch, min, sizeof(min));
        monitor_out(ch, mout, sizeof(mout));
        playback(ch, pb, sizeof(pb));
        if (!named_port_fed_by(min, src) || !named_port_fed_by(mout, pb)) {
            return 0;
        }
    }
    return 1;
}

/* Move `dest` from `from` to `to`: make, then break. A dest that does not exist
 * is not an error — the looper may be down, the monitor may be off. Returns 1
 * when this call changed the graph. */
static int move_edge(const char *from, const char *to, const char *dest)
{
    if (jack_port_by_name(g_client, dest) == NULL) {
        return 0;
    }
    int changed = 0;
    if (!named_port_fed_by(dest, to)) {
        int rc = jack_connect(g_client, to, dest);
        if (rc != 0 && rc != EEXIST) {
            /* Leave the old edge: one path, the old one, beats none. */
            fprintf(stderr, "mpe-fx-host: could not connect %s -> %s (rc %d) — "
                            "leaving it on %s\n", to, dest, rc, from);
            return 0;
        }
        changed = 1;
    }
    if (named_port_fed_by(dest, from)) {
        int rc = jack_disconnect(g_client, from, dest);
        if (rc != 0) {
            fprintf(stderr, "mpe-fx-host: could not detach %s -> %s (rc %d) — "
                            "both paths are live on this destination\n", from, dest, rc);
        } else {
            changed = 1;
        }
    }
    return changed;
}

/* Every port the fan-out owns on channel `ch` (1-based), as the authority's
 * `destinations()` lists them — except that the monitor branch is named by the
 * monitor's INPUT when the monitor is on the graph: the fan-out feeds it, and
 * it alone feeds playback while it carries. */
static int for_each_destination(int ch, const char *from, const char *to, int monitor_carrying)
{
    char dest[PORT_MAX];
    int changed = 0;
    for (int m = 0; m < g_loops; m++) {
        loop_in(m, ch, dest, sizeof(dest));
        changed |= move_edge(from, to, dest);
    }
    monitor_in(ch, dest, sizeof(dest));
    changed |= move_edge(from, to, dest);
    if (!monitor_carrying) {
        playback(ch, dest, sizeof(dest));
        changed |= move_edge(from, to, dest);
    } else {
        /* The monitor owns playback now. A direct edge from Surge left there
         * is a second copy; the monitor removes ours, we remove Surge's. */
        playback(ch, dest, sizeof(dest));
        char surge[PORT_MAX];
        surge_out(ch, surge, sizeof(surge));
        if (strcmp(from, surge) == 0 && named_port_fed_by(dest, surge)) {
            if (jack_disconnect(g_client, surge, dest) == 0) {
                changed = 1;
            }
        }
    }
    return changed;
}

/* Take the fan-out: every destination moves from Surge to us. The monitor's
 * input moves first, so "is the monitor carrying" is asked about the graph as
 * the authority will see it once we are the source. */
static int take_trunk(void)
{
    int changed = 0;
    for (int ch = 1; ch <= CHANNELS; ch++) {
        char surge[PORT_MAX], min[PORT_MAX];
        const char *ours = jack_port_name(g_out_ports[ch - 1]);
        surge_out(ch, surge, sizeof(surge));
        monitor_in(ch, min, sizeof(min));
        changed |= move_edge(surge, ours, min);
    }
    const int monitor_carrying = monitor_insert_carrying();
    for (int ch = 1; ch <= CHANNELS; ch++) {
        char surge[PORT_MAX];
        surge_out(ch, surge, sizeof(surge));
        changed |= for_each_destination(ch, surge, jack_port_name(g_out_ports[ch - 1]),
                                        monitor_carrying);
    }
    return changed;
}

/* Give the fan-out back: every destination our output feeds moves to Surge.
 * Only edges we actually feed are touched; with no Surge port to move them to
 * (Surge restarting) they stay — our input is unfed, so they carry silence and
 * the authority already counts them as no path. */
static void give_back_trunk(void)
{
    for (int ch = 1; ch <= CHANNELS; ch++) {
        jack_port_t *ours = g_out_ports[ch - 1];
        const char **dests = jack_port_get_all_connections(g_client, ours);
        if (dests == NULL) {
            continue;
        }
        char surge[PORT_MAX];
        surge_out(ch, surge, sizeof(surge));
        if (jack_port_by_name(g_client, surge) != NULL) {
            for (int k = 0; dests[k] != NULL; k++) {
                if (move_edge(jack_port_name(ours), surge, dests[k])) {
                    fprintf(stderr, "mpe-fx-host: gave back %s -> %s\n", surge, dests[k]);
                }
            }
        }
        jack_free(dests);
    }
}

/* SIGTERM: hand every destination back, THEN unplug our input. In that order
 * so the audio never has a gap; and the input must go, because while Surge
 * still feeds it every mirror of the authority — the live monitor's included —
 * still names us as the fan-out source and would follow us back. */
static void leave_trunk(void)
{
    give_back_trunk();
    for (int ch = 1; ch <= CHANNELS; ch++) {
        char src[PORT_MAX];
        const char *in_name = jack_port_name(g_in_ports[ch - 1]);
        surge_out(ch, src, sizeof(src));
        if (named_port_fed_by(in_name, src)) {
            (void)jack_disconnect(g_client, src, in_name);
        }
    }
}

/* One pass. Returns whether we carry afterwards. */
static int wiring_pass(void)
{
    static int warned_no_input = 0;
    static int was_carrying = 0;

    for (int ch = 1; ch <= CHANNELS; ch++) {
        char src[PORT_MAX];
        surge_out(ch, src, sizeof(src));
        (void)connect_if_absent(src, jack_port_name(g_in_ports[ch - 1]));
    }

    if (!trunk_insert_carrying()) {
        /* Edge-triggered, as in the live monitor: a misconfigured insert and a
         * healthy one must not read the same. */
        if (!warned_no_input) {
            fprintf(stderr, "mpe-fx-host: %s is not connected to our input — leaving the "
                            "trunk on Surge (check MPE_SL_SURGE_CLIENT)\n", g_surge_client);
            warned_no_input = 1;
        }
        give_back_trunk();
        was_carrying = 0;
        return 0;
    }
    if (warned_no_input) {
        fprintf(stderr, "mpe-fx-host: %s reaching our input again\n", g_surge_client);
        warned_no_input = 0;
    }
    /* Asked on every pass, as the live monitor's detach is: anything that puts
     * Surge back on a destination underneath us is a second copy, and is
     * removed on the next pass — which a graph change triggers at once. */
    if (take_trunk() && !was_carrying) {
        fprintf(stderr, "mpe-fx-host: on the trunk (%s -> in, out -> fan-out)\n",
                g_surge_client);
    }
    was_carrying = 1;
    return 1;
}

static void *wiring_thread(void *arg)
{
    (void)arg;
    while (g_running) {
        atomic_store_explicit(&g_wake_pending, 0, memory_order_relaxed);
        (void)wiring_pass();

        struct timespec deadline;
        clock_gettime(CLOCK_REALTIME, &deadline);
        deadline.tv_sec += WIRING_BACKSTOP_S;
        while (g_running && sem_timedwait(&g_wake, &deadline) != 0 && errno == EINTR) {
        }
    }
    return NULL;
}

static void *control_thread(void *arg)
{
    (void)arg;
    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        fprintf(stderr, "mpe-fx-host: control socket failed: %s\n", strerror(errno));
        return NULL;
    }
    struct timeval tv = { .tv_sec = 0, .tv_usec = CONTROL_TIMEOUT_MS * 1000 };
    (void)setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = htons((uint16_t)g_control_port);
    if (bind(sock, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        /* No control means depth stays 0: the insert passes the input and the
         * instrument sounds as it did before the FX existed. */
        fprintf(stderr, "mpe-fx-host: control port %d unavailable (%s) — staying at depth 0\n",
                g_control_port, strerror(errno));
        close(sock);
        return NULL;
    }

    /* `chainl` with eight levels is ~100 bytes. A datagram that fills the
     * buffer may have been cut short, and a cut number still parses: ignored. */
    char buf[256];
    /* The gain the state file holds, so a resend of the same value (the
     * sender's keepalive, once a second) writes nothing. */
    float written = atomic_load_explicit(&g_target_gain, memory_order_relaxed);
    int write_failed = 0;
    while (g_running) {
        ssize_t got = recvfrom(sock, buf, sizeof(buf), 0, NULL, NULL);
        if (got <= 0 || (size_t)got >= sizeof(buf)) {
            continue;
        }
        buf[got] = '\0';
        uint32_t mask = 0;
        float depth = 0.0f;
        float levels[FX_MAX_EFFECTS];
        float bass = 0.0f;
        float gain = 1.0f;
        if (fx_parse_gain(buf, &gain)) {
            /* Range is the engine's (fx_engine_set_gain clamps). */
            atomic_store_explicit(&g_target_gain, gain, memory_order_relaxed);
            if (gain != written) {
                if (fx_gain_state_write(g_gain_state_path, gain) == 0) {
                    written = gain;
                    write_failed = 0;
                } else if (!write_failed) {
                    write_failed = 1;   /* once per run of failures, not once a second */
                    fprintf(stderr, "mpe-fx-host: cannot keep the stage gain in %s (%s); a "
                            "restart will start at unity\n", g_gain_state_path, strerror(errno));
                }
            }
            continue;
        }
        if (fx_parse_bass(buf, &bass)) {
            /* Range is the engine's (fx_engine_set_bass clamps; 0 is Off). */
            atomic_store_explicit(&g_target_bass, bass, memory_order_relaxed);
            continue;
        }
        if (!fx_parse_control(buf, &mask, &depth, levels) || (mask >> FX_EFFECT_COUNT) != 0u) {
            continue;   /* malformed, or naming an effect not shipped: changes nothing */
        }
        if (depth < 0.0f) {
            depth = 0.0f;
        } else if (depth > 1.0f) {
            depth = 1.0f;
        }
        for (int k = 0; k < FX_MAX_EFFECTS; k++) {
            const float level = levels[k] < 0.0f ? 0.0f : levels[k] > 1.0f ? 1.0f : levels[k];
            atomic_store_explicit(&g_target_level[k], level, memory_order_relaxed);
        }
        atomic_store_explicit(&g_target_mask, mask, memory_order_relaxed);
        atomic_store_explicit(&g_target_depth, depth, memory_order_relaxed);
    }
    close(sock);
    return NULL;
}
static void load_env(void)
{
    const char *surge = getenv("MPE_SL_SURGE_CLIENT");
    snprintf(g_surge_client, sizeof(g_surge_client), "%s",
             (surge != NULL && surge[0] != '\0') ? surge : SURGE_CLIENT_DEFAULT);
    const char *looper = getenv("MPE_SL_JACK_CLIENT");
    snprintf(g_looper_client, sizeof(g_looper_client), "%s",
             (looper != NULL && looper[0] != '\0') ? looper : LOOPER_CLIENT_DEFAULT);
    /* audio_routing.Topology.from_env: unparseable falls back to the default. */
    const char *loops = getenv("MPE_SL_LOOPS");
    if (loops != NULL && loops[0] != '\0') {
        char *end = NULL;
        long value = strtol(loops, &end, 10);
        if (end != loops && *end == '\0') {
            g_loops = value < 0 ? 0 : (value > LOOPS_MAX ? LOOPS_MAX : (int)value);
        }
    }
    const char *state = getenv("MPE_FX_GAIN_STATE");
    if (state != NULL && state[0] != '\0') {
        snprintf(g_gain_state_path, sizeof(g_gain_state_path), "%s", state);
    }
    const char *port = getenv("MPE_FX_HOST_PORT");
    if (port != NULL && port[0] != '\0') {
        int value = atoi(port);
        if (value >= 1 && value <= 65535) {
            g_control_port = value;
        }
    }
}

static void shutdown_client(void)
{
    jack_deactivate(g_client);
    jack_client_close(g_client);
}

int main(int argc, char **argv)
{
    (void)argc;
    (void)argv;

    if (!atomic_is_lock_free(&g_target_depth) || !atomic_is_lock_free(&g_target_mask) ||
        !atomic_is_lock_free(&g_target_level[0]) || !atomic_is_lock_free(&g_target_bass) ||
        !atomic_is_lock_free(&g_target_gain)) {
        fprintf(stderr, "mpe-fx-host: atomics are not lock-free on this platform — refusing to start\n");
        return 1;
    }
    for (int k = 0; k < FX_MAX_EFFECTS; k++) {
        atomic_init(&g_target_level[k], 1.0f);
    }

    load_env();
    if (sem_init(&g_wake, 0, 0) != 0) {
        fprintf(stderr, "mpe-fx-host: sem_init failed\n");
        return 1;
    }
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = handle_signal;
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);

    /* JackUseExactName, as in the live monitor: a second instance renamed to
     * mpe-fx-host-01 would feed the fan-out beside the first. Refuse instead. */
    jack_status_t status = 0;
    g_client = jack_client_open(CLIENT_NAME, JackNoStartServer | JackUseExactName, &status);
    if (g_client == NULL) {
        if (status & JackNameNotUnique) {
            fprintf(stderr, "mpe-fx-host: another instance already holds the name "
                            CLIENT_NAME " — refusing to start a second insert\n");
        } else {
            fprintf(stderr, "mpe-fx-host: jack_client_open failed (status 0x%x)\n",
                    (unsigned)status);
        }
        return 1;
    }

    /* Every effect's state, allocated here and never again. */
    float rate = (float)jack_get_sample_rate(g_client);
    if (fx_engine_init(&g_engine, FX_EFFECTS, FX_EFFECT_COUNT, rate, DEPTH_MS, XFADE_MS) != 0) {
        fprintf(stderr, "mpe-fx-host: cannot run at %.0f Hz (delay lines sized for %d)\n",
                (double)rate, FX_MAX_RATE);
        jack_client_close(g_client);
        return 1;
    }
    atomic_store_explicit(&g_rate, rate, memory_order_relaxed);

    /* The last gain the previous process was told, held to unity, in force
     * from the first period (no ramp from unity): a restart must not play a
     * cut patch loud for the second until the sender's next resend, and must
     * not bring back a boost the sender has not re-sent (fx_gain_state.h). No
     * file: unity, as an insert nobody has configured. */
    float restored = 1.0f;
    if (fx_gain_state_restore(g_gain_state_path, &restored)) {
        fx_engine_start_gain(&g_engine, restored);
        atomic_store_explicit(&g_target_gain, restored, memory_order_relaxed);
        fprintf(stderr, "mpe-fx-host: stage gain %.8f restored from %s\n", (double)restored,
                g_gain_state_path);
    }

    jack_set_process_callback(g_client, process, NULL);
    jack_set_sample_rate_callback(g_client, on_samplerate, NULL);
    jack_set_port_connect_callback(g_client, on_port_connect, NULL);
    jack_set_port_registration_callback(g_client, on_port_registration, NULL);
    jack_on_shutdown(g_client, on_shutdown, NULL);

    for (int ch = 0; ch < CHANNELS; ch++) {
        char name[16];
        snprintf(name, sizeof(name), "in_%d", ch + 1);
        g_in_ports[ch] = jack_port_register(g_client, name, JACK_DEFAULT_AUDIO_TYPE,
                                            JackPortIsInput, 0);
        snprintf(name, sizeof(name), "out_%d", ch + 1);
        g_out_ports[ch] = jack_port_register(g_client, name, JACK_DEFAULT_AUDIO_TYPE,
                                             JackPortIsOutput, 0);
        if (g_in_ports[ch] == NULL || g_out_ports[ch] == NULL) {
            fprintf(stderr, "mpe-fx-host: port register failed\n");
            jack_client_close(g_client);
            return 1;
        }
    }

    if (jack_activate(g_client) != 0) {
        fprintf(stderr, "mpe-fx-host: jack_activate failed\n");
        jack_client_close(g_client);
        return 1;
    }

    pthread_t wirer;
    pthread_t controller;
    if (pthread_create(&wirer, NULL, wiring_thread, NULL) != 0) {
        fprintf(stderr, "mpe-fx-host: wiring thread failed\n");
        shutdown_client();
        return 1;
    }
    if (pthread_create(&controller, NULL, control_thread, NULL) != 0) {
        g_running = 0;
        sem_post(&g_wake);
        pthread_join(wirer, NULL);
        shutdown_client();
        return 1;
    }

    while (g_running) {
        struct timespec ts = { .tv_sec = 0, .tv_nsec = 100000000L };
        (void)nanosleep(&ts, NULL);
    }

    pthread_join(wirer, NULL);
    pthread_join(controller, NULL);

    if (g_jack_shutdown) {
        /* No graph left to give anything back to; the wiring scripts rebuild
         * it when jackd returns. Non-zero so Restart=on-failure brings this
         * back with it. */
        fprintf(stderr, "mpe-fx-host: JACK shut down — exiting non-zero for Restart=on-failure\n");
        return 1;
    }

    leave_trunk();
    shutdown_client();
    fx_engine_free(&g_engine);
    return 0;
}
