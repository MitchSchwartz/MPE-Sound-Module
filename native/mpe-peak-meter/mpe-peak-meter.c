/*
 * mpe-peak-meter — compiled JACK leaf client for the touch UI OUT meter.
 *
 * Phase 5 (session-control-plane-spec): Python must not hold a JACK process
 * callback. This process runs the RT work; the touch UI reads /run/mpe/meter.state.
 *
 * RT callback: block peak only — no syscalls, locks, or allocation.
 * Writer thread: 5 Hz atomic read + atomic KEY=value file install (matches UI poll).
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
/* Match SurgePeakMonitor POLL_INTERVAL_S (0.2 s = 5 Hz). Decay is per write. */
#define WRITER_INTERVAL_US 200000
#define CONNECT_INTERVAL_US 2000000
/* 0.606 per 200 ms write ~= 0.92 per 33 ms (old 30 Hz writer feel). */
#define PEAK_DECAY 0.606f
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

static void write_meter_state(float peak_linear, int surge_wired, int looper_client,
                              int looper_playback, unsigned long xruns, float dsp_percent)
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
    fprintf(fh, "peak_linear=%.9g\n", peak_linear);
    /* wired= reflects Surge XT:out_{1,2} only; looper taps are best-effort. */
    fprintf(fh, "wired=%d\n", surge_wired ? 1 : 0);
    /* jack_online=1 whenever this process is on the graph (watchdog graph probe). */
    fprintf(fh, "jack_online=%d\n", 1);
    fprintf(fh, "online=%d\n", surge_wired ? 1 : 0);
    fprintf(fh, "looper_client=%d\n", looper_client ? 1 : 0);
    fprintf(fh, "looper_playback=%d\n", looper_playback ? 1 : 0);
    fprintf(fh, "source=jack\n");
    fprintf(fh, "xruns=%lu\n", xruns);
    fprintf(fh, "dsp_percent=%.3f\n", dsp_percent);
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

static int ensure_wiring(void)
{
    char src[256];
    int surge_ok = 1;
    for (int ch = 1; ch <= 2; ch++) {
        snprintf(src, sizeof(src), "%s:out_%d", g_surge_client, ch);
        if (connect_source(src, g_in_ports[ch - 1]) != 0) {
            surge_ok = 0;
        }
    }
    if (g_include_looper) {
        for (int ch = 1; ch <= 2; ch++) {
            snprintf(src, sizeof(src), "%s:common_out_%d", LOOPER_CLIENT_DEFAULT, ch);
            (void)connect_source(src, g_in_ports[ch - 1]);
        }
    }
    atomic_store_explicit(&g_surge_wired, surge_ok, memory_order_relaxed);
    atomic_store_explicit(&g_looper_client, looper_client_visible(), memory_order_relaxed);
    atomic_store_explicit(&g_looper_playback, looper_playback_wired(), memory_order_relaxed);
    return surge_ok;
}

static void *writer_thread(void *arg)
{
    (void)arg;
    float held_peak = 0.0f;
    while (g_running) {
        float window_peak =
            atomic_exchange_explicit(&g_period_peak, 0.0f, memory_order_relaxed);
        if (window_peak > held_peak) {
            held_peak = window_peak;
        } else {
            held_peak *= PEAK_DECAY;
        }
        int surge_wired = atomic_load_explicit(&g_surge_wired, memory_order_relaxed);
        int looper_client = atomic_load_explicit(&g_looper_client, memory_order_relaxed);
        int looper_playback = atomic_load_explicit(&g_looper_playback, memory_order_relaxed);
        unsigned long xruns = atomic_load_explicit(&g_xrun_count, memory_order_relaxed);
        float dsp_percent = 0.0f;
        if (g_client != NULL) {
            dsp_percent = (float)(jack_cpu_load(g_client) * 100.0);
            if (!isfinite(dsp_percent) || dsp_percent < 0.0f) {
                dsp_percent = 0.0f;
            } else if (dsp_percent > 100.0f) {
                dsp_percent = 100.0f;
            }
        }
        write_meter_state(held_peak, surge_wired, looper_client, looper_playback, xruns,
                          dsp_percent);
        interruptible_usleep(WRITER_INTERVAL_US);
    }
    write_meter_state(0.0f, 0, 0, 0, atomic_load_explicit(&g_xrun_count, memory_order_relaxed),
                      0.0f);
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
    const char *surge = getenv("MPE_PEAK_METER_SURGE_CLIENT");
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

    /* jack_on_shutdown is expected when jackd restarts (buffer change). Exit 0 so
     * Restart=on-failure does not treat a normal graph teardown as a failure. */
    (void)g_jack_shutdown;
    return 0;
}
