# mk/config.mk -- tool names and repo paths shared by every Makefile.
#
# Every tool is a ?= variable, so you can override it from the command line
# or the environment, e.g.  make YOSYS=yowasp-yosys  or  export IVERILOG=...

# Absolute path of the repo root (the directory above mk/).
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)

# --- Synthesis / place & route / programming (OSS CAD Suite) -------------
YOSYS          ?= yosys
NEXTPNR_ICE40  ?= nextpnr-ice40
ICEPACK        ?= icepack
ICEPROG        ?= iceprog

# --- Simulation ----------------------------------------------------------
IVERILOG       ?= iverilog
VVP            ?= vvp
GTKWAVE        ?= gtkwave
VERILATOR      ?= verilator

# --- Host side -----------------------------------------------------------
PYTHON         ?= python3

# Portable RTL lives in rtl/ and its subdirectories (one module per file,
# file named after the module). Testbenches find modules here automatically.
RTL_DIRS := $(patsubst %/,%,$(sort $(dir $(wildcard $(ROOT)/rtl/*.v $(ROOT)/rtl/*/*.v))))
RTL_SRCS := $(wildcard $(ROOT)/rtl/*.v $(ROOT)/rtl/*/*.v)
