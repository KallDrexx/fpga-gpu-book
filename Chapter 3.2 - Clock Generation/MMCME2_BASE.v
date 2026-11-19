// MMCME2_BASE -- behavioral simulation model for Icarus Verilog (sim only).
//
// Supplied by the book (Chapter 3.2). Copy to sim/MMCME2_BASE.v. Do NOT add
// it to a project's SRCS: yosys knows the real primitive, and this file is
// only for testbenches (iverilog finds it through -y sim).
//
// What it models, for the 7-series -1 speed grade:
//   * Measures the period of CLKIN1, then makes every output from one time
//     base, so outputs are phase-aligned to each other:
//         f_VCO   = f_CLKIN1 * CLKFBOUT_MULT_F / DIVCLK_DIVIDE
//         f_OUTn  = f_VCO / CLKOUTn_DIVIDE        (CLKOUT0_DIVIDE_F for 0)
//         f_FBOUT = f_VCO / CLKFBOUT_MULT_F
//     CLKOUTn_PHASE and CLKOUTn_DUTY_CYCLE are honored; CLKOUTnB = ~CLKOUTn.
//   * Outputs start toggling RUN_CYCLES input clocks after RST and PWRDWN
//     go low, at a fixed phase offset from CLKIN1: the outputs are aligned
//     to each other, not to the input. LOCKED rises 40 to 55 input clocks
//     later (the amount varies, as real lock time does), between output
//     edges. Clocks run before LOCKED rises, as they can on hardware: only
//     LOCKED says they are good.
//   * RST or PWRDWN high stops every output (low) and drops LOCKED at once.
//   * LOCKED never rises if a parameter is out of range for the device (VCO
//     600-1200 MHz, PFD 10-450 MHz, input 10-800 MHz, divider ranges;
//     CLKFBOUT_MULT_F at most 63, nextpnr-xilinx's limit) or if
//     CLKFBIN is not fed from CLKFBOUT. The reason is printed.
//
// Not modeled: jitter, frequency drift before lock, CLKOUT4_CASCADE, the
// exact phase between CLKIN1 and the outputs, loss of the input clock.

`timescale 1ns/1ps
`default_nettype none

