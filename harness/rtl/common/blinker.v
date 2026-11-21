// blinker -- toggles o_led so it completes BLINK_HZ on/off cycles per second.
//
// Portable: no board-specific code. The clock frequency is a parameter, so
// the testbench can use a tiny CLK_HZ and finish in microseconds.
//
//   HALF_PERIOD = CLK_HZ / (2 * BLINK_HZ) clocks on, then the same off.
//   At 25 MHz and 1 Hz that is 12,500,000 clocks, which needs a 24-bit counter.

`default_nettype none

module blinker #(
    parameter integer CLK_HZ   = 25_000_000,
    parameter integer BLINK_HZ = 1
) (
    input  wire i_clk,
    output reg  o_led
);
    localparam integer HALF_PERIOD = CLK_HZ / (2 * BLINK_HZ);
    localparam integer W           = (HALF_PERIOD > 1) ? $clog2(HALF_PERIOD) : 1;
    /* verilator lint_off WIDTHTRUNC */  // HALF_PERIOD - 1 always fits in W bits
    localparam [W-1:0] LAST        = HALF_PERIOD - 1;
    /* verilator lint_on WIDTHTRUNC */

    reg [W-1:0] r_count = {W{1'b0}};

    initial o_led = 1'b0;

    always @(posedge i_clk) begin
        if (r_count == LAST) begin
            r_count <= {W{1'b0}};
            o_led   <= ~o_led;
        end else begin
            r_count <= r_count + 1'b1;
        end
    end
endmodule

`default_nettype wire
