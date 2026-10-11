/*
 * mpe-fx-host — the effects insert on the trunk, before the fan-out.
 *
 * Surge's output fans out to what you hear and to every loop input. The live
 * monitor sits on one branch of that fan-out; this sits upstream of it, so the
 * loops record what you hear:
 *
 *     Surge XT:out_N ──> mpe-fx-host:in_N ─(effect)─> out_N ─┬─> system:playback_N
 *                                                             └─> mpe-looper:loopM_in_N
 *
 * JACK runs clients in graph order inside one period, so this adds no buffer
 * latency. The DSP — the effect table, depth smoothing, the swap crossfade,
 * bypass and the output clamp — is in fx_dsp.c, which has no JACK in it so the
 * self-test can run anywhere.
 *
 * What effect and how much is not decided here. This process holds no policy:
 * it is told an effect id and a depth over UDP and it applies them. The Python
 * policy module is the only thing that decides; the datagram format has one
 * definition on that side, `patch_browser/fx_host_protocol.py`.
 *
 * RT callback: fx_engine_process only — no syscalls, locks, or allocation.
 * Control thread: one blocking recvfrom with a timeout, storing atomics.
 * Wiring thread: woken by JACK's port-connect and port-registration callbacks
 * (and every 2 s as a backstop); never touches audio.
 *
 * Safety properties, the live monitor's plus one:
 *
 *   1. **It cannot make the trunk louder.** Depth mixes toward the wet signal,
 *      never on top of it, and the chorus has no feedback path; every output
 *      sample is clamped to ±1.0 and NaN becomes silence (fx_dsp.h). Depth 0 —
 *      the startup state — is the input, bit for bit.
 *
 *   2. **It takes the trunk only once it is carrying audio, and gives it back
 *      when it leaves.** "Carrying" means Surge reaches our input on every
 *      channel. Only then is each trunk edge Surge -> D moved to us -> D, our
 *      edge made before Surge's is broken, so there is never a moment with no
 *      path. SIGTERM moves every edge back. A crash cannot run that code, so
 *      every edge is written to fx-host.state BEFORE it is taken, and the
 *      unit's ExecStopPost (scripts/restore-fx-trunk.sh) replays that list.
 *
 *   3. **It exits non-zero when jackd goes away**, so Restart=on-failure fires.
 *      The live monitor and the meter exit 0 there and are never restarted.
 *
 * The trunk is EVERY destination Surge fed, not just playback: if the loop
 * inputs stayed on Surge they would record dry while you heard wet, and if
 * they were fed by both they would record the signal twice.
 */

#define _GNU_SOURCE
#include "fx_dsp.h"

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
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define CLIENT_NAME "mpe-fx-host"
#define CONTROL_PORT_DEFAULT 9958
#define CONTROL_TIMEOUT_MS 200
#define WIRING_BACKSTOP_S 2
#define STATE_NAME "fx-host.state"
#define RUN_DIR_MAX 480
#define PORT_NAME_MAX 320
#define MAX_TAKEN 64

/* Depth follows the knob with a one-pole lag. CC has 128 steps, so unsmoothed
 * it zippers; much longer and the knob feels disconnected. A patch change with
 * the knob already raised jumps the target, and this is what ramps it. */
#define DEPTH_MS 40.0
/* Effect swap crossfade: long enough to hide the change of character, short
 * enough that a swap on a patch change lands before the next note matters. */
#define XFADE_MS 30.0

/* --------------------------------------------------------------------------
 * Trunk port names — the ONLY place this process knows them.
 *
 * Interim. Issue #142 puts the intended graph in one routing authority that
 * every wirer, repairer and predicate reads; when that lands, these names and
 * `is_trunk_destination` move there and this block goes. Until then the rule
 * here is deliberately narrow: this process never invents an edge. It moves
 * the trunk edges Surge already has, and only these destinations count as
 * trunk — everything else that listens to Surge (the peak meter, calibration)
 * stays on Surge, pre-FX, as the issue requires.
 * -------------------------------------------------------------------------- */
#define SURGE_CLIENT_DEFAULT "Surge XT"
#define LOOPER_CLIENT_DEFAULT "mpe-looper"
#define LIVE_MONITOR_IN_1 "mpe-live-monitor:in_1"

