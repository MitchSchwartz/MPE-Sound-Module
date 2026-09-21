/*
 * mpe-live-monitor — the gain stage on the monitor branch of the fan-out.
 *
 * Surge's output goes two places at once: straight to playback (what you hear)
 * and into every loop input (what gets captured). Because that is a fan-out
 * and not a chain, a gain stage can sit on one branch and leave the other
 * alone. This is that stage, on the monitor branch only:
 *
 *     Surge XT:out_N ──> mpe-live-monitor:in_N ─(gain)─> out_N ──> system:playback_N
 *                    └──────────────────────────────────────────> mpe-looper:loopM_in_N
 *
 * So the level you hear yourself at is separate from the level you are
 * recorded at. Nothing this process does can reach the capture path, which is
 * what makes it safe to move the monitor level while recording: the take is
 * always captured at full scale and stays raisable afterwards. This is the
 * split-console arrangement — pre-fader to tape, post-fader to the monitor mix
 * — and the record-arm behaviour of every DAW.
 *
 * What decides the level is not here. This process holds no policy: it is told
 * an amplitude and it applies it. `scripts/sooperlooper/live_monitor.py` is the
 * only thing that decides what that amplitude should be.
 *
 * RT callback: multiply and ramp only — no syscalls, locks, or allocation.
 * Control thread: one blocking recvfrom with a timeout, storing an atomic.
 *
 * Two safety properties, both deliberate:
 *
 *   1. **It can only attenuate.** Gain is clamped to 1.0. A bug in the control
 *      path, a malformed datagram, or a stale level can make your monitoring
 *      quieter than it should be; none of them can make it louder than the
 *      signal Surge already sent. On an appliance that may have headphones on
 *      someone's head, the failure directions are not symmetric.
 *
 *   2. **It takes the direct path away only once it is carrying audio, and
 *      gives it back when it leaves.** Surge -> playback is the fail-open route
 *      that keeps the instrument audible when everything else is broken.
 *      Removing it up front, in a wiring script, would mean a window where
 *      neither path exists. So the disconnect happens here, after this client
 *      is activated and audio can traverse it end to end, and SIGTERM reconnects
 *      it before exit. A crash cannot run that code at all, so the unit carries
 *      `ExecStopPost=` lines that restore the direct path whatever killed this
 *      process, and `sl-watchdog.py` asserts that Surge reaches playback by one
 *      route or the other. Nothing covers power loss, which nothing can.
 */

#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <jack/jack.h>
#include <math.h>
#include <netinet/in.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define CLIENT_NAME "mpe-live-monitor"
#define SURGE_CLIENT_DEFAULT "Surge XT"
/* Stereo, and everything downstream assumes it: system:playback_1/_2, Surge's
 * out_1/out_2, and the ExecStopPost restore script's `for ch in 1 2`. Raising
 * this alone would wire channels nothing else knows about. */
#define CHANNELS 2
#define CONTROL_PORT_DEFAULT 9957
#define CONTROL_TIMEOUT_MS 200
#define CONNECT_INTERVAL_US 2000000
#define STATE_NAME "live-monitor.state"
#define RUN_DIR_MAX 480

/* Ramp time constant. The level moves while notes are sounding — on every
 * record start and stop — so a step would click and a long slew would smear
 * the boundary. ~80 ms reads as a swell. Matches the intent of the APC fader
 * smoothing (loop_mix.FADER_SMOOTH_MS), which solves the same problem on the
 * loop side; the two are not required to agree and are not shared. */
#define RAMP_MS_DEFAULT 80.0

/* Below this distance the ramp lands rather than approaching forever. */
#define RAMP_SNAP 1e-5f

static jack_client_t *g_client;
static jack_port_t *g_in_ports[CHANNELS];
static jack_port_t *g_out_ports[CHANNELS];
static volatile sig_atomic_t g_running = 1;
static volatile sig_atomic_t g_jack_shutdown = 0;

/* Target set by the control thread, followed by the RT callback. Unity until
 * something says otherwise: an unconfigured insert must be inaudible, not
 * silent. */
static _Atomic float g_target_gain = 1.0f;
static float g_current_gain = 1.0f;
static _Atomic float g_ramp_alpha = 0.0f;

static char g_surge_client[128];
static char g_run_dir[RUN_DIR_MAX + 1] = "/run/mpe";
static int g_control_port = CONTROL_PORT_DEFAULT;
static double g_ramp_ms = RAMP_MS_DEFAULT;
static atomic_int g_direct_detached = 0;

static float clamp_gain(float value)
{
    if (!isfinite(value) || value < 0.0f) {
        return 0.0f;
    }
    /* Attenuate only — see property 1 in the header comment. */
    return value > 1.0f ? 1.0f : value;
}

