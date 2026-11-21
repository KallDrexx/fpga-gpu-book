# mk/sim.mk -- Icarus Verilog testbench rules.
#
# Conventions
#   * Every testbench is sim/tb_<name>.v and its top module is tb_<name>.
#   * A testbench prints exactly one line starting with PASS or FAIL, then
#     calls $finish. Anything else (no PASS line, a FAIL line, a crash or a
#     timeout) counts as a failure.
#   * Modules under rtl/ are found automatically (iverilog -y), so a testbench
#     never needs a source list. Extra non-rtl sources for one testbench can
#     be added with  tb_<name>_SRCS := path/to/file.v  in this file or the
#     including Makefile.
#   * Pass +vcd to dump waveforms (sim-% and wave-% do this for you).
#
# Targets
#   make test                     run TESTS (default: every sim/tb_*.v)
#   make test TESTS="tb_a tb_b"   run a chosen subset
#   make sim-tb_blinker           run one testbench, print its full output
#   make wave-tb_blinker          run one with +vcd and open GTKWave
#   make list-tests               list every testbench
#   make clean-sim                delete build/sim

include $(dir $(lastword $(MAKEFILE_LIST)))config.mk

SIM_BUILD := $(ROOT)/build/sim
ALL_TESTS := $(sort $(basename $(notdir $(wildcard $(ROOT)/sim/tb_*.v))))
TESTS     ?= $(ALL_TESTS)

IVFLAGS   ?= -g2012 -Wall -Wno-timescale
SIMFLAGS  ?=
IVLIB     := $(addprefix -y ,$(RTL_DIRS)) $(addprefix -I ,$(RTL_DIRS)) -Y .v

.PHONY: test list-tests clean-sim
.SECONDARY:

$(SIM_BUILD):
	@mkdir -p $@

# Compile. Depends on all of rtl/ so any RTL edit triggers a rebuild.
$(SIM_BUILD)/%.vvp: $(ROOT)/sim/%.v $(RTL_SRCS) | $(SIM_BUILD)
	$(IVERILOG) $(IVFLAGS) -s $* -o $@ $(IVLIB) $< $($*_SRCS)

# Run every test, keep going after failures, print a summary, and exit
# non-zero if anything failed (so CI and git hooks can rely on it).
test: $(addprefix $(SIM_BUILD)/,$(addsuffix .vvp,$(TESTS)))
	@pass=0; fail=0; \
	for t in $(TESTS); do \
	  log=$(SIM_BUILD)/$$t.log; \
	  ( cd $(SIM_BUILD) && $(VVP) -n $$t.vvp $(SIMFLAGS) ) > $$log 2>&1; \
	  if grep -q '^PASS' $$log && ! grep -q '^FAIL' $$log; then \
	    echo "  PASS  $$t"; pass=$$((pass+1)); \
	  else \
	    echo "  FAIL  $$t   (log: build/sim/$$t.log)"; fail=$$((fail+1)); \
	  fi; \
	done; \
	echo "----"; echo "$$pass passed, $$fail failed"; \
	test $$fail -eq 0

sim-%: $(SIM_BUILD)/%.vvp
	cd $(SIM_BUILD) && $(VVP) -n $*.vvp +vcd $(SIMFLAGS) | tee $*.log
	@grep -q '^PASS' $(SIM_BUILD)/$*.log && ! grep -q '^FAIL' $(SIM_BUILD)/$*.log

wave-%: $(SIM_BUILD)/%.vvp
	cd $(SIM_BUILD) && $(VVP) -n $*.vvp +vcd $(SIMFLAGS) > $*.log; \
	$(GTKWAVE) $(SIM_BUILD)/$*.vcd >/dev/null 2>&1 &

list-tests:
	@printf '%s\n' $(ALL_TESTS)

clean-sim:
	rm -rf $(SIM_BUILD)
