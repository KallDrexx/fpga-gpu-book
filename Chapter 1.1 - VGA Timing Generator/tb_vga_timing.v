// tb_vga_timing.v -- self-checking testbench for vga_timing (Chapter 1.1).
//
// Checks the default 640x480@60 timing over two full frames:
//   * x counts 0..799 and wraps; y advances once per line and wraps at 525
//   * o_de, o_hsync, o_vsync agree with o_x/o_y in the same clock cycle
//   * hsync period 800 clocks, low for 96
//   * vsync period 420,000 clocks, low for 2 lines (1,600 clocks)
//   * exactly 307,200 o_de cycles per frame
// Then runs a second instance with small, odd parameters to catch
// hard-coded numbers.
//
// Run: iverilog -g2012 -Wall -Wno-timescale -y rtl/common -o build/tb.vvp sim/tb_vga_timing.v
//      vvp -n build/tb.vvp          (add +vcd for waveforms; the dump is large)
`timescale 1ns/1ps
`default_nettype none

module tb_vga_timing;
    // ---------------- expected 640x480 timing ----------------
    localparam H_VIS = 640, H_FP = 16, H_SW = 96, H_BP = 48;
    localparam V_VIS = 480, V_FP = 10, V_SW = 2,  V_BP = 33;
    localparam H_TOT = H_VIS + H_FP + H_SW + H_BP;   // 800
    localparam V_TOT = V_VIS + V_FP + V_SW + V_BP;   // 525

    // ---------------- small mode for the parameter check ----------------
    localparam SH_VIS = 20, SH_FP = 3, SH_SW = 5, SH_BP = 4;  // 32
    localparam SV_VIS = 9,  SV_FP = 2, SV_SW = 3, SV_BP = 1;  // 15
    localparam SH_TOT = SH_VIS + SH_FP + SH_SW + SH_BP;
    localparam SV_TOT = SV_VIS + SV_FP + SV_SW + SV_BP;

    reg clk = 0;
    always #20 clk = ~clk;  // 25 MHz

    wire       hs,  vs,  de;  wire [9:0] x,  y;
    wire       shs, svs, sde; wire [9:0] sx, sy;

    vga_timing u_dut (
        .i_clk(clk), .o_hsync(hs), .o_vsync(vs), .o_de(de), .o_x(x), .o_y(y)
    );

    vga_timing #(
        .H_VISIBLE(SH_VIS), .H_FRONT(SH_FP), .H_SYNC(SH_SW), .H_BACK(SH_BP),
        .V_VISIBLE(SV_VIS), .V_FRONT(SV_FP), .V_SYNC(SV_SW), .V_BACK(SV_BP)
    ) u_small (
        .i_clk(clk), .o_hsync(shs), .o_vsync(svs), .o_de(sde), .o_x(sx), .o_y(sy)
    );

    integer errors = 0;
    integer cycle  = 0;

    integer serrors = 0;  // errors from the small-parameter instance

    task fail(input [8*96-1:0] msg);
        begin
            if (errors == 0) $display("  first error at cycle %0d: %0s", cycle, msg);
            errors = errors + 1;
        end
    endtask

    task sfail(input [8*96-1:0] msg);
        begin
            if (serrors == 0) $display("  first error at cycle %0d: small-parameter instance: %0s", cycle, msg);
            serrors = serrors + 1;
        end
    endtask

    // ---------------- per-cycle consistency (both instances) ----------------
    reg        have_prev = 0, shave_prev = 0;
    reg [9:0]  px, py, spx, spy;
    reg        exp_de, exp_hs, exp_vs;

    always @(posedge clk) begin
        cycle = cycle + 1;
    end

    // Sample just before each rising edge, when outputs are settled.
    always @(negedge clk) if (cycle > 2) begin
        if ((^{hs, vs, de, x, y}) === 1'bx)
            fail("X/Z on an output (did you give your registers initial values?)");
        else begin
            if (x >= H_TOT || y >= V_TOT) fail("x or y out of range");
            exp_de = (x < H_VIS) && (y < V_VIS);
            exp_hs = !((x >= H_VIS + H_FP) && (x < H_VIS + H_FP + H_SW));
            exp_vs = !((y >= V_VIS + V_FP) && (y < V_VIS + V_FP + V_SW));
            if (de !== exp_de) fail("o_de does not match x/y in the same cycle");
            if (hs !== exp_hs) fail("o_hsync does not match x in the same cycle (position or polarity)");
            if (vs !== exp_vs) fail("o_vsync does not match y in the same cycle (position or polarity)");
            if (have_prev) begin
                if (px == H_TOT - 1) begin
                    if (x !== 0) fail("x did not wrap to 0 after 799");
                    if (y !== ((py == V_TOT - 1) ? 0 : py + 1)) fail("y did not advance (or wrap) at end of line");
                end else begin
                    if (x !== px + 1) fail("x did not increment by 1");
                    if (y !== py)     fail("y changed in the middle of a line");
                end
            end
            px = x; py = y; have_prev = 1;
        end

        if ((^{shs, svs, sde, sx, sy}) === 1'bx)
            sfail("X/Z on an output");
        else begin
            if (sx >= SH_TOT || sy >= SV_TOT) sfail("x/y out of range (hard-coded totals?)");
            if (sde !== ((sx < SH_VIS) && (sy < SV_VIS)))
                sfail("o_de wrong (hard-coded 640/480?)");
            if (shs !== !((sx >= SH_VIS + SH_FP) && (sx < SH_VIS + SH_FP + SH_SW)))
                sfail("o_hsync wrong (hard-coded porch/sync?)");
            if (svs !== !((sy >= SV_VIS + SV_FP) && (sy < SV_VIS + SV_FP + SV_SW)))
                sfail("o_vsync wrong (hard-coded porch/sync?)");
            if (shave_prev) begin
                if (spx == SH_TOT - 1) begin
                    if (sx !== 0) sfail("x did not wrap");
                    if (sy !== ((spy == SV_TOT - 1) ? 0 : spy + 1)) sfail("y did not advance/wrap");
                end else if (sx !== spx + 1 || sy !== spy)
                    sfail("bad x/y sequence");
            end
            spx = sx; spy = sy; shave_prev = 1;
        end
    end

    // ---------------- sync measurements (main instance) ----------------
    integer h_fall_t = -1, h_periods = 0, h_bad_period = 0, h_bad_width = 0;
    integer v_fall_t = -1, v_periods = 0, v_bad_period = 0, v_bad_width = 0;
    integer de_count = 0, frames_counted = 0, de_bad = 0;
    integer last_de_frame = -1;
    reg     hs_q = 1, vs_q = 1;

    always @(negedge clk) if (cycle > 2 && (^{hs, vs, de}) !== 1'bx) begin
        // hsync: falling edge to falling edge = period; falling to rising = pulse
        if (hs_q && !hs) begin
            if (h_fall_t >= 0) begin
                h_periods = h_periods + 1;
                if (cycle - h_fall_t != H_TOT) begin
                    if (h_bad_period == 0) $display("  hsync period %0d clocks, expected %0d", cycle - h_fall_t, H_TOT);
                    h_bad_period = h_bad_period + 1;
                end
            end
            h_fall_t = cycle;
        end
        if (!hs_q && hs && h_fall_t >= 0 && cycle - h_fall_t != H_SW) begin
            if (h_bad_width == 0) $display("  hsync pulse %0d clocks, expected %0d", cycle - h_fall_t, H_SW);
            h_bad_width = h_bad_width + 1;
        end

        // vsync, with de counted between falling edges
        if (vs_q && !vs) begin
            if (v_fall_t >= 0) begin
                v_periods = v_periods + 1;
                if (cycle - v_fall_t != H_TOT * V_TOT) begin
                    if (v_bad_period == 0) $display("  vsync period %0d clocks, expected %0d", cycle - v_fall_t, H_TOT * V_TOT);
                    v_bad_period = v_bad_period + 1;
                end
                frames_counted = frames_counted + 1;
                if (de_count != H_VIS * V_VIS) begin
                    if (de_bad == 0) $display("  %0d de cycles in a frame, expected %0d", de_count, H_VIS * V_VIS);
                    de_bad = de_bad + 1;
                end
            end
            v_fall_t = cycle;
            de_count = 0;
        end
        if (!vs_q && vs && v_fall_t >= 0 && cycle - v_fall_t != V_SW * H_TOT) begin
            if (v_bad_width == 0) $display("  vsync pulse %0d clocks, expected %0d (2 lines)", cycle - v_fall_t, V_SW * H_TOT);
            v_bad_width = v_bad_width + 1;
        end
        if (de) de_count = de_count + 1;
        hs_q = hs; vs_q = vs;
    end

    // ---------------- run, watchdog, verdict ----------------
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("build/tb_vga_timing.vcd");
            $dumpvars(0, tb_vga_timing);
        end
    end

    initial begin : watchdog
        #(40 * 1500000);
        $display("FAIL: tb_vga_timing watchdog timeout (simulation hung)");
        $finish;
    end

    initial begin
        // Up to one frame to find the first vsync edge, then two measured frames.
        wait (v_periods == 2 || cycle > 3 * H_TOT * V_TOT + 10);
        @(negedge clk); @(negedge clk);
        if (v_periods < 2) begin
            $display("  saw %0d complete vsync periods in %0d clocks", v_periods, cycle);
            errors = errors + 1;
        end
        if (h_periods < 2 * V_TOT) begin
            $display("  saw only %0d hsync periods", h_periods);
            errors = errors + 1;
        end
        errors = errors + serrors + h_bad_period + h_bad_width + v_bad_period + v_bad_width + de_bad;
        if (errors == 0)
            $display("PASS: tb_vga_timing (%0d lines, %0d frames: hsync 800/96, vsync 420000/1600, 307200 de per frame)",
                     h_periods, frames_counted);
        else
            $display("FAIL: tb_vga_timing (%0d failed checks)", errors);
        $finish;
    end
endmodule
`default_nettype wire
