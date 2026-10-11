/*
 * A fake <jack/jack.h> for tests/test_audio_routing.py — NOT the JACK API.
 *
 * Declares just what native/mpe-live-monitor and native/mpe-peak-meter use, so
 * those files compile against an in-memory graph (fake_jack_impl.h) and their
 * routing decisions can be executed and compared with
 * patch_browser/audio_routing.py. No JACK headers or server are needed.
 */
#ifndef FAKE_JACK_H
#define FAKE_JACK_H

#include <stdint.h>

typedef uint32_t jack_nframes_t;
typedef float jack_default_audio_sample_t;
typedef struct fake_jack_port jack_port_t;
typedef struct fake_jack_client jack_client_t;

typedef enum { JackNullOption = 0, JackNoStartServer = 1, JackUseExactName = 2 } jack_options_t;
typedef enum { JackFailure = 1, JackNameNotUnique = 4 } jack_status_t;

enum { JackPortIsInput = 1, JackPortIsOutput = 2 };
#define JACK_DEFAULT_AUDIO_TYPE "32 bit float mono audio"

typedef int (*JackProcessCallback)(jack_nframes_t nframes, void *arg);
typedef int (*JackSampleRateCallback)(jack_nframes_t nframes, void *arg);
typedef int (*JackXRunCallback)(void *arg);
typedef void (*JackShutdownCallback)(void *arg);

jack_client_t *jack_client_open(const char *name, jack_options_t options, jack_status_t *status, ...);
int jack_client_close(jack_client_t *client);
int jack_activate(jack_client_t *client);
int jack_deactivate(jack_client_t *client);
int jack_set_process_callback(jack_client_t *client, JackProcessCallback cb, void *arg);
int jack_set_sample_rate_callback(jack_client_t *client, JackSampleRateCallback cb, void *arg);
int jack_set_xrun_callback(jack_client_t *client, JackXRunCallback cb, void *arg);
void jack_on_shutdown(jack_client_t *client, JackShutdownCallback cb, void *arg);
jack_nframes_t jack_get_sample_rate(jack_client_t *client);
float jack_cpu_load(jack_client_t *client);
jack_port_t *jack_port_register(jack_client_t *client, const char *name, const char *type,
                                unsigned long flags, unsigned long buffer_size);
void *jack_port_get_buffer(jack_port_t *port, jack_nframes_t nframes);
const char *jack_port_name(const jack_port_t *port);
jack_port_t *jack_port_by_name(jack_client_t *client, const char *name);
const char **jack_port_get_all_connections(const jack_client_t *client, const jack_port_t *port);
const char **jack_get_ports(jack_client_t *client, const char *name_pattern,
                            const char *type_pattern, unsigned long flags);
int jack_connect(jack_client_t *client, const char *source, const char *dest);
int jack_disconnect(jack_client_t *client, const char *source, const char *dest);
void jack_free(void *ptr);

#endif