static int process(jack_nframes_t nframes, void *arg)
{
    (void)arg;
    const float target = atomic_load_explicit(&g_target_gain, memory_order_relaxed);
    const float alpha = atomic_load_explicit(&g_ramp_alpha, memory_order_relaxed);

    jack_default_audio_sample_t *in[CHANNELS];
    jack_default_audio_sample_t *out[CHANNELS];
    for (int ch = 0; ch < CHANNELS; ch++) {
        in[ch] = (jack_default_audio_sample_t *)jack_port_get_buffer(g_in_ports[ch], nframes);
        out[ch] = (jack_default_audio_sample_t *)jack_port_get_buffer(g_out_ports[ch], nframes);
    }

    /* Hoisted out of the sample loop: a buffer pointer cannot change mid-period,
     * so testing it per sample bought nothing and left the failure pointing the
     * wrong way — `continue` left an output buffer unwritten, which is whatever
     * the last period left in it, at full scale. Silence is the correct output
     * when there is no input. */
    for (int ch = 0; ch < CHANNELS; ch++) {
        if (out[ch] == NULL) {
            continue;
        }
        if (in[ch] == NULL) {
            memset(out[ch], 0, (size_t)nframes * sizeof(*out[ch]));
        }
    }

    float gain = g_current_gain;
    for (jack_nframes_t i = 0; i < nframes; i++) {
        if (alpha <= 0.0f || fabsf(target - gain) <= RAMP_SNAP) {
            gain = target;
        } else {
            gain += alpha * (target - gain);
        }
        for (int ch = 0; ch < CHANNELS; ch++) {
            /* Both buffers were checked above, before the loop. */
            if (in[ch] != NULL && out[ch] != NULL) {
                out[ch][i] = in[ch][i] * gain;
            }
        }
    }
    g_current_gain = gain;
    return 0;
}

static int on_samplerate(jack_nframes_t rate, void *arg)
{
    (void)arg;
    /* One-pole coefficient for the configured time constant, recomputed
     * whenever jackd changes rate. Computed here rather than in `process`
     * because `exp` in the RT callback is exactly the kind of thing that is
     * fine until the day it is not. */
    float alpha = 0.0f;
    if (rate > 0 && g_ramp_ms > 0.0) {
        alpha = (float)(1.0 - exp(-1.0 / ((g_ramp_ms / 1000.0) * (double)rate)));
    }
    atomic_store_explicit(&g_ramp_alpha, alpha, memory_order_relaxed);
    return 0;
}

static void on_shutdown(void *arg)
{
    (void)arg;
    g_jack_shutdown = 1;
    g_running = 0;
}

