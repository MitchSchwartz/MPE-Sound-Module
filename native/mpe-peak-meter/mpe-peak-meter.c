/*
 * mpe-peak-meter — compiled JACK leaf client for the touch UI OUT meter.
 *
 * Phase 5 (session-control-plane-spec): Python must not hold a JACK process
 * callback. This process runs the RT work; the touch UI reads /run/mpe/meter.state.
 *
 * RT callback: block peak only — no syscalls, locks, or allocation.
 * Writer thread: ~30 Hz atomic read + atomic KEY=value file install.
 */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
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
#define WRITER_INTERVAL_US 33333
#define CONNECT_INTERVAL_US 2000000
#define METER_STATE_NAME "meter.state"

static jack_client_t *g_client;
static jack_port_t *g_in_ports[2];
static volatile sig_atomic_t g_running = 1;
static atomic_float g_period_peak = 0.0f;
static atomic_int g_wired = 0;
static char g_run_dir[512] = "/run/mpe";
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

static void on_shutdown(void *arg)
{
    (void)arg;
    g_running = 0;
}

static int env_truthy(const char *value)
{
    if (value == NULL || *value == '\0') {
        return 0;
    }
    return (strcmp(value, "1") == 0 || strcasecmp(value, "true") == 0 ||
            strcasecmp(value, "yes") == 0 || strcasecmp(value, "on") == 0);
}

static void write_meter_state(float peak_linear, int wired)
{
    char path[640];
    char tmp[640];
    snprintf(path, sizeof(path), "%s/%s", g_run_dir, METER_STATE_NAME);
    snprintf(tmp, sizeof(tmp), "%s.tmp.%d", path, (int)getpid());

    FILE *fh = fopen(tmp, "we");
    if (fh == NULL) {
        return;
    }
    fprintf(fh, "peak_linear=%.9g\n", peak_linear);
    fprintf(fh, "wired=%d\n", wired ? 1 : 0);
    fprintf(fh, "online=%d\n", wired ? 1 : 0);
    fprintf(fh, "source=jack\n");
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
    int ok = 1;
    for (int ch = 1; ch <= 2; ch++) {
        snprintf(src, sizeof(src), "%s:out_%d", g_surge_client, ch);
        if (connect_source(src, g_in_ports[ch - 1]) != 0) {
            ok = 0;
        }
    }
    if (g_include_looper) {
        for (int ch = 1; ch <= 2; ch++) {
            snprintf(src, sizeof(src), "%s:common_out_%d", LOOPER_CLIENT_DEFAULT, ch);
            (void)connect_source(src, g_in_ports[ch - 1]);
        }
    }
    atomic_store_explicit(&g_wired, ok, memory_order_relaxed);
    return ok;
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
            held_peak *= 0.92f;
        }
        int wired = atomic_load_explicit(&g_wired, memory_order_relaxed);
        write_meter_state(held_peak, wired);
        usleep(WRITER_INTERVAL_US);
    }
    write_meter_state(0.0f, 0);
    return NULL;
}

static void *connect_thread(void *arg)
{
    (void)arg;
    while (g_running) {
        ensure_wiring();
        usleep(CONNECT_INTERVAL_US);
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

    load_env();
    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    g_client = jack_client_open(CLIENT_NAME, JackNoStartServer, NULL);
    if (g_client == NULL) {
        fprintf(stderr, "mpe-peak-meter: jack_client_open failed\n");
        return 1;
    }

    jack_set_process_callback(g_client, process, NULL);
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
        jack_client_close(g_client);
        return 1;
    }
    if (pthread_create(&connector, NULL, connect_thread, NULL) != 0) {
        g_running = 0;
        pthread_join(writer, NULL);
        jack_client_close(g_client);
        return 1;
    }

    while (g_running) {
        sleep(1);
    }

    pthread_join(connector, NULL);
    pthread_join(writer, NULL);
    jack_deactivate(g_client);
    jack_client_close(g_client);
    return 0;
}