static char g_surge_client[128];
static char g_looper_client[128];

static void surge_out(int ch, char *buf, size_t len)
{
    snprintf(buf, len, "%s:out_%d", g_surge_client, ch + 1);
}

/* system:playback_N, or <looper>:loop<k>_in_N, on the same channel. */
static int is_trunk_destination(const char *dest, int ch)
{
    char want[64];
    snprintf(want, sizeof(want), "system:playback_%d", ch + 1);
    if (strcmp(dest, want) == 0) {
        return 1;
    }
    size_t clen = strlen(g_looper_client);
    if (strncmp(dest, g_looper_client, clen) != 0 || strncmp(dest + clen, ":loop", 5) != 0) {
        return 0;
    }
    const char *p = dest + clen + 5;
    if (*p < '0' || *p > '9') {
        return 0;
    }
    while (*p >= '0' && *p <= '9') {
        p++;
    }
    char tail[16];
    snprintf(tail, sizeof(tail), "_in_%d", ch + 1);
    return strcmp(p, tail) == 0;
}

/* ------------------------------------------------------------------------ */

static jack_client_t *g_client;
static jack_port_t *g_in_ports[FX_CHANNELS];
static jack_port_t *g_out_ports[FX_CHANNELS];
static volatile sig_atomic_t g_running = 1;
static volatile sig_atomic_t g_jack_shutdown = 0;

static fx_engine g_engine;

/* Targets set by the control thread, followed by the RT callback. Chorus at
 * depth 0 until something says otherwise: an unconfigured insert must be
 * inaudible, not silent. */
static atomic_int g_target_effect = 0;
static _Atomic float g_target_depth = 0.0f;
static _Atomic float g_rate = 0.0f;

static char g_run_dir[RUN_DIR_MAX + 1] = "/run/mpe";
static int g_control_port = CONTROL_PORT_DEFAULT;

/* Wake the wiring thread on a graph change. Coalesced: a wiring script making
 * thirty connections costs one or two passes, not thirty. */
static sem_t g_wake;
static atomic_int g_wake_pending = 0;

/* Edges this process has taken from Surge. Wiring thread only. */
typedef struct {
    int ch;
    char dest[PORT_NAME_MAX];
} taken_edge;
static taken_edge g_taken[MAX_TAKEN];
static int g_taken_count = 0;

static int process(jack_nframes_t nframes, void *arg)
{
    (void)arg;
    const int effect = atomic_load_explicit(&g_target_effect, memory_order_relaxed);
    float depth = atomic_load_explicit(&g_target_depth, memory_order_relaxed);
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
    fx_engine_process(&g_engine, cin, cout, nframes, effect, depth);
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

static int disconnect_if_present(const char *source, const char *dest)
{
    jack_port_t *src_port = jack_port_by_name(g_client, source);
    if (src_port == NULL || !port_connected_to(src_port, dest)) {
        return 0;
    }
    return jack_disconnect(g_client, source, dest);
}

/* Is Surge reaching our input on every channel? */
static int input_leg_live(void)
{
    for (int ch = 0; ch < FX_CHANNELS; ch++) {
        char src[192];
        surge_out(ch, src, sizeof(src));
        if (!port_connected_to(g_in_ports[ch], src)) {
            return 0;
        }
    }
    return 1;
}

/* The state file: what this process has taken, written BEFORE each edge is
 * taken so a crash between "connected ours" and "wrote it down" cannot leave an
 * edge nobody knows to give back. `scripts/restore-fx-trunk.sh` reads the
 * `edge=` lines; the rest is for whoever is diagnosing. Its going stale is the
 * alarm, as with live-monitor.state.
 *
 * Wiring thread only; written on every change and every backstop pass. */
static void write_state(int carrying)
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
    fprintf(fh, "carrying=%d\n", carrying ? 1 : 0);
    fprintf(fh, "taken=%d\n", g_taken_count);
    for (int i = 0; i < g_taken_count; i++) {
        fprintf(fh, "edge=%d %s\n", g_taken[i].ch + 1, g_taken[i].dest);
    }
    fprintf(fh, "effect=%d\n", atomic_load_explicit(&g_target_effect, memory_order_relaxed));
    fprintf(fh, "depth=%.6f\n",
            (double)atomic_load_explicit(&g_target_depth, memory_order_relaxed));
    fprintf(fh, "surge_client=%s\n", g_surge_client);
    fprintf(fh, "updated=%ld\n", (long)time(NULL));
    fclose(fh);
    chmod(tmp, 0644);
    rename(tmp, path);
}

