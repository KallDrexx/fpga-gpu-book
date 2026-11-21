# A Test-Driven Course in FPGA Graphics

From blinking an LED on the Nandland Go Board to a small SPI-driven 3D GPU on the
Arty Z7. The chapters are the `Chapter *` directories; `Summary.md` is the table
of contents.

## Repository layout

| Path | Contents |
| --- | --- |
| `Chapter N.M - ...` | The book text, one directory per chapter |
| `Summary.md` | Table of contents and part titles |
| `build_book.py`, `requirements.txt` | Packages the chapters into EPUB/PDF (`output/`) |
| `harness/` | The Makefiles, simulation rules, boards and starter files you work in |

## Setting up your own project

The book repository is for reading. Do your work in a separate copy of the
harness so your code isn't mixed in with the book:

```sh
cp -r harness ~/fpga-gpu          # any location outside this repository
cd ~/fpga-gpu
git init && git add -A && git commit -m "Start from book harness"
make test                         # should print: 1 passed, 0 failed
```

`make test` needs Icarus Verilog (`iverilog`, `vvp`); building for the Go Board
also needs yosys, nextpnr-ice40, icepack and iceprog. Chapter 0.1 walks through
installing them. Chapters tell you which files to create (testbenches in `sim/`,
modules in `rtl/`, a project per step in `projects/`), always relative to the
root of this copy. `harness/README.md` lists the layout and everyday commands.

## Building the book

```sh
pip install -r requirements.txt
python build_book.py 1.0          # argument is the revision label for the title page
```
