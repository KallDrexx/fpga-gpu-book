// tb_hex_to_7seg -- self-checking testbench for Step 0.2 (Simulation workflow).
//
// Copy to sim/tb_hex_to_7seg.v. Expects a module `hex_to_7seg` in rtl/ (for
// example rtl/common/hex_to_7seg.v):
//
//   module hex_to_7seg (input  wire [3:0] i_hex,
//                       output reg  [6:0] o_seg);   // a wire output is fine too
//
// o_seg is ACTIVE HIGH (1 = segment lit), bit 0 = segment A ... bit 6 = G:
//
//        --A--
//       |     |
//       F     B
//       |     |
//        --G--
//       |     |
//       E     C
//       |     |
//        --D--
//
// The decoder must be purely combinational: there is no clock. The testbench
// applies all 16 inputs (in order, then in a scrambled order so a design that
// only works when counting up is caught), waits 1 ns after each change and
// compares o_seg against the table below. Mismatches are reported by segment
// letter.
//
// Prints exactly one PASS or FAIL line, then $finish. Run with +vcd to dump
// tb_hex_to_7seg.vcd.

`timescale 1ns/1ps
`default_nettype none

module tb_hex_to_7seg;
    // Starts as X, so the first value applied is a change even for an
    // `always @(*)` decoder (which only wakes up when its inputs change).
    reg  [3:0] hex;
    wire [6:0] seg;

    hex_to_7seg dut (.i_hex(hex), .o_seg(seg));

    // Expected patterns, {G,F,E,D,C,B,A}.
    reg [6:0] expected [0:15];
    initial begin
        expected[4'h0] = 7'b0111111;   // A B C D E F
        expected[4'h1] = 7'b0000110;   //   B C
        expected[4'h2] = 7'b1011011;   // A B   D E   G
        expected[4'h3] = 7'b1001111;   // A B C D     G
        expected[4'h4] = 7'b1100110;   //   B C     F G
        expected[4'h5] = 7'b1101101;   // A   C D   F G
        expected[4'h6] = 7'b1111101;   // A   C D E F G
        expected[4'h7] = 7'b0000111;   // A B C
        expected[4'h8] = 7'b1111111;   // A B C D E F G
        expected[4'h9] = 7'b1101111;   // A B C D   F G
        expected[4'hA] = 7'b1110111;   // A B C   E F G
        expected[4'hB] = 7'b1111100;   //     C D E F G   (lower-case b)
        expected[4'hC] = 7'b0111001;   // A     D E F
        expected[4'hD] = 7'b1011110;   //   B C D E   G   (lower-case d)
        expected[4'hE] = 7'b1111001;   // A     D E F G
        expected[4'hF] = 7'b1110001;   // A       E F G
    end

    integer errors = 0;
    integer checks = 0;
    integer i;

    // Names of the segments that differ, e.g. "A G", for the error message.
    function [8*14-1:0] seg_names(input [6:0] diff);
        integer s;
        begin
            seg_names = "";
            for (s = 0; s < 7; s = s + 1)
                if (diff[s]) seg_names = {seg_names[8*12-1:0], " ", "A" + s[7:0]};
        end
    endfunction

    task check(input [3:0] value);
        begin
            hex = value;
            #1;
            checks = checks + 1;
            if (^seg === 1'bx) begin
                if (errors < 8)
                    $display("  digit %h: o_seg is %b (contains X or Z)", value, seg);
                errors = errors + 1;
            end else if (seg !== expected[value]) begin
                if (errors < 8)
                    $display("  digit %h: o_seg = %b, expected %b (wrong segment(s):%0s)",
                             value, seg, expected[value], seg_names(seg ^ expected[value]));
                errors = errors + 1;
            end
        end
    endtask

    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_hex_to_7seg.vcd");
            $dumpvars(0, tb_hex_to_7seg);
        end

        #1;
        for (i = 0; i < 16; i = i + 1)
            check(i[3:0]);

        // Same digits in a scrambled order (i * 7 mod 16 visits all 16).
        for (i = 0; i < 16; i = i + 1)
            check((i * 7 + 3) % 16);

        if (errors == 0)
            $display("PASS tb_hex_to_7seg: all 16 digits match the table (%0d checks)", checks);
        else
            $display("FAIL tb_hex_to_7seg: %0d of %0d checks wrong", errors, checks);
        $finish;
    end

    // Watchdog: the whole test takes about 33 ns.
    initial begin
        #10_000;
        $display("FAIL tb_hex_to_7seg: timeout");
        $finish;
    end
endmodule

`default_nettype wire
