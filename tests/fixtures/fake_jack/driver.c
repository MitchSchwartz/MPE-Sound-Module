/*
 * Runs a native client's routing decisions against an in-memory graph.
 * Built by tests/test_audio_routing.py with -I<this dir> (the fake jack.h) and
 * -DCLIENT_SRC='"<path to .c>"' plus -DCLIENT_MONITOR or -DCLIENT_METER.
 *
 * stdin, one scenario at a time:
 *     port <name>
 *     edge <a>\t<b>
 *     end
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

struct fake_jack_port { char name[NAME_MAX_LEN]; int used; };
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
jack_nframes_t jack_get_sample_rate(jack_client_t *c) { (void)c; return 48000; }
float jack_cpu_load(jack_client_t *c) { (void)c; return 0.0f; }
jack_port_t *jack_port_register(jack_client_t *c, const char *n, const char *t, unsigned long f, unsigned long b)
{
    (void)c; (void)t; (void)f; (void)b;
    return fj_add_port(n);
}
void *jack_port_get_buffer(jack_port_t *p, jack_nframes_t n) { (void)p; (void)n; return NULL; }
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

static void evaluate(void)
{
    g_client = &fj_client;
#ifdef CLIENT_MONITOR
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
    printf("surge_playback=%d\n", surge_playback_wired());
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
        } else if (strcmp(line, "end") == 0) {
            evaluate();
            fflush(stdout);
            fj_reset();
        }
    }
    return 0;
}
