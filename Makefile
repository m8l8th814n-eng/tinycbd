CC      ?= cc
CFLAGS  ?= -O2 -g -Wall -Wextra
CPPFLAGS ?=
LDFLAGS ?=
PREFIX  ?= /usr/local
BINDIR  ?= $(PREFIX)/bin

ifneq (,$(findstring clang,$(shell $(CC) --version 2>/dev/null)))
LDFLAGS += --rtlib=compiler-rt
endif

BIN := tinycbd
SRC := tinycbd.c

all: $(BIN)

$(BIN): $(SRC)
	$(CC) $(CFLAGS) $(CPPFLAGS) -o $@ $< $(LDFLAGS)

install: $(BIN)
	install -d $(DESTDIR)$(BINDIR)
	install -m 0755 $(BIN) $(DESTDIR)$(BINDIR)/$(BIN)

clean:
	rm -f $(BIN)

.PHONY: all install clean
