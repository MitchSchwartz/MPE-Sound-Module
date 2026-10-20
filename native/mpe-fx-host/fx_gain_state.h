/* The stage gain's last commanded value, kept in a file (fx_dsp.h, "Stage gain").
 *
 * A restarted mpe-fx-host used to start at unity and stay there until the
 * sender's next `gain` datagram, up to its resend interval (one second): a
 * patch normalized 20 dB down played 20 dB loud for that second. The control
 * thread now writes every new gain it is told to this file, and main reads it
 * back before the client is activated, so a restart starts where the last
 * process left off, with no ramp from unity.
 *
 * The file holds one line in the datagram's own format (`gain 0.10000000`) and
 * is read with the datagram's parser, so a file the parser would refuse as a
 * datagram is refused here too and the host starts at unity, as before. It
 * lives under /run (tmpfs: gone at reboot, when the sender starts over too),
 * in the unit's RuntimeDirectory, which survives restarts of the unit. Written
 * to a temporary name and renamed over, so a reader never sees half a line.
 *
 * Neither function is called from the audio callback: the write is the
 * control thread's, the read is main's before jack_activate.
 */
#ifndef FX_GAIN_STATE_H
#define FX_GAIN_STATE_H

/* Where the file is when MPE_FX_GAIN_STATE does not say. */
#define FX_GAIN_STATE_DEFAULT "/run/mpe/fx-stage-gain"

/* Write `gain <linear>` to `path` (via `path`.tmp and a rename). Returns 0 on
 * success, -1 otherwise (the old file, if any, is left as it was). Refuses
 * NaN, inf and negative values, which the engine would ignore. */
int fx_gain_state_write(const char *path, float linear);

/* Read the gain back. Returns 1 and fills `linear` when the file holds a
 * well-formed `gain` line (fx_parse_gain), 0 when it is missing, unreadable,
 * too long or malformed. */
int fx_gain_state_read(const char *path, float *linear);

/* What a restarted host starts at: the file's gain (fx_gain_state_read), held
 * to unity. A restart keeps a cut and never a boost. The file can outlive the
 * patch that wanted the boost (the directory survives jackd and fx-host
 * restarts, and only the host writes it), so a boost is left to the sender's
 * next resend, which comes only for a patch Surge has confirmed. Returns 1 and
 * fills `linear` as fx_gain_state_read does, 0 when there is nothing to
 * restore. */
int fx_gain_state_restore(const char *path, float *linear);

#ifdef FX_SELFTEST
/* fx_gain_state_restore passing a boost through: the restore check must
 * catch it. */
extern int fx_test_gain_restore_boosts;
#endif

#endif
