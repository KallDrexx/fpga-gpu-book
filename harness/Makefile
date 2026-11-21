# Top-level Makefile -- regression tests and shortcuts into projects/.
#
#   make test                    run every testbench in sim/ (the regression)
#   make test TESTS=tb_blinker   run a subset
#   make sim-tb_blinker          run one testbench and show its output
#   make wave-tb_blinker         run one with waveforms and open GTKWave
#   make list                    list projects and testbenches
#
#   make build P=0.1-blink       build one project   (same as make -C projects/0.1-blink)
#   make prog  P=0.1-blink       build and program it
#   make report P=0.1-blink      show its resource report
#   make build-all               build every project (catches breakage in old steps)
#   make new   P=0.2-sim         start a new Go Board project from projects/_template
#   make clean                   delete all build output

include mk/sim.mk

PROJECTS := $(sort $(filter-out _%,$(patsubst projects/%/Makefile,%,$(wildcard projects/*/Makefile))))

.DEFAULT_GOAL := help
.PHONY: help list build prog report lint build-all new clean check-p

help:
	@sed -n '2,15p' Makefile | sed 's/^# \{0,1\}//'

list:
	@echo "Projects:";    printf '  %s\n' $(PROJECTS)
	@echo "Testbenches:"; printf '  %s\n' $(ALL_TESTS)

check-p:
	@test -n "$(P)" || { echo "Set P=<project>, one of: $(PROJECTS)"; exit 1; }
	@test -f projects/$(P)/Makefile || { echo "No project projects/$(P)"; exit 1; }

build: check-p
	$(MAKE) -C projects/$(P)

prog report lint: check-p
	$(MAKE) -C projects/$(P) $@

build-all:
	@set -e; for p in $(PROJECTS); do echo "== $$p =="; $(MAKE) --no-print-directory -C projects/$$p; done

new:
	@test -n "$(P)" || { echo "Usage: make new P=<step>-<name>"; exit 1; }
	@test ! -e projects/$(P) || { echo "projects/$(P) already exists"; exit 1; }
	cp -r projects/_template projects/$(P)
	sed -i 's/^PROJ  := .*/PROJ  := $(lastword $(subst -, ,$(P)))/' projects/$(P)/Makefile
	@echo "Created projects/$(P). Edit its Makefile (SRCS, TESTS) and top_goboard.v."

clean: clean-sim
	@for p in $(PROJECTS) _template; do $(MAKE) --no-print-directory -C projects/$$p clean; done
	rm -rf build
