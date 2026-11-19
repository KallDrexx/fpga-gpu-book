// tb_lfsr.v -- self-checking testbench for lfsr (Chapter 2.3).
//
// Two 16-bit LFSRs with different seeds. For each one the testbench checks
// that
//   * the first value is SEED, and the value holds while i_en is low,
//   * with i_en high it steps once per clock and never shows 0,
//   * it visits all 65,535 non-zero values exactly once before returning to
//     SEED (a maximal-length sequence: any maximal tap set passes).
//
// Run: iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/tb.vvp sim/tb_lfsr.v
//      vvp -n build/tb.vvp           (add +vcd for waveforms)
`timescale 1ns/1ps
`default_nettype none

module tb_lfsr;
    localparam [15:0] SEED_A = 16'h0001, SEED_B = 16'hBEEF;

    reg clk = 0;
    always #20 clk = ~clk;

    reg en_a = 0, en_b = 0;
    wire [15:0] va, vb;
    lfsr #(.SEED(SEED_A)) u_a (.i_clk(clk), .i_en(en_a), .o_value(va));
    lfsr #(.SEED(SEED_B)) u_b (.i_clk(clk), .i_en(en_b), .o_value(vb));

    reg seen [0:65535];
    integer errors = 0;

    // Run one LFSR through its whole period. which = 0 for A, 1 for B.
    task check_period(input integer which, input [15:0] seed);
        integer n, j;
        reg [15:0] v, first;
        begin
            for (j = 0; j < 65536; j = j + 1) seen[j] = 1'b0;
            @(negedge clk);
            v = which ? vb : va;
            if (v !== seed) begin
                $display("  LFSR %0d: first value %h, expected SEED %h", which, v, seed);
                errors = errors + 1;
            end
            // Hold: i_en low for a few clocks must not change the value.
            repeat (5) @(negedge clk);
            if ((which ? vb : va) !== v) begin
                $display("  LFSR %0d: value changed while i_en was low", which);
                errors = errors + 1;
            end
            first = v;
            if (which) en_b = 1; else en_a = 1;
            n = 0;
            while (errors == 0) begin
                if (v === 16'd0 || ^v === 1'bx) begin
                    $display("  LFSR %0d: value %h after %0d steps (0 or X: the register is stuck)", which, v, n);
                    errors = errors + 1;
                end else if (seen[v]) begin
                    if (v !== first)
                        $display("  LFSR %0d: value %h repeats after %0d steps without returning to SEED", which, v, n);
                    else if (n != 65535)
                        $display("  LFSR %0d: period is %0d, expected 65535 (taps are not maximal-length)", which, n);
                    if (v !== first || n != 65535) errors = errors + 1;
                    disable check_period;
                end else begin
                    seen[v] = 1'b1;
                end
                @(negedge clk);
                v = which ? vb : va;
                n = n + 1;
            end
        end
    endtask

    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("build/tb_lfsr.vcd");
            $dumpvars(0, tb_lfsr);
        end
    end

    initial begin : watchdog
        #(40 * 300000);
        $display("FAIL: tb_lfsr watchdog timeout");
        $finish;
    end

    initial begin
        check_period(0, SEED_A);
        en_a = 0;
        if (errors == 0) check_period(1, SEED_B);
        if (errors == 0)
            $display("PASS: tb_lfsr (both seeds visit all 65535 non-zero values, period 65535)");
        else
            $display("FAIL: tb_lfsr (%0d errors, see above)", errors);
        $finish;
    end
endmodule
`default_nettype wire
