/*
 * mpe-peak-meter — compiled JACK leaf client for the touch UI OUT meter.
 *
 * Phase 5 (session-control-plane-spec): Python must not hold a JACK process
 * callback. This process runs the RT work; the touch UI reads /run/mpe/meter.state.
 *
 * RT callback: block peak only — no syscalls, locks, or allocation.
 * Writer thread: 5 Hz atomic read + atomic KEY=value file install (matches UI poll).
 *
 * WHAT IT MEASURES (2026-10-11): what leaves the box. The input tap follows the
 * fan-out source — the FX host's output while it carries the trunk, otherwise
 * Surge — so the level and the clip latch see the effects' added peaks and
 * their ±1.0 clamp. Until then the tap sat on Surge, before the FX host, and a
 * phaser's +5.4 dB could clip audibly while the meter read -3 dBFS.
 *
 * The HEALTH keys did not move with it. wired= / online= still answer "are
 * Surge's outputs on the graph" (audio-engine.sh's on-graph probe) and
 * surge_playback= still answers "can you hear yourself play" (sl-watchdog);
 * neither depends on where the meter listens.
 */

#define _GNU_SOURCE
#include <errno.h>
#include <jack/jack.h>
#include <math.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>
#include <unistd.h>
#include <pthread.h>
#include <sys/stat.h>

#define CLIENT_NAME "mpe-peak-meter"
#define SURGE_CLIENT_DEFAULT "Surge XT"
#define LOOPER_CLIENT_DEFAULT "mpe-looper"
/* The two inserts the routing authority (patch_browser/audio_routing.py) knows.
 * The trunk insert is absent today and harmless absent. */
#define TRUNK_INSERT "mpe-fx-host"
#define MONITOR_INSERT "mpe-live-monitor"
/* Match SurgePeakMonitor POLL_INTERVAL_S (0.2 s = 5 Hz). Decay is per write. */
#define WRITER_INTERVAL_US 200000
#define CONNECT_INTERVAL_US 2000000
/* 0.606 per 200 ms write ~= 0.92 per 33 ms (old 30 Hz writer feel). */
#define PEAK_DECAY 0.606f
/* Sample peak at or above this is a clip: the DAC clips on samples, and the FX
 * host clamps at exactly 1.0, so true-peak detection would add nothing. */
#define CLIP_THRESHOLD 0.999f
/* clip=1 stays up for this many writes after the last clipped window: 8 x 200 ms
 * = 1.6 s, long enough to be seen on a glance at the UI. */
#define CLIP_HOLD_WRITES 8
#define METER_STATE_NAME "meter.state"
#define RUN_DIR_MAX 480

static jack_client_t *g_client;
static jack_port_t *g_in_ports[2];
static volatile sig_atomic_t g_running = 1;
static volatile sig_atomic_t g_jack_shutdown = 0;
static _Atomic float g_period_peak = 0.0f;
static atomic_int g_surge_wired = 0;
static atomic_int g_looper_client = 0;
static atomic_int g_looper_playback = 0;
/* Does anything reach system:playback from the live side — Surge directly, or
 * the live monitor insert standing in for it? Nothing in this repository used
 * to ask: the connection was made once at wiring time and never removed, so it
 * needed no watchdog. mpe-live-monitor removes it on purpose, which makes
 * "neither route is connected" a reachable state that is silent in every log.
 * sl-watchdog.py repairs it from this flag. */
static atomic_int g_surge_playback = 0;
static _Atomic unsigned long g_xrun_count = 0;
static char g_run_dir[RUN_DIR_MAX + 1] = "/run/mpe";
static char g_surge_client[128];
static int g_include_looper = 0;

static float sample_peak(const jack_default_audio_sample_t *buf, jack_nframes_t nframes)
{
    float peak = 0.0f;
    for (jack_nframes_t i = 0; i < nframes; i++) {
        float s = buf[i];
        if (!isfinite(s)) {
            continue;
        }
        float a = s < 0.0f ? -s : s;
        if (a > peak) {
            peak = a;
        }
    }
    return peak;
}

