// Step 0.1 -- Toolchain and blink (Go Board top level)
//
// Thin board wrapper: pin names match boards/goboard/goboard.pcf, and all
// real logic lives in portable modules under rtl/.
//
//   LED 1  blinks at 1 Hz from the 25 MHz clock (switch 1 also lights it)
//   LED n  lights while switch n is held, for n = 2..4

`default_nettype none

module top_goboard (
    input  wire i_Clk,
    input  wire i_Switch_1,
    input  wire i_Switch_2,
    input  wire i_Switch_3,
    input  wire i_Switch_4,
    output wire o_LED_1,
    output wire o_LED_2,
    output wire o_LED_3,
    output wire o_LED_4
);
    wire w_blink;

    blinker #(
        .CLK_HZ   (25_000_000),
        .BLINK_HZ (1)
    ) u_blinker (
        .i_clk (i_Clk),
        .o_led (w_blink)
    );

    assign o_LED_1 = w_blink | i_Switch_1;
    assign o_LED_2 = i_Switch_2;
    assign o_LED_3 = i_Switch_3;
    assign o_LED_4 = i_Switch_4;
endmodule

`default_nettype wire
