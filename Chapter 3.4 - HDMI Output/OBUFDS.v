// OBUFDS -- simulation model of the 7-series differential output buffer
// (sim only).
//
// Supplied by the book (Chapter 3.4). Copy to sim/OBUFDS.v. Do NOT add it to
// a project's SRCS: yosys knows the real primitive. On the chip it drives a
// pin pair, O true and OB inverted, in the IOSTANDARD the .xdc gives (TMDS_33
// for HDMI). In simulation O follows I and OB is its inverse; an X or Z
// input gives X on both.

`timescale 1ns/1ps
`default_nettype none

module OBUFDS #(
    parameter CAPACITANCE = "DONT_CARE",
    parameter IOSTANDARD  = "DEFAULT",
    parameter SLEW        = "SLOW"
) (
    input  wire I,
    output wire O,
    output wire OB
);
    assign O  = (I === 1'b1) ? 1'b1 : (I === 1'b0) ? 1'b0 : 1'bx;
    assign OB = (I === 1'b1) ? 1'b0 : (I === 1'b0) ? 1'b1 : 1'bx;
endmodule

`default_nettype wire
