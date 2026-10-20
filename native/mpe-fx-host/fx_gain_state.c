/* The stage gain's last commanded value, kept in a file. See fx_gain_state.h. */

#define _GNU_SOURCE
#include "fx_gain_state.h"
#include "fx_dsp.h"

#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

/* `gain ` + eight decimals of up to FX_GAIN_MAX_DB (251.19) + newline is 20
 * bytes; anything near this size is not a file this wrote. */
#define STATE_MAX 64

int fx_gain_state_write(const char *path, float linear)
{
    if (path == NULL || !isfinite(linear) || linear < 0.0f) {
        return -1;
    }
    char tmp[4096];
    char line[STATE_MAX];
    if (snprintf(tmp, sizeof(tmp), "%s.tmp", path) >= (int)sizeof(tmp)) {
        return -1;
    }
    const int len = snprintf(line, sizeof(line), "gain %.8f\n", (double)linear);
    if (len <= 0 || len >= (int)sizeof(line)) {
        return -1;
    }
    const int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) {
        return -1;
    }
    const ssize_t put = write(fd, line, (size_t)len);
    if (close(fd) != 0 || put != len || rename(tmp, path) != 0) {
        (void)unlink(tmp);
        return -1;
    }
    return 0;
}

int fx_gain_state_read(const char *path, float *linear)
{
    if (path == NULL) {
        return 0;
    }
    const int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        return 0;
    }
    char buf[STATE_MAX + 1];
    const ssize_t got = read(fd, buf, sizeof(buf));
    (void)close(fd);
    if (got <= 0 || got > STATE_MAX) {
        return 0;
    }
    buf[got] = '\0';
    if (memchr(buf, '\0', (size_t)got) != NULL) {
        return 0;
    }
    float value = 0.0f;
    if (!fx_parse_gain(buf, &value)) {
        return 0;
    }
    *linear = value;
    return 1;
}

#ifdef FX_SELFTEST
int fx_test_gain_restore_boosts;
#endif

int fx_gain_state_restore(const char *path, float *linear)
{
    float value = 1.0f;
    if (!fx_gain_state_read(path, &value)) {
        return 0;
    }
#ifdef FX_SELFTEST
    if (fx_test_gain_restore_boosts) {
        *linear = value;
        return 1;
    }
#endif
    *linear = value > 1.0f ? 1.0f : value;
    return 1;
}
