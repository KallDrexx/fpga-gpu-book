# Project harness

The Makefiles, simulation rules and starter files the book builds on. Copy this
directory out of the book repository to start your own project (see the root README).


## Layout

```
Makefile              regression tests + shortcuts into projects/
mk/
  config.mk           tool names (all overridable) and repo paths
  sim.mk              Icarus testbench rules: make test / sim-% / wave-%
  goboard.mk          iCE40 HX1K flow: yosys -> nextpnr-ice40 -> icepack -> iceprog
boards/goboard/       goboard.pcf, every Go Board pin
rtl/                  portable modules, one per file, file named after the module
  common/             blinker.v ...
sim/                  self-checking testbenches, sim/tb_<name>.v
projects/
  0.1-blink/          one directory per step: Makefile + thin top_goboard.v
  _template/          copied by `make new`
build/                all generated output (git-ignored)
```

Later phases add `mk/artyz7.mk` and `boards/artyz7/` for the openXC7 flow, and
Python golden models alongside the frame-dump harness in Step 1.2.

## Everyday commands

From the repo root:

| Command | What it does |
| --- | --- |
| `make test` | Runs every testbench in `sim/`, prints a PASS/FAIL summary, exits non-zero on any failure |
| `make test TESTS=tb_blinker` | Runs a subset |
| `make sim-tb_blinker` | Runs one testbench and shows its output (also writes a VCD) |
| `make wave-tb_blinker` | Runs one and opens the waveform in GTKWave |
| `make build P=0.1-blink` | Builds one project |
| `make prog P=0.1-blink` | Builds and programs the board |
| `make build-all` | Builds every project, to catch a change that breaks an old step |
| `make new P=0.2-sim` | Starts a new project from the template |
| `make list` / `make clean` | Lists projects and testbenches / deletes all build output |

Inside a project directory (`cd projects/0.1-blink`): `make`, `make prog`,
`make clean prog`, `make report`, `make lint`, and `make test` (only that step's `TESTS`).

Tool names can be overridden, e.g. `make prog ICEPROG="openFPGALoader -b ice40_generic"`
or `make YOSYS=yowasp-yosys NEXTPNR_ICE40=yowasp-nextpnr-ice40 ICEPACK=yowasp-icepack`.

## Conventions

- **Testbenches** are `sim/tb_<name>.v` with top module `tb_<name>`. Each prints exactly
  one line starting with `PASS` or `FAIL` and then calls `$finish`. A missing PASS line,
  any FAIL line, or a timeout counts as failure. Include a watchdog so a hang still FAILs.
- **No source lists for tests.** iverilog searches `rtl/` and its subdirectories
  (`-y`), so a module is found as long as its file is named after it.
- **Waveforms** are dumped only with `+vcd`, keeping `make test` fast.
- **Board code stays thin.** `top_goboard.v` maps pins to portable modules and holds
  nothing else, so Phase 3 can reuse every core under `top_artyz7.v`.
- **Read the report** after every build (`make report`); it prints LUT, RAM and IO use
  and the max clock frequency.
- **Commit each step with its testbench**, and run `make test` before committing.

## Step 0.1 checklist

1. Install OSS CAD Suite and put its `bin/` on `PATH`; `pip install -r requirements.txt`.
2. `make test` prints `1 passed, 0 failed`.
3. `cd projects/0.1-blink && make clean prog` rebuilds from nothing and programs the board.
4. LED 1 blinks 60 times in 60 seconds on a stopwatch; switches 1-4 light LEDs 1-4.
