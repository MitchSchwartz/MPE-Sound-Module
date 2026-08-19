/*
 * mpe-xrun-probe — passive JACK client for xrun delay measurement.
 *
 * Registers no ports and no process callback. On each graph xrun,
 * jack_set_xrun_callback() fires and we read jack_get_xrun_delayed_usecs().
 * Step 0 of low-latency-512-256-spec.md — journal scraping cannot recover delay.
 *
 * Usage: mpe-xrun-probe /path/to/events.log
 */

#define _GNU_SOURCE
#include <errno.h>
#include <jack/jack.h>
#include <jack/statistics.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define CLIENT_NAME "mpe-xrun-probe"

static volatile sig_atomic_t g_running = 1;
static FILE *g_log = NULL;
static jack_client_t *g_client = NULL;

static void handle_signal(int sig)
{
    (void)sig;
    g_running = 0;
}

static void wall_iso8601(char *buf, size_t buflen)
{
    struct timespec ts;
    struct tm tm;
    clock_gettime(CLOCK_REALTIME, &ts);
    localtime_r(&ts.tv_sec, &tm);
    strftime(buf, buflen, "%Y-%m-%dT%H:%M:%S", &tm);
    size_t used = strlen(buf);
    if (used + 8 < buflen) {
        snprintf(buf + used, buflen - used, ".%03ld", ts.tv_nsec / 1000000L);
    }
}

static int on_xrun(void *arg)
{
    jack_client_t *client = (jack_client_t *)arg;
    float delay = jack_get_xrun_delayed_usecs(client);
    char wall[48];
    wall_iso8601(wall, sizeof(wall));
    if (g_log != NULL) {
        fprintf(g_log, "XRUN wall=%s delay_usec=%.0f\n", wall, delay);
        fflush(g_log);
    }
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
    fprintf(g_log, "PROBE_START client=%s\n", CLIENT_NAME);
    fflush(g_log);

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    g_client = jack_client_open(CLIENT_NAME, JackNoStartServer, NULL);
    if (g_client == NULL) {
        fprintf(stderr, "%s: jack_client_open failed\n", argv[0]);
        fclose(g_log);
        return 1;
    }

    jack_set_xrun_callback(g_client, on_xrun, g_client);
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

    fprintf(g_log, "PROBE_END\n");
    fflush(g_log);
    jack_deactivate(g_client);
    jack_client_close(g_client);
    fclose(g_log);
    return 0;
}
