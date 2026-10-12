/*
 * Runs a native client's routing decisions against an in-memory graph.
 * Built by tests/test_audio_routing.py with -I<this dir> (the fake jack.h) and
 * -DCLIENT_SRC='"<path to .c>"' plus -DCLIENT_MONITOR, -DCLIENT_METER or
 * -DCLIENT_FX (which also links native/mpe-fx-host/fx_dsp.c).
 *
 * stdin, one scenario at a time:
 *     port <name>
 *     edge <a>\t<b>
 *     level <name>\t<constant sample value>   (optional; what an output port plays)
 *     end
 *
 * Buffers: an output port given a level plays that constant; any other port's
 * buffer is the sum of the levels of the ports connected to it — JACK's own
 * summing of every connection into an input. Only the meter's driver reads
 * buffers.
 * stdout, one line per scenario (see the bottom of this file).
 */
#define _GNU_SOURCE
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <jack/jack.h>

#define MAX_PORTS 128
#define MAX_EDGES 256
#define NAME_MAX_LEN 192

struct fake_jack_port { char name[NAME_MAX_LEN]; int used; int has_level; float level; };
struct fake_jack_client { int unused; };

static struct fake_jack_port fj_ports[MAX_PORTS];
static char fj_edges[MAX_EDGES][2][NAME_MAX_LEN];
static int fj_edge_count;
static struct fake_jack_client fj_client;

static void fj_reset(void)
{
    memset(fj_ports, 0, sizeof(fj_ports));
    fj_edge_count = 0;
}

static jack_port_t *fj_find(const char *name)
{
    for (int i = 0; i < MAX_PORTS; i++) {
        if (fj_ports[i].used && strcmp(fj_ports[i].name, name) == 0) {
            return &fj_ports[i];
        }
    }
    return NULL;
}

static jack_port_t *fj_add_port(const char *name)
{
    jack_port_t *p = fj_find(name);
    if (p != NULL) {
        return p;
    }
    for (int i = 0; i < MAX_PORTS; i++) {
        if (!fj_ports[i].used) {
            fj_ports[i].used = 1;
            snprintf(fj_ports[i].name, NAME_MAX_LEN, "%s", name);
            return &fj_ports[i];
        }
    }
    abort();
}

static int fj_edge_index(const char *a, const char *b)
{
    for (int i = 0; i < fj_edge_count; i++) {
        if ((strcmp(fj_edges[i][0], a) == 0 && strcmp(fj_edges[i][1], b) == 0) ||
            (strcmp(fj_edges[i][0], b) == 0 && strcmp(fj_edges[i][1], a) == 0)) {
            return i;
        }
    }
    return -1;
}

jack_client_t *jack_client_open(const char *name, jack_options_t options, jack_status_t *status, ...)
{
    (void)name; (void)options;
    if (status) *status = 0;
    return &fj_client;
}
int jack_client_close(jack_client_t *c) { (void)c; return 0; }
int jack_activate(jack_client_t *c) { (void)c; return 0; }
int jack_deactivate(jack_client_t *c) { (void)c; return 0; }
int jack_set_process_callback(jack_client_t *c, JackProcessCallback cb, void *a) { (void)c; (void)cb; (void)a; return 0; }
int jack_set_sample_rate_callback(jack_client_t *c, JackSampleRateCallback cb, void *a) { (void)c; (void)cb; (void)a; return 0; }
int jack_set_xrun_callback(jack_client_t *c, JackXRunCallback cb, void *a) { (void)c; (void)cb; (void)a; return 0; }
void jack_on_shutdown(jack_client_t *c, JackShutdownCallback cb, void *a) { (void)c; (void)cb; (void)a; }
int jack_set_port_connect_callback(jack_client_t *c, JackPortConnectCallback cb, void *a) { (void)c; (void)cb; (void)a; return 0; }
int jack_set_port_registration_callback(jack_client_t *c, JackPortRegistrationCallback cb, void *a) { (void)c; (void)cb; (void)a; return 0; }
jack_nframes_t jack_get_sample_rate(jack_client_t *c) { (void)c; return 48000; }
float jack_cpu_load(jack_client_t *c) { (void)c; return 0.0f; }
jack_port_t *jack_port_register(jack_client_t *c, const char *n, const char *t, unsigned long f, unsigned long b)
{
    (void)c; (void)t; (void)f; (void)b;
    return fj_add_port(n);
}
#define FJ_FRAMES 64
static float fj_buffers[MAX_PORTS][FJ_FRAMES];
void *jack_port_get_buffer(jack_port_t *p, jack_nframes_t n)
{
    if (n > FJ_FRAMES) abort();
    float value = 0.0f;
    if (p->has_level) {
        value = p->level;
    } else {
        for (int i = 0; i < fj_edge_count; i++) {
            const char *other = NULL;
            if (strcmp(fj_edges[i][0], p->name) == 0) other = fj_edges[i][1];
            else if (strcmp(fj_edges[i][1], p->name) == 0) other = fj_edges[i][0];
            jack_port_t *q = other ? fj_find(other) : NULL;
            if (q != NULL && q->has_level) value += q->level;
        }
    }
    float *buf = fj_buffers[p - fj_ports];
    for (jack_nframes_t k = 0; k < n; k++) buf[k] = value;
    return buf;
}
const char *jack_port_name(const jack_port_t *p) { return p->name; }
jack_port_t *jack_port_by_name(jack_client_t *c, const char *n) { (void)c; return fj_find(n); }

