# frame.mk -- frame-dump test rules (Chapter 1.2).
#
# Include from the top-level Makefile (after its own variables):
#     include frame.mk
#
# For a frame test called NAME you provide:
#     sim/tb_frame_NAME.v   writes build/frame_NAME_<label>.ppm, prints PASS/FAIL
#     sim/py/ref_NAME.py    run as `ref_NAME.py build`, writes build/ref_NAME_<label>.ppm
# Then `make frame-NAME` simulates, draws the references and compares them.
# Add NAME to FRAME_TESTS so `make frame` (and `make test`, if you wish) runs it.

FRAME_TESTS += patterns

PYTHON  ?= python3
FRAME_IVERILOG = iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim

.PHONY: frame
frame: $(addprefix frame-,$(FRAME_TESTS))

frame-%:
	@mkdir -p build
	@rm -f build/frame_$*_*.ppm build/ref_$*_*.ppm build/diff_$*_*.ppm
	$(FRAME_IVERILOG) -o build/tb_frame_$*.vvp sim/tb_frame_$*.v
	vvp -n build/tb_frame_$*.vvp | tee build/tb_frame_$*.log
	@grep -q '^PASS' build/tb_frame_$*.log
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) sim/py/ref_$*.py build
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) sim/py/compare.py --set build $*
