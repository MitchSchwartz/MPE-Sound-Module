/*
 * mpe-xrun-probe — passive JACK client for latency measurement.
 *
 * Process callback: monotonic inter-callback period jitter + frames_since_cycle_start.
 * Xrun callback: event count only (jack_get_xrun_delayed_usecs is 0 on JACK2/ALSA).
 *
 * Usage: mpe-xrun-probe /path/to/events.log
 *
 * See Documents/specs/low-latency-512-256-spec.md Step 0.
 */

#define _GNU_SOURCE
#include <errno.h>
#include <jack/jack.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define CLIENT_NAME "mpe-xrun-probe"
#define MAX_SAMPLES 16384

static volatile sig_atomic_t g_running = 1;
static FILE *g_log = NULL;
static jack_client_t *g_client = NULL;

static jack_nframes_t g_buffer_frames = 0;
static jack_nframes_t g_sample_rate = 48000;
static double g_expected_period_us = 0.0;

static struct timespec g_last_mono;
static int g_have_last_mono = 0;

static double g_period_err_us[MAX_SAMPLES];
static double g_frames_late_us[MAX_SAMPLES];
static atomic_ulong g_sample_count = 0;
static atomic_ulong g_xrun_count = 0;

static void handle_signal(int sig)
{
    (void)sig;
    g_running = 0;
}

static double timespec_delta_us(const struct timespec *a, const struct timespec *b)
{
    double sec = (double)(a->tv_sec - b->tv_sec);
    double nsec = (double)(a->tv_nsec - b->tv_nsec);
    return sec * 1000000.0 + nsec / 1000.0;
}

static int compare_double(const void *a, const void *b)
{
    double da = *(const double *)a;
    double db = *(const double *)b;
    if (da < db) {
        return -1;
    }
    if (da > db) {
        return 1;
    }
    return 0;
}

static double percentile_sorted(const double *sorted, size_t n, double p)
{
    if (n == 0) {
        return 0.0;
    }
    if (n == 1) {
        return sorted[0];
    }
    double idx = p * (double)(n - 1);
    size_t lo = (size_t)idx;
    size_t hi = lo + 1;
    if (hi >= n) {
        return sorted[n - 1];
    }
    double frac = idx - (double)lo;
    return sorted[lo] * (1.0 - frac) + sorted[hi] * frac;
}

static void write_summary(const char *tag, double *samples, size_t n)
{
    if (g_log == NULL || n == 0) {
        if (g_log != NULL) {
            fprintf(g_log, "%s n=0\n", tag);
        }
        return;
    }
    qsort(samples, n, sizeof(double), compare_double);
    fprintf(g_log,
            "%s expected_period_us=%.0f n=%zu median_usec=%.0f p99_usec=%.0f "
            "p99_9_usec=%.0f max_usec=%.0f\n",
            tag, g_expected_period_us, n, percentile_sorted(samples, n, 0.50),
            percentile_sorted(samples, n, 0.99), percentile_sorted(samples, n, 0.999),
            samples[n - 1]);
}

static void flush_summaries(void)
{
    size_t n = (size_t)atomic_load_explicit(&g_sample_count, memory_order_relaxed);
    if (n > MAX_SAMPLES) {
        n = MAX_SAMPLES;
    }
    write_summary("JITTER_SUMMARY", g_period_err_us, n);
    write_summary("FRAMES_LATE_SUMMARY", g_frames_late_us, n);
    fprintf(g_log, "XRUN_COUNT %lu\n",
            (unsigned long)atomic_load_explicit(&g_xrun_count, memory_order_relaxed));
    fflush(g_log);
}

static int on_process(jack_nframes_t nframes, void *arg)
{
    (void)nframes;
    jack_client_t *client = (jack_client_t *)arg;
    struct timespec now;

    clock_gettime(CLOCK_MONOTONIC, &now);
    if (!g_have_last_mono) {
        g_last_mono = now;
        g_have_last_mono = 1;
        return 0;
    }

    unsigned long idx = atomic_fetch_add_explicit(&g_sample_count, 1UL, memory_order_relaxed);
    if (idx >= MAX_SAMPLES) {
        return 0;
    }

    double period_us = timespec_delta_us(&now, &g_last_mono);
    g_last_mono = now;
    g_period_err_us[idx] = period_us - g_expected_period_us;

    jack_nframes_t late_frames = jack_frames_since_cycle_start(client);
    g_frames_late_us[idx] = (double)late_frames * 1000000.0 / (double)g_sample_rate;

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
    g_running = 0;
}

int main(int argc, char **argv)
{
    if (argc != 2) {
        fprintf(stderr, "Usage: %s /path/to/events.log\n", argv[0]);
        return 2;
    }

    g_log = fopen(argv[1], "we");
    if (g_log == NULL) {
        fprintf(stderr, "%s: cannot open %s: %s\n", argv[0], argv[1], strerror(errno));
        return 1;
    }

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    g_client = jack_client_open(CLIENT_NAME, JackNoStartServer, NULL);
    if (g_client == NULL) {
        fprintf(stderr, "%s: jack_client_open failed\n", argv[0]);
        fclose(g_log);
        return 1;
    }

    g_buffer_frames = jack_get_buffer_size(g_client);
    g_sample_rate = jack_get_sample_rate(g_client);
    g_expected_period_us = (double)g_buffer_frames * 1000000.0 / (double)g_sample_rate;

    fprintf(g_log, "PROBE_START client=%s buffer_frames=%u sample_rate=%u expected_period_us=%.0f\n",
            CLIENT_NAME, (unsigned)g_buffer_frames, (unsigned)g_sample_rate, g_expected_period_us);
    fflush(g_log);

    jack_set_process_callback(g_client, on_process, g_client);
    jack_set_xrun_callback(g_client, on_xrun, NULL);
    jack_on_shutdown(g_client, on_shutdown, NULL);

    if (jack_activate(g_client) != 0) {
        fprintf(stderr, "%s: jack_activate failed\n", argv[0]);
        jack_client_close(g_client);
        fclose(g_log);
        return 1;
    }

    while (g_running) {
        pause();
    }

    jack_deactivate(g_client);
    flush_summaries();
    fprintf(g_log, "PROBE_END\n");
    fflush(g_log);
    jack_client_close(g_client);
    fclose(g_log);
    return 0;
}