static int taken_index(int ch, const char *dest)
{
    for (int i = 0; i < g_taken_count; i++) {
        if (g_taken[i].ch == ch && strcmp(g_taken[i].dest, dest) == 0) {
            return i;
        }
    }
    return -1;
}

static void forget_taken(int i)
{
    g_taken[i] = g_taken[g_taken_count - 1];
    g_taken_count--;
}

/* A looper restart removes its ports, and the edges with them. Nothing to give
 * back for a port that is gone; whoever recreates it wires it to Surge, and the
 * next pass moves it. */
static int prune_taken(void)
{
    int changed = 0;
    for (int i = g_taken_count - 1; i >= 0; i--) {
        if (jack_port_by_name(g_client, g_taken[i].dest) == NULL) {
            forget_taken(i);
            changed = 1;
        }
    }
    return changed;
}

/* Move every trunk edge Surge has onto us. Make-before-break: for the instant
 * between the two calls the destination is fed twice (one period at most, the
 * same trade the live monitor makes), never zero times. */
static void take_trunk(void)
{
    for (int ch = 0; ch < FX_CHANNELS; ch++) {
        char src[192];
        surge_out(ch, src, sizeof(src));
        jack_port_t *src_port = jack_port_by_name(g_client, src);
        if (src_port == NULL) {
            continue;
        }
        const char **dests = jack_port_get_all_connections(g_client, src_port);
        if (dests == NULL) {
            continue;
        }
        const char *ours = jack_port_name(g_out_ports[ch]);
        for (int k = 0; dests[k] != NULL; k++) {
            const char *dest = dests[k];
            if (!is_trunk_destination(dest, ch)) {
                continue;
            }
            int idx = taken_index(ch, dest);
            if (idx < 0) {
                if (g_taken_count >= MAX_TAKEN) {
                    fprintf(stderr, "mpe-fx-host: more than %d trunk edges — leaving %s on %s\n",
                            MAX_TAKEN, dest, src);
                    continue;
                }
                g_taken[g_taken_count].ch = ch;
                snprintf(g_taken[g_taken_count].dest, PORT_NAME_MAX, "%s", dest);
                g_taken_count++;
                write_state(1);   /* write-ahead, see write_state */
            }
            int rc = connect_if_absent(ours, dest);
            if (rc != 0) {
                fprintf(stderr, "mpe-fx-host: could not connect %s -> %s (rc %d) — "
                                "leaving it on %s\n", ours, dest, rc, src);
                idx = taken_index(ch, dest);
                if (idx >= 0) {
                    forget_taken(idx);
                }
                continue;
            }
            rc = jack_disconnect(g_client, src, dest);
            if (rc != 0) {
                fprintf(stderr, "mpe-fx-host: could not detach %s -> %s (rc %d) — "
                                "both paths are live on this destination\n", src, dest, rc);
            } else if (idx < 0) {
                fprintf(stderr, "mpe-fx-host: took %s -> %s\n", src, dest);
            }
        }
        jack_free(dests);
    }
}

/* Hand every taken edge back to Surge. An edge stays on the list until Surge
 * reaches it again, so a failed restore is retried, and recorded for
 * ExecStopPost, rather than forgotten. */
static void give_back_trunk(void)
{
    if (g_taken_count == 0) {
        return;
    }
    for (int i = g_taken_count - 1; i >= 0; i--) {
        int ch = g_taken[i].ch;
        char src[192];
        surge_out(ch, src, sizeof(src));
        int rc = connect_if_absent(src, g_taken[i].dest);
        if (rc != 0) {
            fprintf(stderr, "mpe-fx-host: could not restore %s -> %s (rc %d)\n",
                    src, g_taken[i].dest, rc);
            continue;
        }
        (void)disconnect_if_present(jack_port_name(g_out_ports[ch]), g_taken[i].dest);
        fprintf(stderr, "mpe-fx-host: gave back %s -> %s\n", src, g_taken[i].dest);
        forget_taken(i);
    }
}