static int process(jack_nframes_t nframes, void *arg)
{
    (void)arg;
    float peak = 0.0f;
    for (int ch = 0; ch < 2; ch++) {
        jack_default_audio_sample_t *buf =
            (jack_default_audio_sample_t *)jack_port_get_buffer(g_in_ports[ch], nframes);
        if (buf == NULL) {
            continue;
        }
        float ch_peak = sample_peak(buf, nframes);
        if (ch_peak > peak) {
            peak = ch_peak;
        }
    }
    float cur = atomic_load_explicit(&g_period_peak, memory_order_relaxed);
    while (peak > cur &&
           !atomic_compare_exchange_weak_explicit(
               &g_period_peak, &cur, peak, memory_order_relaxed, memory_order_relaxed)) {
    }
    return 0;
}

static int on_xrun(void *arg)
{
    (void)arg;
    atomic_fetch_add_explicit(&g_xrun_count, 1UL, memory_order_relaxed);
    return 0;
}

static void on_shutdown(void *arg)
{
    (void)arg;
    g_jack_shutdown = 1;
    g_running = 0;
}

/* Poll g_running in 100 ms slices so SIGTERM/jack shutdown is not delayed by sleep(1). */
static void wait_running(void)
{
    while (g_running) {
        struct timespec ts = { .tv_sec = 0, .tv_nsec = 100000000L };
        (void)nanosleep(&ts, NULL);
    }
}

static void interruptible_usleep(useconds_t us)
{
    while (us > 0 && g_running) {
        useconds_t chunk = us > 100000U ? 100000U : us;
        usleep(chunk);
        us -= chunk;
    }
}

static int env_truthy(const char *value)
{
    if (value == NULL || *value == '\0') {
        return 0;
    }
    return (strcmp(value, "1") == 0 || strcasecmp(value, "true") == 0 ||
            strcasecmp(value, "yes") == 0 || strcasecmp(value, "on") == 0);
}

static int looper_client_visible(void)
{
    const char **ports = jack_get_ports(g_client, "mpe-looper:*", NULL, 0);
    if (ports == NULL) {
        return 0;
    }
    int found = 0;
    for (int i = 0; ports[i] != NULL; i++) {
        if (strncmp(ports[i], "mpe-looper:", 11) == 0) {
            found = 1;
            break;
        }
    }
    jack_free(ports);
    return found;
}

static int looper_playback_wired(void)
{
    jack_port_t *pb = jack_port_by_name(g_client, "system:playback_1");
    if (pb == NULL) {
        return 0;
    }
    const char **connections = jack_port_get_all_connections(g_client, pb);
    if (connections == NULL) {
        return 0;
    }
    int ok = 0;
    for (int i = 0; connections[i] != NULL; i++) {
        if (strncmp(connections[i], "mpe-looper:common_out", 21) == 0) {
            ok = 1;
            break;
        }
    }
    jack_free(connections);
    return ok;
}

/* Does a named source feed system:playback_<ch>? */
static int playback_fed_by(int ch, const char *source)
{
    char playback[64];
    snprintf(playback, sizeof(playback), "system:playback_%d", ch);
    jack_port_t *pb = jack_port_by_name(g_client, playback);
    if (pb == NULL) {
        return 0;
    }
    const char **connections = jack_port_get_all_connections(g_client, pb);
    if (connections == NULL) {
        return 0;
    }
    int found = 0;
    for (int i = 0; connections[i] != NULL; i++) {
        if (strcmp(connections[i], source) == 0) {
            found = 1;
            break;
        }
    }
    jack_free(connections);
    return found;
}

