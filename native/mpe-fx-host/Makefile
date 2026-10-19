# Build the trunk effects insert (see mpe-fx-host.c) and its DSP self-test.
# Requires: libjack-dev (pkg-config jack) for the client; only a C compiler for
# the self-test, which runs without JACK.

PREFIX ?= /usr/local
CFLAGS ?= -O2 -Wall -Wextra -std=c11
LDFLAGS ?=

BIN := mpe-fx-host
SRC := mpe-fx-host.c fx_dsp.c fx_gain_state.c
HDR := fx_dsp.h fx_gain_state.h

# Overridable so the unit tests can build into a temp dir, not the tree.
SELFTEST_BIN ?= fx-selftest

.PHONY: all clean install check selftest selftest-bin

all: $(BIN)

check:
	@pkg-config --exists jack || (echo "libjack-dev not installed — skip native build" >&2; exit 1)
	$(MAKE) clean all

$(BIN): $(SRC) $(HDR)
	$(CC) $(CFLAGS) $(shell pkg-config --cflags jack) -o $@ $(SRC) $(LDFLAGS) $(shell pkg-config --libs jack) -lm -lpthread

# The self-test is built with -Werror: it guards the listener, so a warning in
# it is not something to scroll past. FX_SELFTEST compiles in the deliberately
# broken effect variants the checks must catch; the client never has them.
# `selftest-bin` only builds it (the wire-format tests need its parser, not the
# DSP checks); `selftest` builds and runs every check, one worker per core
# (FX_SELFTEST_JOBS=1 for the serial run).
selftest-bin:
	$(CC) $(CFLAGS) -Werror -DFX_SELFTEST -o $(SELFTEST_BIN) fx_selftest.c fx_dsp.c fx_gain_state.c $(LDFLAGS) -lm

selftest: selftest-bin
	$(abspath $(SELFTEST_BIN))

clean:
	rm -f $(BIN) fx-selftest

install: $(BIN)
	install -d "$(DESTDIR)$(PREFIX)/bin"
	install -m 755 $(BIN) "$(DESTDIR)$(PREFIX)/bin/"