const char **jack_port_get_all_connections(const jack_client_t *c, const jack_port_t *port)
{
    (void)c;
    const char **out = calloc((size_t)fj_edge_count + 1, sizeof(*out));
    int k = 0;
    for (int i = 0; i < fj_edge_count; i++) {
        if (strcmp(fj_edges[i][0], port->name) == 0) out[k++] = fj_edges[i][1];
        else if (strcmp(fj_edges[i][1], port->name) == 0) out[k++] = fj_edges[i][0];
    }
    if (k == 0) {
        free(out);
        return NULL;
    }
    return out;
}

const char **jack_get_ports(jack_client_t *c, const char *n, const char *t, unsigned long f)
{
    (void)c; (void)n; (void)t; (void)f;
    return NULL;
}

int jack_connect(jack_client_t *c, const char *a, const char *b)
{
    (void)c;
    if (fj_find(a) == NULL || fj_find(b) == NULL) return -1;
    if (fj_edge_index(a, b) >= 0) return EEXIST;
    snprintf(fj_edges[fj_edge_count][0], NAME_MAX_LEN, "%s", a);
    snprintf(fj_edges[fj_edge_count][1], NAME_MAX_LEN, "%s", b);
    fj_edge_count++;
    return 0;
}

int jack_disconnect(jack_client_t *c, const char *a, const char *b)
{
    (void)c;
    int i = fj_edge_index(a, b);
    if (i < 0) return -1;
    fj_edge_count--;
    if (i != fj_edge_count) memcpy(fj_edges[i], fj_edges[fj_edge_count], sizeof(fj_edges[i]));
    return 0;
}

void jack_free(void *ptr) { free(ptr); }

/* --- the client under test, compiled into this translation unit ------------ */
#define main fake_jack_client_main
#include CLIENT_SRC
#undef main

static void print_edges(void)
{
    for (int i = 0; i < fj_edge_count; i++) {
        printf("%s%s\t%s", i ? "|" : "", fj_edges[i][0], fj_edges[i][1]);
    }
}

/* Edges as "a>b|c>d": the FX line has more fields than the monitor's, and a
 * tab inside the list would split them. */
static void print_edges_compact(void)
{
    for (int i = 0; i < fj_edge_count; i++) {
        printf("%s%s>%s", i ? "|" : "", fj_edges[i][0], fj_edges[i][1]);
    }
}