static int wiring_pass(void)
{
    static int warned_no_input = 0;
    static int warned_monitor = 0;

    for (int ch = 0; ch < FX_CHANNELS; ch++) {
        char src[192];
        surge_out(ch, src, sizeof(src));
        (void)connect_if_absent(src, jack_port_name(g_in_ports[ch]));
    }
    (void)prune_taken();

    /* Two inserts, one on the trunk and one on its monitor branch, need the
     * routing authority to say which feeds which: each client here knows only
     * Surge as its source, and running both makes them re-source each other's
     * edges in a loop, with the live signal doubled on every turn. Until that
     * lands, the monitor wins and this stays out of the path. */
    if (jack_port_by_name(g_client, LIVE_MONITOR_IN_1) != NULL) {
        if (!warned_monitor) {
            fprintf(stderr, "mpe-fx-host: mpe-live-monitor is running — staying off the trunk "
                            "(both inserts at once needs the routing authority, #142)\n");
            warned_monitor = 1;
        }
        give_back_trunk();
        return 0;
    }
    warned_monitor = 0;

    if (!input_leg_live()) {
        /* Edge-triggered, as in the live monitor: a misconfigured insert and a
         * healthy one must not read the same. */
        if (!warned_no_input) {
            fprintf(stderr, "mpe-fx-host: %s is not connected to our input — leaving the "
                            "trunk on Surge (check MPE_SL_SURGE_CLIENT)\n", g_surge_client);
            warned_no_input = 1;
        }
        give_back_trunk();
        return 0;
    }
    if (warned_no_input) {
        fprintf(stderr, "mpe-fx-host: %s reaching our input again\n", g_surge_client);
        warned_no_input = 0;
    }
    take_trunk();
    return 1;
}

static void *wiring_thread(void *arg)
{
    (void)arg;
    while (g_running) {
        atomic_store_explicit(&g_wake_pending, 0, memory_order_relaxed);
        int carrying = wiring_pass();
        write_state(carrying);

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

    char buf[64];
    while (g_running) {
        ssize_t got = recvfrom(sock, buf, sizeof(buf) - 1, 0, NULL, NULL);
        if (got <= 0) {
            continue;
        }
        buf[got] = '\0';
        int effect = 0;
        float depth = 0.0f;
        if (!fx_parse_control(buf, &effect, &depth) || effect < 0 || effect >= FX_EFFECT_COUNT) {
            continue;   /* a malformed or unknown request changes nothing */
        }
        if (depth < 0.0f) {
            depth = 0.0f;
        } else if (depth > 1.0f) {
            depth = 1.0f;
        }
        atomic_store_explicit(&g_target_effect, effect, memory_order_relaxed);
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
    const char *port = getenv("MPE_FX_HOST_PORT");
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

    if (!atomic_is_lock_free(&g_target_depth) || !atomic_is_lock_free(&g_target_effect)) {
        fprintf(stderr, "mpe-fx-host: atomics are not lock-free on this platform — refusing to start\n");
        return 1;
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
     * mpe-fx-host-01 would take the trunk beside the first. Refuse instead. */
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

    jack_set_process_callback(g_client, process, NULL);
    jack_set_sample_rate_callback(g_client, on_samplerate, NULL);
    jack_set_port_connect_callback(g_client, on_port_connect, NULL);
    jack_set_port_registration_callback(g_client, on_port_registration, NULL);
    jack_on_shutdown(g_client, on_shutdown, NULL);

    for (int ch = 0; ch < FX_CHANNELS; ch++) {
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
        /* No graph left to give anything back to. The state file keeps its
         * edge list; restore-fx-trunk.sh will find no server and do nothing,
         * and the wiring scripts rebuild the graph when jackd returns. Non-zero
         * so Restart=on-failure brings this back with it. */
        fprintf(stderr, "mpe-fx-host: jackd went away — exiting for restart\n");
        return 1;
    }

    give_back_trunk();
    /* Say what we leave behind, as the live monitor does: a stale carrying=1
     * from a process that is gone is an alarm that can never clear. Any edge
     * that could not be restored stays listed for ExecStopPost. */
    write_state(0);
    shutdown_client();
    fx_engine_free(&g_engine);
    return 0;
}
