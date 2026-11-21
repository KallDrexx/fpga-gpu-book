// Step X.Y -- <title> (Go Board top level)
//
// Keep this file thin: pins in, pins out, instantiate portable modules from rtl/.
// Port names must match boards/goboard/goboard.pcf.

`default_nettype none

module top_goboard (
    input  wire i_Clk,
    output wire o_LED_1
);
    assign o_LED_1 = 1'b0;
endmodule

`default_nettype wire