static void handle_signal(int sig)
{
    (void)sig;
    g_running = 0;
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

/* Is Surge actually reaching our input on every channel? */
static int input_leg_live(void)
{
    for (int ch = 0; ch < CHANNELS; ch++) {
        char src[192];
        snprintf(src, sizeof(src), "%s:out_%d", g_surge_client, ch + 1);
        if (!port_connected_to(g_in_ports[ch], src)) {
            return 0;
        }
    }
    return 1;
}

/* Is our own output reaching playback on every channel? */
static int output_leg_live(void)
{
    for (int ch = 0; ch < CHANNELS; ch++) {
        char playback[64];
        snprintf(playback, sizeof(playback), "system:playback_%d", ch + 1);
        if (!port_connected_to(g_out_ports[ch], playback)) {
            return 0;
        }
    }
    return 1;
}

/* The direct path may only be taken away when audio can actually traverse this
 * client end to end — both legs, every channel.
 *
 * Checking the output leg alone was a silent-instrument bug: with our outputs
 * wired and our inputs connected to nothing (wrong MPE_SL_SURGE_CLIENT, or a
 * Surge that never came up) this returned true, the direct path was removed,
 * and `wire-jack-graph.sh` declined to restore it because the client was
 * "present". Total silence, nothing in any log, reaffirmed on every pass —
 * exactly the cases where the fail-open path is what you needed. */
static int monitor_path_live(void)
{
    return input_leg_live() && output_leg_live();
}

/* Returns 0 when the direct path is gone on every channel.
 *
 * Asks whether each connection is there before removing it, rather than firing
 * a disconnect every 2 s forever at something absent, and reports what it could
 * not remove: a half-failed detach leaves one channel doubled and the other not,
 * which is an L/R imbalance that reads as a broken cable. */
static int detach_direct_path(void)
{
    int failures = 0;
    for (int ch = 0; ch < CHANNELS; ch++) {
        char src[192];
        char playback[64];
        snprintf(src, sizeof(src), "%s:out_%d", g_surge_client, ch + 1);
        snprintf(playback, sizeof(playback), "system:playback_%d", ch + 1);
        jack_port_t *src_port = jack_port_by_name(g_client, src);
        if (src_port == NULL || !port_connected_to(src_port, playback)) {
            continue;
        }
        int rc = jack_disconnect(g_client, src, playback);
        if (rc != 0) {
            failures++;
            fprintf(stderr, "mpe-live-monitor: could not detach %s -> %s (rc %d) — "
                            "both paths are live on this channel\n", src, playback, rc);
        }
    }
    if (failures == 0) {
        atomic_store_explicit(&g_direct_detached, 1, memory_order_relaxed);
    }
    return failures;
}

static void restore_direct_path(void)
{
    if (!atomic_load_explicit(&g_direct_detached, memory_order_relaxed)) {
        return;
    }
    fprintf(stderr, "mpe-live-monitor: restoring %s -> system:playback\n", g_surge_client);
    int failures = 0;
    for (int ch = 0; ch < CHANNELS; ch++) {
        char src[192];
        char playback[64];
        snprintf(src, sizeof(src), "%s:out_%d", g_surge_client, ch + 1);
        snprintf(playback, sizeof(playback), "system:playback_%d", ch + 1);
        int rc = connect_if_absent(src, playback);
        if (rc != 0) {
            failures++;
            fprintf(stderr, "mpe-live-monitor: could not restore %s -> %s (rc %d)\n",
                    src, playback, rc);
        }
    }
    /* Only say the fail-open path is back when it is. Clearing the flag on a
     * failed restore made the failure permanent: the next pass saw "nothing to
     * restore" and the instrument stayed silent with the reason already past. */
    if (failures == 0) {
        atomic_store_explicit(&g_direct_detached, 0, memory_order_relaxed);
    }
}

static void ensure_wiring(void)
{
    static int warned_no_input = 0;

    for (int ch = 0; ch < CHANNELS; ch++) {
        char src[192];
        char playback[64];
        snprintf(src, sizeof(src), "%s:out_%d", g_surge_client, ch + 1);
        snprintf(playback, sizeof(playback), "system:playback_%d", ch + 1);
        (void)connect_if_absent(src, jack_port_name(g_in_ports[ch]));
        (void)connect_if_absent(jack_port_name(g_out_ports[ch]), playback);
    }

    if (!input_leg_live()) {
        /* Edge-triggered: Surge is not reaching us, so the direct path stays.
         * Said out loud because the alternative is an instrument that is fine
         * and an instrument that is misconfigured reading identically. */
        if (!warned_no_input) {
            fprintf(stderr, "mpe-live-monitor: %s is not connected to our input — "
                            "leaving the direct path in place (check MPE_SL_SURGE_CLIENT)\n",
                    g_surge_client);
            warned_no_input = 1;
        }
        restore_direct_path();
        return;
    }
    if (warned_no_input) {
        fprintf(stderr, "mpe-live-monitor: %s reaching our input again\n", g_surge_client);
        warned_no_input = 0;
    }

    /* Asked on every pass, not once. `detach_direct_path()` is already idempotent
     * — it checks each connection before removing it — so guarding this on
     * "have we detached before" bought nothing and cost the ability to detach
     * AGAIN. Anything that puts Surge back on playback underneath a live insert
     * (a hand-run jack_connect, the watchdog's repair firing on a stale sensor,
     * an older wiring script) would otherwise stay there for the life of this
     * process: two copies of the live signal, which is the one thing AGENTS.md
     * says must never happen. Announced only on the edge, so the journal does
     * not fill at one line every 2 s. */
    int was_detached = atomic_load_explicit(&g_direct_detached, memory_order_relaxed);
    if (monitor_path_live()) {
        if (detach_direct_path() == 0 && !was_detached) {
            fprintf(stderr, "mpe-live-monitor: inserted on the monitor branch "
                            "(%s -> in, out -> system:playback)\n", g_surge_client);
        }
    }
}

/* Publish what this process knows about the live path, the way mpe-peak-meter
 * publishes the graph: a file somebody else can read without forking anything.
 *
 * sl-watchdog.py is that somebody. It cannot ask JACK directly — a jack_lsp
 * fork inside a periodic loop is banned by `scripts/lib/periodic_loop_lint.py`,
 * and rightly, since CPU is the scarcest thing on this board — and the meter
 * that could answer is opt-in and off by default. So the process that takes the
 * fail-open path away is the process that reports on it, and the report going
 * stale IS the alarm: this file stops being written the moment this process
 * dies, which is exactly when Surge needs putting back on playback.
 *
 * Written from the wiring thread at CONNECT_INTERVAL_US (2 s), never from the
 * RT callback. */
static void write_live_state(int carrying, int detached, float gain)
{
    char path[RUN_DIR_MAX + 32];
    char tmp[sizeof(path) + 32];

    if (snprintf(path, sizeof(path), "%s/%s", g_run_dir, STATE_NAME) >= (int)sizeof(path)) {
        return;
    }
    if (snprintf(tmp, sizeof(tmp), "%s.tmp.%d", path, (int)getpid()) >= (int)sizeof(tmp)) {
        return;
    }
    FILE *fh = fopen(tmp, "we");
    if (fh == NULL) {
        return;
    }
    /* carrying=1 means audio can traverse this client end to end, so the direct
     * path is not needed. detached=1 means we have actually removed it. */
    fprintf(fh, "carrying=%d\n", carrying ? 1 : 0);
    fprintf(fh, "detached=%d\n", detached ? 1 : 0);
    fprintf(fh, "gain=%.6f\n", (double)gain);
    fprintf(fh, "surge_client=%s\n", g_surge_client);
    fprintf(fh, "updated=%ld\n", (long)time(NULL));
    fclose(fh);
    chmod(tmp, 0644);
    rename(tmp, path);
}

static void interruptible_usleep(useconds_t us)
{
    while (us > 0 && g_running) {
        useconds_t chunk = us > 100000U ? 100000U : us;
        usleep(chunk);
        us -= chunk;
    }
}

static void *connect_thread(void *arg)
{
    (void)arg;
    while (g_running) {
        ensure_wiring();
        write_live_state(monitor_path_live(),
                         atomic_load_explicit(&g_direct_detached, memory_order_relaxed),
                         atomic_load_explicit(&g_target_gain, memory_order_relaxed));
        interruptible_usleep(CONNECT_INTERVAL_US);
    }
    return NULL;
}

/* `gain <0.0-1.0>`, one line of ASCII per datagram. Deliberately not OSC, for
 * the same reasons as remote_fader.py: the sending side stays a three-line
 * sendto with no dependency, and a malformed datagram from anything else on
 * loopback must be ignorable without a parser. */
static int parse_gain(const char *text, float *out)
{
    while (*text == ' ' || *text == '\t') {
        text++;
    }
    if (strncmp(text, "gain", 4) != 0) {
        return 0;
    }
    const char *rest = text + 4;
    if (*rest != ' ' && *rest != '\t') {
        return 0;
    }
    char *end = NULL;
    errno = 0;
    float value = strtof(rest, &end);
    if (end == rest || errno == ERANGE || !isfinite(value)) {
        return 0;
    }
    while (*end == ' ' || *end == '\t' || *end == '\n' || *end == '\r') {
        end++;
    }
    if (*end != '\0') {
        return 0;
    }
    *out = value;
    return 1;
}

static void *control_thread(void *arg)
{
    (void)arg;
    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        fprintf(stderr, "mpe-live-monitor: control socket failed: %s\n", strerror(errno));
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
        /* A bound port elsewhere means no level control, not no audio: the
         * insert stays at unity and the instrument is unchanged. */
        fprintf(stderr, "mpe-live-monitor: control port %d unavailable (%s) — "
                        "monitor stays at unity\n",
                g_control_port, strerror(errno));
        close(sock);
        return NULL;
    }

    char buf[64];
    while (g_running) {
        ssize_t got = recvfrom(sock, buf, sizeof(buf) - 1, 0, NULL, NULL);
        if (got <= 0) {
            continue;
        }
        buf[got] = '\0';
        float value = 0.0f;
        if (parse_gain(buf, &value)) {
            atomic_store_explicit(&g_target_gain, clamp_gain(value), memory_order_relaxed);
        }
    }
    close(sock);
    return NULL;
}

