// BUFG -- simulation model of the 7-series global clock buffer (sim only).
//
// Supplied by the book (Chapter 3.2). Copy to sim/BUFG.v. Do NOT add it to a
// project's SRCS: yosys knows the real primitive. On the chip a BUFG drives
// the clock onto the global network with a fixed delay; in simulation it is
// a plain wire.

`timescale 1ns/1ps
`default_nettype none

module BUFG (
    input  wire I,
    output wire O
);
    assign O = I;
endmodule

`default_nettype wire