static void evaluate(void)
{
    g_client = &fj_client;
#if defined(CLIENT_FX)
    load_env();
    for (int ch = 0; ch < CHANNELS; ch++) {
        char name[64];
        snprintf(name, sizeof(name), CLIENT_NAME ":in_%d", ch + 1);
        g_in_ports[ch] = fj_add_port(name);
        snprintf(name, sizeof(name), CLIENT_NAME ":out_%d", ch + 1);
        g_out_ports[ch] = fj_add_port(name);
    }
    char s1[192], s2[192];
    fanout_source(1, s1, sizeof(s1));
    fanout_source(2, s2, sizeof(s2));
    printf("source=%s|%s\tcarrying_before=%d\t", s1, s2, trunk_insert_carrying());
    int carrying = wiring_pass();
    printf("carrying_after=%d\tedges=", carrying);
    print_edges_compact();
    leave_trunk();
    printf("\treturned=");
    print_edges_compact();
    printf("\n");
#elif defined(CLIENT_MONITOR)
    for (int ch = 0; ch < CHANNELS; ch++) {
        char name[64];
        snprintf(name, sizeof(name), CLIENT_NAME ":in_%d", ch + 1);
        g_in_ports[ch] = fj_add_port(name);
        snprintf(name, sizeof(name), CLIENT_NAME ":out_%d", ch + 1);
        g_out_ports[ch] = fj_add_port(name);
    }
    atomic_store(&g_direct_detached, 0);
    char s1[192], s2[192];
    fanout_source(1, s1, sizeof(s1));
    fanout_source(2, s2, sizeof(s2));
    printf("source=%s|%s\tcarrying_before=%d\t", s1, s2, monitor_path_live());
    ensure_wiring();
    printf("carrying_after=%d\tedges=", monitor_path_live());
    print_edges();
    printf("\n");
#else
    /* The meter: its health keys, then one connect pass, then the level it
     * reads off its tap and the clip latch over the windows that follow. */
    for (int ch = 0; ch < 2; ch++) {
        char name[64];
        snprintf(name, sizeof(name), CLIENT_NAME ":in_%d", ch + 1);
        g_in_ports[ch] = fj_add_port(name);
    }
    atomic_store(&g_period_peak, 0.0f);
    ensure_wiring();
    printf("surge_playback=%d\twired=%d\ttap=",
           atomic_load(&g_surge_playback), atomic_load(&g_surge_wired));
    for (int ch = 0; ch < 2; ch++) {
        const char **conns = jack_port_get_all_connections(g_client, g_in_ports[ch]);
        printf("%s", ch ? "|" : "");
        for (int k = 0; conns != NULL && conns[k] != NULL; k++) {
            printf("%s%s", k ? "+" : "", conns[k]);
        }
        jack_free(conns);
    }
    process(FJ_FRAMES, NULL);
    struct meter_hold hold = { 0.0f, 0 };
    meter_hold_step(&hold, atomic_exchange(&g_period_peak, 0.0f));
    printf("\tpeak=%.4f\tclip=", hold.held_peak);
    /* clip= over the clipped window and the silent windows after it. */
    for (int w = 0; w < CLIP_HOLD_WRITES + 2; w++) {
        if (w > 0) {
            meter_hold_step(&hold, 0.0f);
        }
        printf("%d", hold.clip_writes > 0);
    }
    printf("\n");
#endif
}

int main(void)
{
    char line[512];
    snprintf(g_surge_client, sizeof(g_surge_client), "%s", "Surge XT");
    fj_reset();
    while (fgets(line, sizeof(line), stdin) != NULL) {
        line[strcspn(line, "\n")] = '\0';
        if (strncmp(line, "port ", 5) == 0) {
            fj_add_port(line + 5);
        } else if (strncmp(line, "edge ", 5) == 0) {
            char *tab = strchr(line + 5, '\t');
            if (tab == NULL) abort();
            *tab = '\0';
            fj_add_port(line + 5);
            fj_add_port(tab + 1);
            jack_connect(&fj_client, line + 5, tab + 1);
        } else if (strncmp(line, "level ", 6) == 0) {
            char *tab = strchr(line + 6, '\t');
            if (tab == NULL) abort();
            *tab = '\0';
            jack_port_t *p = fj_add_port(line + 6);
            p->has_level = 1;
            p->level = strtof(tab + 1, NULL);
        } else if (strcmp(line, "end") == 0) {
            evaluate();
            fflush(stdout);
            fj_reset();
        }
    }
    return 0;
}
