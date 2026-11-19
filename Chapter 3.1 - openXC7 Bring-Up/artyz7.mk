# mk/artyz7.mk -- Zynq xc7z020 (Digilent Arty Z7-20) build flow, openXC7.
# Copy to mk/artyz7.mk.
#
# Run `source <openxc7>/export.sh` first. It puts the openXC7 tools on PATH
# and sets NEXTPNR_XILINX_DIR and PRJXRAY_DB_DIR, which this file uses.
#
# A project Makefile sets these, then includes this file:
#   PROJ   := arty_blink                 # output file names
#   SRCS   := top_artyz7.v $(ROOT)/rtl/common/blinker.v
#   TESTS  := tb_arty_blink              # testbenches this step relies on
#   TOP    := top_artyz7                 # optional, this is the default
#
# Targets
#   make            synthesize, place & route, assemble -> build/<proj>.bit
#   make prog       build if needed, then load the bitstream over JTAG
#   make report     resource use and timing from the last build
#   make lint       Verilator lint of the design
#   make test       run this project's TESTS (see mk/sim.mk)
#   make clean      delete this project's build/
#   make chipdb     build the nextpnr chip database if it is missing

include $(dir $(lastword $(MAKEFILE_LIST)))config.mk

# --- Tools (override like any config.mk variable) -------------------------
NEXTPNR_XILINX ?= nextpnr-xilinx
FPGA_AS        ?= fpga-as
BBASM          ?= bbasm
OPENFPGALOADER ?= openFPGALoader

# --- Part and board -------------------------------------------------------
PART     := xc7z020clg400-1
DEVICE   := xc7z020clg400
FABRIC   := xc7z020
FAMILY   := zynq7
BOARD    := arty_z7_20
XDC      ?= $(ROOT)/boards/arty-z7-20/arty-z7-20.xdc
TOP      ?= top_artyz7
BUILD    ?= build
DEFINES  ?=

# The chip database: nextpnr's description of every tile, site and wire.
# It lives with the toolchain, not in the repo, and is built once.
CHIPDB_DIR ?= $(NEXTPNR_XILINX_DIR)/chipdb
CHIPDB     ?= $(CHIPDB_DIR)/chipdb-$(DEVICE).bin
XILINX_GEN ?= $(NEXTPNR_XILINX_DIR)/share/nextpnr/himbaechel/uarch/xilinx/gen/xilinx_gen.py

JSON := $(BUILD)/$(PROJ).json
FASM := $(BUILD)/$(PROJ).fasm
BIT  := $(BUILD)/$(PROJ).bit

.DEFAULT_GOAL := all
.PHONY: all prog report lint clean chipdb check-env
# A failed step must not leave a half-written .bit that looks up to date.
.DELETE_ON_ERROR:

all: $(BIT)

$(BUILD):
	@mkdir -p $@

check-env:
	@test -n "$(PRJXRAY_DB_DIR)" -a -n "$(NEXTPNR_XILINX_DIR)" || \
	  { echo "openXC7 environment not set: run 'source <openxc7>/export.sh' first"; exit 1; }

# Synthesis to Xilinx 7-series primitives (LUT1-6, FDRE, CARRY4, BRAM, DSP48E1).
$(JSON): $(SRCS) $(MAKEFILE_LIST) | $(BUILD)
	$(YOSYS) -q -l $(BUILD)/yosys.log \
	  -p 'read_verilog $(DEFINES) $(SRCS); synth_xilinx -flatten -abc9 -arch xc7 -top $(TOP); write_json $@'

chipdb: check-env $(CHIPDB)

$(CHIPDB): | check-env $(BUILD)
	@echo "Building $@ (one time only, about a minute)"
	@mkdir -p $(dir $@)
	python3 $(XILINX_GEN) --xray $(PRJXRAY_DB_DIR)/$(FAMILY) --device $(FABRIC) --bba $(BUILD)/chipdb.bba
	$(BBASM) -l $(BUILD)/chipdb.bba $@
	rm -f $(BUILD)/chipdb.bba

# Place & route. The clock period comes from create_clock in the XDC.
$(FASM): $(JSON) $(XDC) | $(CHIPDB)
	$(NEXTPNR_XILINX) --chipdb $(CHIPDB) --xdc $(XDC) --json $< --fasm $@ \
	  -q --log $(BUILD)/nextpnr.log
	@$(MAKE) --no-print-directory report

# FASM (a text list of features to turn on) -> configuration frames -> .bit.
$(BIT): $(FASM) | check-env
	$(FPGA_AS) --prjxray_db_path=$(PRJXRAY_DB_DIR)/$(FAMILY) --part $(PART) $< > $@

# JTAG load into the fabric's configuration SRAM. Lost at power-off.
prog: $(BIT)
	$(OPENFPGALOADER) -b $(BOARD) $<

report:
	@echo "== $(PROJ): resource use ($(PART)) =="
	@sed -n '/Device utilisation/,/^Info: *$$/p' $(BUILD)/nextpnr.log | \
	  grep -E '[1-9][0-9]*/ *[0-9]+' | sed 's/^Info:[[:space:]]*/  /'
	@grep 'Max frequency' $(BUILD)/nextpnr.log | tail -n 1 | sed 's/^Info:[[:space:]]*/  /'

lint:
	$(VERILATOR) --lint-only -Wall --top-module $(TOP) $(SRCS)

clean:
	rm -rf $(BUILD)

# Project-level `make test` runs only the testbenches listed in TESTS.
include $(dir $(lastword $(MAKEFILE_LIST)))sim.mk
