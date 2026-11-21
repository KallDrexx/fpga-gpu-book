# mk/goboard.mk -- iCE40 HX1K (Nandland Go Board) build flow.
#
# A project Makefile sets these, then includes this file:
#   PROJ   := blink                      # output file names
#   SRCS   := top_goboard.v $(ROOT)/rtl/blinker.v
#   TESTS  := tb_blinker                 # testbenches this step relies on
#   TOP    := top_goboard                # optional, this is the default
#
# Targets
#   make            synthesize, place & route, pack -> build/<proj>.bin
#   make prog       build if needed, then program the board
#   make report     resource use (LUTs, RAM, IO) and timing from the last build
#   make lint       Verilator lint of the design
#   make test       run this project's TESTS (see mk/sim.mk)
#   make clean      delete this project's build/

include $(dir $(lastword $(MAKEFILE_LIST)))config.mk

DEVICE   := hx1k
PACKAGE  := vq100
PCF      ?= $(ROOT)/boards/goboard/goboard.pcf
FREQ     ?= 25
TOP      ?= top_goboard
BUILD    ?= build
DEFINES  ?=

JSON := $(BUILD)/$(PROJ).json
ASC  := $(BUILD)/$(PROJ).asc
BIN  := $(BUILD)/$(PROJ).bin

.DEFAULT_GOAL := all
.PHONY: all prog report lint clean

all: $(BIN)

$(BUILD):
	@mkdir -p $@

# Synthesis. The log keeps yosys quiet on the terminal; read it if in doubt.
$(JSON): $(SRCS) $(MAKEFILE_LIST) | $(BUILD)
	$(YOSYS) -q -l $(BUILD)/yosys.log \
	  -p 'read_verilog $(DEFINES) $(SRCS); synth_ice40 -top $(TOP) -json $@'

# Place & route. --freq makes nextpnr report pass/fail against the 25 MHz clock.
$(ASC): $(JSON) $(PCF)
	$(NEXTPNR_ICE40) -q --$(DEVICE) --package $(PACKAGE) --pcf $(PCF) \
	  --json $< --asc $@ --freq $(FREQ) --log $(BUILD)/nextpnr.log
	@$(MAKE) --no-print-directory report

$(BIN): $(ASC)
	$(ICEPACK) $< $@

prog: $(BIN)
	$(ICEPROG) $<

report:
	@echo "== $(PROJ): resource use ($(DEVICE)) =="
	@grep -E '^Info:[[:space:]]+(ICESTORM_LC|ICESTORM_RAM|SB_IO|SB_GB):[[:space:]]+[0-9]+/' \
	  $(BUILD)/nextpnr.log | sed 's/^Info:[[:space:]]*/  /'
	@grep 'Max frequency' $(BUILD)/nextpnr.log | tail -n 1 | sed 's/^Info:[[:space:]]*/  /'

lint:
	$(VERILATOR) --lint-only -Wall --top-module $(TOP) $(SRCS)

clean:
	rm -rf $(BUILD)

# Project-level `make test` runs only the testbenches listed in TESTS.
include $(dir $(lastword $(MAKEFILE_LIST)))sim.mk