static void load_env(void)
{
    const char *surge = getenv("MPE_SL_SURGE_CLIENT");
    if (surge != NULL && surge[0] != '\0') {
        snprintf(g_surge_client, sizeof(g_surge_client), "%s", surge);
    } else {
        snprintf(g_surge_client, sizeof(g_surge_client), "%s", SURGE_CLIENT_DEFAULT);
    }
    const char *port = getenv("MPE_LIVE_MONITOR_PORT");
    if (port != NULL && port[0] != '\0') {
        int value = atoi(port);
        if (value >= 1 && value <= 65535) {
            g_control_port = value;
        }
    }
    const char *run_dir = getenv("MPE_RUN_DIR");
    if (run_dir != NULL && run_dir[0] != '\0') {
        snprintf(g_run_dir, sizeof(g_run_dir), "%s", run_dir);
    }
    const char *ramp = getenv("MPE_LIVE_MONITOR_RAMP_MS");
    if (ramp != NULL && ramp[0] != '\0') {
        double value = atof(ramp);
        if (value >= 0.0 && value <= 2000.0) {
            g_ramp_ms = value;
        }
    }
}

int main(int argc, char **argv)
{
    (void)argc;
    (void)argv;

    if (!atomic_is_lock_free(&g_target_gain)) {
        fprintf(stderr,
                "mpe-live-monitor: atomics are not lock-free on this platform — refusing to start\n");
        return 1;
    }

    load_env();
    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    /* JackUseExactName, and a status we actually read. Without it JACK renames
     * a colliding client to mpe-live-monitor-01 and returns SUCCESS, so a second
     * instance — a restart racing a lingering process, or a hand-run binary
     * during a soak — wires its own insert to playback beside the first. That is
     * two copies of the live signal, which is the one thing AGENTS.md says must
     * never happen here. Worse, the loser's control port is already bound, so
     * the duplicate sits at unity: the loud copy is the one nothing can turn
     * down. Refuse instead. */
    jack_status_t status = 0;
    g_client = jack_client_open(CLIENT_NAME, JackNoStartServer | JackUseExactName, &status);
    if (g_client == NULL) {
        if (status & JackNameNotUnique) {
            fprintf(stderr, "mpe-live-monitor: another instance already holds the name "
                            CLIENT_NAME " — refusing to start a second insert\n");
        } else {
            fprintf(stderr, "mpe-live-monitor: jack_client_open failed (status 0x%x)\n",
                    (unsigned)status);
        }
        return 1;
    }

    jack_set_process_callback(g_client, process, NULL);
    jack_set_sample_rate_callback(g_client, on_samplerate, NULL);
    jack_on_shutdown(g_client, on_shutdown, NULL);
    (void)on_samplerate(jack_get_sample_rate(g_client), NULL);

    for (int ch = 0; ch < CHANNELS; ch++) {
        char name[16];
        snprintf(name, sizeof(name), "in_%d", ch + 1);
        g_in_ports[ch] = jack_port_register(g_client, name, JACK_DEFAULT_AUDIO_TYPE,
                                            JackPortIsInput, 0);
        snprintf(name, sizeof(name), "out_%d", ch + 1);
        g_out_ports[ch] = jack_port_register(g_client, name, JACK_DEFAULT_AUDIO_TYPE,
                                             JackPortIsOutput, 0);
        if (g_in_ports[ch] == NULL || g_out_ports[ch] == NULL) {
            fprintf(stderr, "mpe-live-monitor: port register failed\n");
            jack_client_close(g_client);
            return 1;
        }
    }

    if (jack_activate(g_client) != 0) {
        fprintf(stderr, "mpe-live-monitor: jack_activate failed\n");
        jack_client_close(g_client);
        return 1;
    }

    ensure_wiring();

    pthread_t connector;
    pthread_t controller;
    if (pthread_create(&connector, NULL, connect_thread, NULL) != 0) {
        fprintf(stderr, "mpe-live-monitor: connect thread failed\n");
        jack_deactivate(g_client);
        jack_client_close(g_client);
        return 1;
    }
    if (pthread_create(&controller, NULL, control_thread, NULL) != 0) {
        g_running = 0;
        pthread_join(connector, NULL);
        jack_deactivate(g_client);
        jack_client_close(g_client);
        return 1;
    }

    while (g_running) {
        struct timespec ts = { .tv_sec = 0, .tv_nsec = 100000000L };
        (void)nanosleep(&ts, NULL);
    }

    pthread_join(connector, NULL);
    pthread_join(controller, NULL);

    /* Hand the fail-open path back before leaving. Skipped when jackd is the
     * thing that went away — there is no graph left to write to. */
    if (!g_jack_shutdown) {
        restore_direct_path();
    }

    /* Say what we leave behind. Without this, a clean stop left the last line
     * the wiring thread wrote — `detached=1` — in a file nobody would ever
     * update again, so the watchdog would read "the fail-open path was taken
     * away by a process that is gone", every tick, forever, about an instrument
     * that is perfectly fine. A false alarm that cannot clear is worse than no
     * alarm: it is the one that teaches you to ignore the real one. */
    write_live_state(0, atomic_load_explicit(&g_direct_detached, memory_order_relaxed),
                     atomic_load_explicit(&g_target_gain, memory_order_relaxed));

    jack_deactivate(g_client);
    jack_client_close(g_client);
    return 0;
}