static int port_fed_by(const char *port_name, const char *source)
{
    jack_port_t *port = jack_port_by_name(g_client, port_name);
    if (port == NULL) {
        return 0;
    }
    const char **connections = jack_port_get_all_connections(g_client, port);
    if (connections == NULL) {
        return 0;
    }
    int found = 0;
    for (int i = 0; connections[i] != NULL; i++) {
        if (strcmp(connections[i], source) == 0) {
            found = 1;
            break;
        }
    }
    jack_free(connections);
    return found;
}

/* Is the FX host on the trunk? Its input fed by Surge on every channel.
 * Mirror of audio_routing.trunk_insert_carrying(). */
static int trunk_insert_carrying(void)
{
    for (int ch = 1; ch <= 2; ch++) {
        char surge_out[192];
        char fx_in[64];
        snprintf(surge_out, sizeof(surge_out), "%s:out_%d", g_surge_client, ch);
        snprintf(fx_in, sizeof(fx_in), TRUNK_INSERT ":in_%d", ch);
        if (!port_fed_by(fx_in, surge_out)) {
            return 0;
        }
    }
    return 1;
}

/* The fan-out source on channel `ch`: the FX host while it carries, otherwise
 * Surge. Mirror of audio_routing.fanout_source(). The meter's own input tap
 * follows it (ensure_wiring below), so the meter reads what leaves the box. */
static void fanout_source(int ch, char *buf, size_t size)
{
    if (trunk_insert_carrying()) {
        snprintf(buf, size, TRUNK_INSERT ":out_%d", ch);
    } else {
        snprintf(buf, size, "%s:out_%d", g_surge_client, ch);
    }
}

/* Are Surge's outputs on the graph, every channel? The health answer behind
 * wired= / online=. It used to be read off the meter's own connect from Surge
 * succeeding; the tap no longer always comes from Surge, so it is asked of
 * Surge directly. */
static int surge_ports_present(void)
{
    for (int ch = 1; ch <= 2; ch++) {
        char surge_out[192];
        snprintf(surge_out, sizeof(surge_out), "%s:out_%d", g_surge_client, ch);
        if (jack_port_by_name(g_client, surge_out) == NULL) {
            return 0;
        }
    }
    return 1;
}

/* Can you hear yourself play? True when the fan-out source (Surge, or the FX
 * host on the trunk) reaches playback directly on both channels, or when
 * mpe-live-monitor does it on its behalf — and for the insert, only when the
 * fan-out source actually reaches the insert's input. Mirror of
 * audio_routing.live_path_ok(); tests/test_audio_routing.py runs both.
 *
 * Both legs, both channels, exact names. The first version of this asked
 * whether ANY connection whose name started with "mpe-live-monitor:" was on
 * playback_1, which is the presence-not-path law that produced the silent
 * instrument this whole flag exists to catch: an insert wired to playback with
 * nothing feeding it satisfied it perfectly. A sensor that can be satisfied by
 * the failure it watches for is worse than no sensor, because the watchdog
 * built on it reports healthy. */
static int surge_playback_wired(void)
{
    int direct = 1;
    int through_insert = 1;
    for (int ch = 1; ch <= 2; ch++) {
        char source[192];
        char insert_in[64];
        char insert_out[64];
        fanout_source(ch, source, sizeof(source));
        snprintf(insert_in, sizeof(insert_in), MONITOR_INSERT ":in_%d", ch);
        snprintf(insert_out, sizeof(insert_out), MONITOR_INSERT ":out_%d", ch);

        if (!playback_fed_by(ch, source)) {
            direct = 0;
        }
        if (!playback_fed_by(ch, insert_out) || !port_fed_by(insert_in, source)) {
            through_insert = 0;
        }
    }
    return direct || through_insert;
}

/* The writer's per-window state: the decaying displayed peak and the clip latch. */
struct meter_hold {
    float held_peak;
    int clip_writes;
};

/* One writer window. `window_peak` is the largest sample the RT callback saw
 * since the last window, off the post-FX tap. Pure, so the fake-JACK driver
 * runs it exactly as the writer does. */
