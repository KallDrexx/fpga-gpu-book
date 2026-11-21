# Step 0.1 -- Toolchain and blink
#
#   make            build build/blink.bin and print the resource report
#   make prog       program the Go Board
#   make clean prog rebuild from nothing and program (the "Done when" check)
#   make test       run this step's testbenches
#   make lint       Verilator lint

PROJ  := blink
TOP   := top_goboard
include ../../mk/config.mk

SRCS  := top_goboard.v \
         $(ROOT)/rtl/common/blinker.v

TESTS := tb_blinker

include $(ROOT)/mk/goboard.mk
