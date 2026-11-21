// tb_color_bars.v -- self-checking testbench for color_bars (Chapter 1.1).
//
// color_bars is combinational. For every x in 0..799 with i_de low, and
// every x in 0..639 with i_de high, it checks the 3-bit-per-channel color:
// the eight-bar pattern (80 pixels per bar) when i_de is high, and black
// whenever i_de is low.
//
// Run: iverilog -g2012 -Wall -Wno-timescale -y rtl/common -o build/tb.vvp sim/tb_color_bars.v
//      vvp -n build/tb.vvp          (add +vcd for waveforms)
`timescale 1ns/1ps
`default_nettype none

module tb_color_bars;
    reg  [9:0] x  = 10'h3FF;  // not 0, so the first x=0 is a change
    reg        de = 0;
    wire [2:0] r, g, b;

    color_bars u_dut (.i_x(x), .i_de(de), .o_red(r), .o_grn(g), .o_blu(b));

    // Bar order, left to right: white, yellow, cyan, green,
    // magenta, red, blue, black. Each entry is {R,G,B}, 1 = full on.
    function automatic [2:0] bar_rgb(input integer bar);
        case (bar)
            0: bar_rgb = 3'b111;  // white
            1: bar_rgb = 3'b110;  // yellow
            2: bar_rgb = 3'b011;  // cyan
            3: bar_rgb = 3'b010;  // green
            4: bar_rgb = 3'b101;  // magenta
            5: bar_rgb = 3'b100;  // red
            6: bar_rgb = 3'b001;  // blue
            default: bar_rgb = 3'b000;  // black
        endcase
    endfunction

    integer errors = 0;
    integer i, d;
    reg [2:0] c;
    reg [8:0] exp_rgb;

    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("build/tb_color_bars.vcd");
            $dumpvars(0, tb_color_bars);
        end
    end

    initial begin : watchdog
        #1000000;
        $display("FAIL: tb_color_bars watchdog timeout (simulation hung)");
        $finish;
    end

    initial begin
        for (d = 0; d < 2; d = d + 1) begin
            for (i = 0; i < 800; i = i + 1) begin
                if (d == 0 || i < 640) begin  // de is never high past x=639
                x = i; de = d; #10;
                c = d ? bar_rgb(i / 80) : 3'b000;
                exp_rgb = {{3{c[2]}}, {3{c[1]}}, {3{c[0]}}};
                if ({r, g, b} !== exp_rgb) begin
                    if (errors < 5)
                        $display("  x=%0d de=%0d: got R=%b G=%b B=%b, expected R=%b G=%b B=%b",
                                 i, d, r, g, b, exp_rgb[8:6], exp_rgb[5:3], exp_rgb[2:0]);
                    errors = errors + 1;
                end
                end
            end
        end
        if (errors == 0)
            $display("PASS: tb_color_bars (1440 x/de combinations)");
        else
            $display("FAIL: tb_color_bars (%0d mismatches)", errors);
        $finish;
    end
endmodule
`default_nettype wire