static void meter_hold_step(struct meter_hold *h, float window_peak)
{
    if (window_peak > h->held_peak) {
        h->held_peak = window_peak;
    } else {
        h->held_peak *= PEAK_DECAY;
    }
    if (window_peak >= CLIP_THRESHOLD) {
        h->clip_writes = CLIP_HOLD_WRITES;
    } else if (h->clip_writes > 0) {
        h->clip_writes--;
    }
}

static void write_meter_state(float peak_linear, int clip, int surge_wired, int looper_client,
                              int looper_playback, int surge_playback,
                              unsigned long xruns, float dsp_percent)
{
    char path[RUN_DIR_MAX + 32];
    char tmp[sizeof(path) + 32];
    int pid = (int)getpid();

    if (snprintf(path, sizeof(path), "%s/%s", g_run_dir, METER_STATE_NAME) >= (int)sizeof(path)) {
        return;
    }
    if (snprintf(tmp, sizeof(tmp), "%s.tmp.%d", path, pid) >= (int)sizeof(tmp)) {
        return;
    }

    FILE *fh = fopen(tmp, "we");
    if (fh == NULL) {
        return;
    }
    /* peak_linear= and clip= are the post-FX tap: what leaves the box. */
    fprintf(fh, "peak_linear=%.9g\n", peak_linear);
    fprintf(fh, "clip=%d\n", clip ? 1 : 0);
    /* wired= reflects Surge XT:out_{1,2} being on the graph only — not where
     * the meter listens; looper taps are best-effort. */
    fprintf(fh, "wired=%d\n", surge_wired ? 1 : 0);
    /* jack_online=1 whenever this process is on the graph (watchdog graph probe). */
    fprintf(fh, "jack_online=%d\n", 1);
    fprintf(fh, "online=%d\n", surge_wired ? 1 : 0);
    fprintf(fh, "looper_client=%d\n", looper_client ? 1 : 0);
    fprintf(fh, "looper_playback=%d\n", looper_playback ? 1 : 0);
    fprintf(fh, "surge_playback=%d\n", surge_playback ? 1 : 0);
    fprintf(fh, "source=jack\n");
    fprintf(fh, "xruns=%lu\n", xruns);
    /* NAN = no valid reading (see read_dsp_percent). "invalid" fails float()
     * in governor_load, which is its existing stale path: fall back to proc
     * and say so in the journal. Never a plausible number. */
    if (isfinite(dsp_percent)) {
        fprintf(fh, "dsp_percent=%.3f\n", dsp_percent);
    } else {
        fprintf(fh, "dsp_percent=invalid\n");
    }
    fprintf(fh, "updated=%ld\n", (long)time(NULL));
    fclose(fh);
    chmod(tmp, 0644);
    rename(tmp, path);
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

static int connect_source(const char *source, jack_port_t *dest)
{
    if (port_connected_to(dest, source)) {
        return 0;
    }
    int rc = jack_connect(g_client, source, jack_port_name(dest));
    if (rc != 0 && rc != EEXIST) {
        return rc;
    }
    return 0;
}

/* Point the meter's input on channel `ch` at `want` and at nothing else from
 * the trunk. Break, then make: the meter is a leaf, so a moment of silence on
 * it costs nothing, while a moment fed by both Surge and the FX host would sum
 * them and could latch a clip that never reached the DAC. */
static int tap_from(int ch, const char *want)
{
    char candidates[2][192];
    snprintf(candidates[0], sizeof(candidates[0]), "%s:out_%d", g_surge_client, ch);
    snprintf(candidates[1], sizeof(candidates[1]), TRUNK_INSERT ":out_%d", ch);
    for (int i = 0; i < 2; i++) {
        if (strcmp(candidates[i], want) != 0 &&
            port_connected_to(g_in_ports[ch - 1], candidates[i])) {
            (void)jack_disconnect(g_client, candidates[i], jack_port_name(g_in_ports[ch - 1]));
        }
    }
    return connect_source(want, g_in_ports[ch - 1]);
}

static int ensure_wiring(void)
{
    char src[256];
    for (int ch = 1; ch <= 2; ch++) {
        fanout_source(ch, src, sizeof(src));
        (void)tap_from(ch, src);
    }
    const int surge_ok = surge_ports_present();
    if (g_include_looper) {
        for (int ch = 1; ch <= 2; ch++) {
            snprintf(src, sizeof(src), "%s:common_out_%d", LOOPER_CLIENT_DEFAULT, ch);
            (void)connect_source(src, g_in_ports[ch - 1]);
        }
    }
    atomic_store_explicit(&g_surge_wired, surge_ok, memory_order_relaxed);
    atomic_store_explicit(&g_looper_client, looper_client_visible(), memory_order_relaxed);
    atomic_store_explicit(&g_looper_playback, looper_playback_wired(), memory_order_relaxed);
    atomic_store_explicit(&g_surge_playback, surge_playback_wired(), memory_order_relaxed);
    return surge_ok;
}

/*
 * jack_cpu_load() is ALREADY a percentage, 0-100 of the real time available
 * per cycle (jack.h; jack2 JackEngineControl::CalcCPULoad). It is published
 * as-is. From 2026-08-23 (d4d91cb) to 2026-10-11 it was multiplied by 100 and
 * clamped, so every real load >= 1% read 100.000 (#154).
 *
 * A value jackd cannot produce (non-finite, < 0, > 100) is NOT clamped: a
 * clamp turns a broken instrument into a plausible reading. It returns NAN,
 * which write_meter_state publishes as "invalid", and is logged once per
 * valid->invalid transition.
 */
static float dsp_percent_from_jack_load(float load)
{
    if (!isfinite(load) || load < 0.0f || load > 100.0f) {
        return NAN;
    }
    return load;
}

static float read_dsp_percent(void)
{
    static int reported_invalid = 0;
    if (g_client == NULL) {
        return NAN;
    }
    float raw = jack_cpu_load(g_client);
    float dsp = dsp_percent_from_jack_load(raw);
    if (isfinite(dsp)) {
        reported_invalid = 0;
    } else if (!reported_invalid) {
        fprintf(stderr, CLIENT_NAME ": jack_cpu_load()=%g outside 0-100; "
                "publishing dsp_percent=invalid\n", (double)raw);
        reported_invalid = 1;
    }
    return dsp;
}

static void *writer_thread(void *arg)
{
    (void)arg;
    struct meter_hold hold = { 0.0f, 0 };
    while (g_running) {
        float window_peak =
            atomic_exchange_explicit(&g_period_peak, 0.0f, memory_order_relaxed);
        meter_hold_step(&hold, window_peak);
        int surge_wired = atomic_load_explicit(&g_surge_wired, memory_order_relaxed);
        int looper_client = atomic_load_explicit(&g_looper_client, memory_order_relaxed);
        int looper_playback = atomic_load_explicit(&g_looper_playback, memory_order_relaxed);
        int surge_playback = atomic_load_explicit(&g_surge_playback, memory_order_relaxed);
        unsigned long xruns = atomic_load_explicit(&g_xrun_count, memory_order_relaxed);
        float dsp_percent = read_dsp_percent();
        write_meter_state(hold.held_peak, hold.clip_writes > 0, surge_wired, looper_client,
                          looper_playback, surge_playback, xruns, dsp_percent);
        interruptible_usleep(WRITER_INTERVAL_US);
    }
    write_meter_state(0.0f, 0, 0, 0, 0, 0,
                      atomic_load_explicit(&g_xrun_count, memory_order_relaxed), NAN);
    return NULL;
}

static void *connect_thread(void *arg)
{
    (void)arg;
    while (g_running) {
        ensure_wiring();
        interruptible_usleep(CONNECT_INTERVAL_US);
    }
    return NULL;
}

static void handle_signal(int sig)
{
    (void)sig;
    g_running = 0;
}

static void load_env(void)
{
    const char *run_dir = getenv("MPE_RUN_DIR");
    if (run_dir != NULL && run_dir[0] != '\0') {
        snprintf(g_run_dir, sizeof(g_run_dir), "%s", run_dir);
    }
    /* MPE_PEAK_METER_SURGE_CLIENT first for compatibility, then the name every
     * other part of the system uses. They were separate variables answering one
     * question, and this one is now load-bearing: it decides whether the
     * watchdog thinks you can hear yourself play. */
    const char *surge = getenv("MPE_PEAK_METER_SURGE_CLIENT");
    if (surge == NULL || surge[0] == '\0') {
        surge = getenv("MPE_SL_SURGE_CLIENT");
    }
    if (surge != NULL && surge[0] != '\0') {
        snprintf(g_surge_client, sizeof(g_surge_client), "%s", surge);
    } else {
        snprintf(g_surge_client, sizeof(g_surge_client), "%s", SURGE_CLIENT_DEFAULT);
    }
    g_include_looper = env_truthy(getenv("MPE_PEAK_METER_INCLUDE_LOOPER"));
}

int main(int argc, char **argv)
{
    (void)argc;
    (void)argv;

    if (!atomic_is_lock_free(&g_period_peak) || !atomic_is_lock_free(&g_xrun_count)) {
        fprintf(stderr,
                "mpe-peak-meter: atomics are not lock-free on this platform — refusing to start\n");
        return 1;
    }

    load_env();
    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    g_client = jack_client_open(CLIENT_NAME, JackNoStartServer, NULL);
    if (g_client == NULL) {
        fprintf(stderr, "mpe-peak-meter: jack_client_open failed\n");
        return 1;
    }

    jack_set_process_callback(g_client, process, NULL);
    jack_set_xrun_callback(g_client, on_xrun, NULL);
    jack_on_shutdown(g_client, on_shutdown, NULL);

    for (int ch = 0; ch < 2; ch++) {
        char name[16];
        snprintf(name, sizeof(name), "in_%d", ch + 1);
        g_in_ports[ch] = jack_port_register(g_client, name, JACK_DEFAULT_AUDIO_TYPE,
                                            JackPortIsInput, 0);
        if (g_in_ports[ch] == NULL) {
            fprintf(stderr, "mpe-peak-meter: port register failed\n");
            jack_client_close(g_client);
            return 1;
        }
    }

    if (jack_activate(g_client) != 0) {
        fprintf(stderr, "mpe-peak-meter: jack_activate failed\n");
        jack_client_close(g_client);
        return 1;
    }

    ensure_wiring();

    pthread_t writer;
    pthread_t connector;
    if (pthread_create(&writer, NULL, writer_thread, NULL) != 0) {
        fprintf(stderr, "mpe-peak-meter: writer thread failed\n");
        jack_deactivate(g_client);
        jack_client_close(g_client);
        return 1;
    }
    if (pthread_create(&connector, NULL, connect_thread, NULL) != 0) {
        g_running = 0;
        pthread_join(writer, NULL);
        jack_deactivate(g_client);
        jack_client_close(g_client);
        return 1;
    }

    wait_running();

    pthread_join(connector, NULL);
    pthread_join(writer, NULL);
    jack_deactivate(g_client);
    jack_client_close(g_client);

    /* Non-zero when JACK went away underneath us, so Restart=on-failure brings
     * the meter back. This used to exit 0 on the theory that a jackd restart is
     * a normal teardown — but a systemd restart or stop of mpe-jackd reaches
     * this unit first (PartOf= plus After= orders our SIGTERM before jackd's),
     * which still exits 0 below. Only a jackd that dies or is killed outside
     * systemd lands here, and exiting 0 then left the unit inactive with
     * Result=success, the meter reading zero forever: indistinguishable from
     * real silence (found 2026-09-02). */
    if (g_jack_shutdown) {
        fprintf(stderr, "mpe-peak-meter: JACK shut down — exiting non-zero for Restart=on-failure\n");
        return 1;
    }
    return 0;
}