module MMCME2_BASE #(
    parameter         BANDWIDTH          = "OPTIMIZED",
    parameter real    CLKFBOUT_MULT_F    = 5.000,
    parameter real    CLKFBOUT_PHASE     = 0.000,
    parameter real    CLKIN1_PERIOD      = 0.000,
    parameter real    CLKOUT0_DIVIDE_F   = 1.000,
    parameter real    CLKOUT0_DUTY_CYCLE = 0.500,
    parameter real    CLKOUT0_PHASE      = 0.000,
    parameter integer CLKOUT1_DIVIDE     = 1,
    parameter real    CLKOUT1_DUTY_CYCLE = 0.500,
    parameter real    CLKOUT1_PHASE      = 0.000,
    parameter integer CLKOUT2_DIVIDE     = 1,
    parameter real    CLKOUT2_DUTY_CYCLE = 0.500,
    parameter real    CLKOUT2_PHASE      = 0.000,
    parameter integer CLKOUT3_DIVIDE     = 1,
    parameter real    CLKOUT3_DUTY_CYCLE = 0.500,
    parameter real    CLKOUT3_PHASE      = 0.000,
    parameter         CLKOUT4_CASCADE    = "FALSE",
    parameter integer CLKOUT4_DIVIDE     = 1,
    parameter real    CLKOUT4_DUTY_CYCLE = 0.500,
    parameter real    CLKOUT4_PHASE      = 0.000,
    parameter integer CLKOUT5_DIVIDE     = 1,
    parameter real    CLKOUT5_DUTY_CYCLE = 0.500,
    parameter real    CLKOUT5_PHASE      = 0.000,
    parameter integer CLKOUT6_DIVIDE     = 1,
    parameter real    CLKOUT6_DUTY_CYCLE = 0.500,
    parameter real    CLKOUT6_PHASE      = 0.000,
    parameter integer DIVCLK_DIVIDE      = 1,
    parameter real    REF_JITTER1        = 0.010,
    parameter         STARTUP_WAIT       = "FALSE"
) (
    output wire CLKFBOUT,
    output wire CLKFBOUTB,
    output wire CLKOUT0,
    output wire CLKOUT0B,
    output wire CLKOUT1,
    output wire CLKOUT1B,
    output wire CLKOUT2,
    output wire CLKOUT2B,
    output wire CLKOUT3,
    output wire CLKOUT3B,
    output wire CLKOUT4,
    output wire CLKOUT5,
    output wire CLKOUT6,
    output wire LOCKED,
    input  wire CLKFBIN,
    input  wire CLKIN1,
    input  wire PWRDWN,
    input  wire RST
);
    // -1 speed grade limits (DS187 / DS181).
    localparam real VCO_MIN = 600.0,  VCO_MAX = 1200.0;
    localparam real PFD_MIN = 10.0,   PFD_MAX = 450.0;
    localparam real FIN_MIN = 10.0,   FIN_MAX = 800.0;
    localparam integer RUN_CYCLES = 8;     // input clocks before outputs start

    reg        r_run    = 1'b0;            // outputs toggling
    reg        r_locked = 1'b0;
    reg [63:0] r_tin_ps = 64'd8000;        // measured CLKIN1 period, ps
    reg [31:0] r_lfsr   = 32'h1234_5678;   // varies the lock time
    integer    r_fb_edges = 0;
    real       r_fb_last  = -1.0;
    real       r_fb_per   = 0.0;

    assign LOCKED = r_locked;

    // ------------------------------------------------ parameter checks
    function integer is_eighth(input real v);  // a multiple of 0.125?
        real s;
        begin
            s = v * 8.0;
            is_eighth = (s - $floor(s + 0.5) < 1e-6 && $floor(s + 0.5) - s < 1e-6);
        end
    endfunction

    function integer params_ok(input real tin_ns);
        real fin, pfd, vco;
        begin
            fin = 1000.0 / tin_ns;
            pfd = fin / DIVCLK_DIVIDE;
            vco = pfd * CLKFBOUT_MULT_F;
            params_ok = 1;
            if (fin < FIN_MIN || fin > FIN_MAX) begin
                $display("MMCME2_BASE ERROR (%m): CLKIN1 is %0.3f MHz, outside %0.0f-%0.0f MHz",
                         fin, FIN_MIN, FIN_MAX);
                params_ok = 0;
            end
            if (DIVCLK_DIVIDE < 1 || DIVCLK_DIVIDE > 106) begin
                $display("MMCME2_BASE ERROR (%m): DIVCLK_DIVIDE = %0d, must be 1-106", DIVCLK_DIVIDE);
                params_ok = 0;
            end
            // The data sheet allows 64.000; openXC7's nextpnr stops above 63.
            if (CLKFBOUT_MULT_F < 2.0 || CLKFBOUT_MULT_F > 63.0 || !is_eighth(CLKFBOUT_MULT_F)) begin
                $display("MMCME2_BASE ERROR (%m): CLKFBOUT_MULT_F = %0.3f, must be 2.000-63.000 in steps of 0.125",
                         CLKFBOUT_MULT_F);
                params_ok = 0;
            end
            if (pfd < PFD_MIN || pfd > PFD_MAX) begin
                $display("MMCME2_BASE ERROR (%m): f_CLKIN1/DIVCLK_DIVIDE = %0.3f MHz, outside %0.0f-%0.0f MHz",
                         pfd, PFD_MIN, PFD_MAX);
                params_ok = 0;
            end
            if (vco < VCO_MIN || vco > VCO_MAX) begin
                $display("MMCME2_BASE ERROR (%m): VCO = %0.3f MHz, outside %0.0f-%0.0f MHz (-1 speed grade)",
                         vco, VCO_MIN, VCO_MAX);
                params_ok = 0;
            end
            if (CLKOUT0_DIVIDE_F < 1.0 || CLKOUT0_DIVIDE_F > 128.0 || !is_eighth(CLKOUT0_DIVIDE_F) ||
                (CLKOUT0_DIVIDE_F < 2.0 && CLKOUT0_DIVIDE_F != 1.0)) begin
                $display("MMCME2_BASE ERROR (%m): CLKOUT0_DIVIDE_F = %0.3f, must be 1, or 2.000-128.000 in steps of 0.125",
                         CLKOUT0_DIVIDE_F);
                params_ok = 0;
            end
            if (CLKOUT1_DIVIDE < 1 || CLKOUT1_DIVIDE > 128 || CLKOUT2_DIVIDE < 1 || CLKOUT2_DIVIDE > 128 ||
                CLKOUT3_DIVIDE < 1 || CLKOUT3_DIVIDE > 128 || CLKOUT4_DIVIDE < 1 || CLKOUT4_DIVIDE > 128 ||
                CLKOUT5_DIVIDE < 1 || CLKOUT5_DIVIDE > 128 || CLKOUT6_DIVIDE < 1 || CLKOUT6_DIVIDE > 128) begin
                $display("MMCME2_BASE ERROR (%m): CLKOUT1..6_DIVIDE must each be 1-128");
                params_ok = 0;
            end
            if (CLKIN1_PERIOD == 0.0)
                $display("MMCME2_BASE warning (%m): CLKIN1_PERIOD is not set (use the input period in ns)");
            else if (CLKIN1_PERIOD > tin_ns * 1.01 || CLKIN1_PERIOD < tin_ns * 0.99)
                $display("MMCME2_BASE warning (%m): CLKIN1_PERIOD = %0.3f ns, but CLKIN1 measures %0.3f ns",
                         CLKIN1_PERIOD, tin_ns);
        end
    endfunction

    // ------------------------------------------------ feedback monitor
    always @(posedge CLKFBIN) begin
        if (r_run) begin
            if (r_fb_last >= 0.0) r_fb_per = $realtime - r_fb_last;
            r_fb_last  = $realtime;
            r_fb_edges = r_fb_edges + 1;
        end
    end

    // ------------------------------------------------ start-up sequence
    real    t_prev, t_now, tin_ns, fb_exp;
    integer lock_cycles;
    always begin : seq
        r_locked = 1'b0;
        r_run    = 1'b0;
        wait (RST === 1'b0 && PWRDWN === 1'b0);
        // Measure the input period over the next few rising edges.
        @(posedge CLKIN1);
        t_prev = $realtime;
        repeat (RUN_CYCLES - 1) @(posedge CLKIN1);
        t_now  = $realtime;
        tin_ns = (t_now - t_prev) / (RUN_CYCLES - 1);
        r_tin_ps = $rtoi(tin_ns * 1000.0 + 0.5);
        if (!params_ok(tin_ns)) begin
            $display("MMCME2_BASE ERROR (%m): will not lock");
            wait (RST !== 1'b0 || PWRDWN !== 1'b0);
        end else begin
            r_fb_edges = 0;
            r_fb_last  = -1.0;
            #(0.15 * tin_ns);                // outputs are not aligned to CLKIN1
            r_run      = 1'b1;
            r_lfsr     = {r_lfsr[30:0], r_lfsr[31] ^ r_lfsr[21] ^ r_lfsr[1] ^ r_lfsr[0]};
            lock_cycles = 40 + (r_lfsr % 16);
            repeat (lock_cycles) @(posedge CLKIN1);
            #(0.3 * tin_ns);                 // LOCKED lands between output edges
            fb_exp = tin_ns * DIVCLK_DIVIDE;
            if (r_fb_edges < 4) begin
                $display("MMCME2_BASE ERROR (%m): CLKFBIN is not toggling; connect CLKFBOUT to CLKFBIN (directly or through a BUFG). Will not lock.");
                wait (RST !== 1'b0 || PWRDWN !== 1'b0);
            end else if (r_fb_per > fb_exp * 1.001 || r_fb_per < fb_exp * 0.999) begin
                $display("MMCME2_BASE ERROR (%m): CLKFBIN period is %0.3f ns, expected %0.3f ns; feed CLKFBOUT back to CLKFBIN. Will not lock.",
                         r_fb_per, fb_exp);
                wait (RST !== 1'b0 || PWRDWN !== 1'b0);
            end else begin
                r_locked = 1'b1;
                wait (RST !== 1'b0 || PWRDWN !== 1'b0);
            end
        end
    end

    // RST or PWRDWN aborts the sequence at once; it then restarts from the top.
    always @(posedge RST or posedge PWRDWN) disable seq;

    // ------------------------------------------------ outputs
    MMCME2_BASE_clkout #(.DIV(CLKFBOUT_MULT_F), .DUTY(0.5), .PHASE(CLKFBOUT_PHASE),
                         .MULT(CLKFBOUT_MULT_F), .DIVCLK(DIVCLK_DIVIDE))
        u_fb (.i_run(r_run), .i_tin_ps(r_tin_ps), .o_clk(CLKFBOUT));
    MMCME2_BASE_clkout #(.DIV(CLKOUT0_DIVIDE_F), .DUTY(CLKOUT0_DUTY_CYCLE), .PHASE(CLKOUT0_PHASE),
                         .MULT(CLKFBOUT_MULT_F), .DIVCLK(DIVCLK_DIVIDE))
        u_o0 (.i_run(r_run), .i_tin_ps(r_tin_ps), .o_clk(CLKOUT0));
    MMCME2_BASE_clkout #(.DIV(CLKOUT1_DIVIDE), .DUTY(CLKOUT1_DUTY_CYCLE), .PHASE(CLKOUT1_PHASE),
                         .MULT(CLKFBOUT_MULT_F), .DIVCLK(DIVCLK_DIVIDE))
        u_o1 (.i_run(r_run), .i_tin_ps(r_tin_ps), .o_clk(CLKOUT1));
    MMCME2_BASE_clkout #(.DIV(CLKOUT2_DIVIDE), .DUTY(CLKOUT2_DUTY_CYCLE), .PHASE(CLKOUT2_PHASE),
                         .MULT(CLKFBOUT_MULT_F), .DIVCLK(DIVCLK_DIVIDE))
        u_o2 (.i_run(r_run), .i_tin_ps(r_tin_ps), .o_clk(CLKOUT2));
    MMCME2_BASE_clkout #(.DIV(CLKOUT3_DIVIDE), .DUTY(CLKOUT3_DUTY_CYCLE), .PHASE(CLKOUT3_PHASE),
                         .MULT(CLKFBOUT_MULT_F), .DIVCLK(DIVCLK_DIVIDE))
        u_o3 (.i_run(r_run), .i_tin_ps(r_tin_ps), .o_clk(CLKOUT3));
    MMCME2_BASE_clkout #(.DIV(CLKOUT4_DIVIDE), .DUTY(CLKOUT4_DUTY_CYCLE), .PHASE(CLKOUT4_PHASE),
                         .MULT(CLKFBOUT_MULT_F), .DIVCLK(DIVCLK_DIVIDE))
        u_o4 (.i_run(r_run), .i_tin_ps(r_tin_ps), .o_clk(CLKOUT4));
    MMCME2_BASE_clkout #(.DIV(CLKOUT5_DIVIDE), .DUTY(CLKOUT5_DUTY_CYCLE), .PHASE(CLKOUT5_PHASE),
                         .MULT(CLKFBOUT_MULT_F), .DIVCLK(DIVCLK_DIVIDE))
        u_o5 (.i_run(r_run), .i_tin_ps(r_tin_ps), .o_clk(CLKOUT5));
    MMCME2_BASE_clkout #(.DIV(CLKOUT6_DIVIDE), .DUTY(CLKOUT6_DUTY_CYCLE), .PHASE(CLKOUT6_PHASE),
                         .MULT(CLKFBOUT_MULT_F), .DIVCLK(DIVCLK_DIVIDE))
        u_o6 (.i_run(r_run), .i_tin_ps(r_tin_ps), .o_clk(CLKOUT6));

    assign CLKFBOUTB = r_run & ~CLKFBOUT;
    assign CLKOUT0B  = r_run & ~CLKOUT0;
    assign CLKOUT1B  = r_run & ~CLKOUT1;
    assign CLKOUT2B  = r_run & ~CLKOUT2;
    assign CLKOUT3B  = r_run & ~CLKOUT3;
endmodule

// One output divider. All edges are scheduled from the time i_run rose, so
// outputs never drift apart however long the simulation runs.
module MMCME2_BASE_clkout #(
    parameter real    DIV    = 1.0,
    parameter real    DUTY   = 0.5,
    parameter real    PHASE  = 0.0,
    parameter real    MULT   = 5.0,
    parameter integer DIVCLK = 1
) (
    input  wire        i_run,
    input  wire [63:0] i_tin_ps,
    output reg         o_clk = 1'b0
);
    real    t0, per, off, t_rise;
    integer k;

    always begin : gen
        o_clk = 1'b0;
        wait (i_run === 1'b1);
        t0  = $realtime;
        per = (i_tin_ps / 1000.0) * DIVCLK * DIV / MULT;
        off = per * PHASE / 360.0;
        while (off < 0.0)  off = off + per;
        while (off >= per) off = off - per;
        for (k = 0; i_run === 1'b1; k = k + 1) begin
            t_rise = t0 + off + k * per;
            if (t_rise > $realtime) #(t_rise - $realtime);
            o_clk = 1'b1;
            #(t_rise + DUTY * per - $realtime);
            o_clk = 1'b0;
        end
    end

    always @(negedge i_run) disable gen;
endmodule

`default_nettype wire
