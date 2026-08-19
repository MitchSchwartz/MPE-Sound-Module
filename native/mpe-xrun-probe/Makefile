# Passive JACK xrun delay probe — libjack-dev required.

PREFIX ?= /usr/local
CFLAGS ?= -O2 -Wall -Wextra -std=c11
LDFLAGS ?=

BIN := mpe-xrun-probe
SRC := mpe-xrun-probe.c

.PHONY: all clean install check

all: $(BIN)

check:
	@pkg-config --exists jack || (echo "libjack-dev not installed" >&2; exit 1)
	$(MAKE) clean all

$(BIN): $(SRC)
	$(CC) $(CFLAGS) $(shell pkg-config --cflags jack) -o $@ $< $(LDFLAGS) $(shell pkg-config --libs jack)

clean:
	rm -f $(BIN)

install: $(BIN)
	install -d "$(DESTDIR)$(PREFIX)/bin"
	install -m 755 $(BIN) "$(DESTDIR)$(PREFIX)/bin/"
