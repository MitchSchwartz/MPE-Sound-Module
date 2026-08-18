# Build the compiled OUT peak meter (Phase 5 realtime boundary).
# Requires: libjack-dev (pkg-config jack)

PREFIX ?= /usr/local
CFLAGS ?= -O2 -Wall -Wextra -std=c11
LDFLAGS ?=

BIN := mpe-peak-meter
SRC := mpe-peak-meter.c

.PHONY: all clean install check

all: $(BIN)

check:
	@pkg-config --exists jack || (echo "libjack-dev not installed — skip native build" >&2; exit 1)
	$(MAKE) clean all

$(BIN): $(SRC)
	$(CC) $(CFLAGS) $(shell pkg-config --cflags jack) -o $@ $< $(LDFLAGS) $(shell pkg-config --libs jack) -lm -lpthread

clean:
	rm -f $(BIN)

install: $(BIN)
	install -d "$(DESTDIR)$(PREFIX)/bin"
	install -m 755 $(BIN) "$(DESTDIR)$(PREFIX)/bin/"
