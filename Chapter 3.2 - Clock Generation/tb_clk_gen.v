// tb_clk_gen -- self-checking testbench for Step 3.2 (clock generation)
//
// Designs under test (see "Testbench requirements" in Chapter 3.2):
//   reset_sync   rtl/common/reset_sync.v   (found by -y rtl/common)
//   clk_gen      rtl/xc7/clk_gen.v         (tb_clk_gen_SRCS in mk/sim.mk)
// Simulation models supplied by the book, found by -y sim:
//   MMCME2_BASE  sim/MMCME2_BASE.v         BUFG  sim/BUFG.v
//
// Part 1, reset_sync on a clock the testbench can start and stop:
//   * o_rst is 1 at power-up, before any clock edge,
//   * o_rst rises within 0.5 ns of i_rst rising, even with the clock
//     stopped (asynchronous assert),
//   * o_rst falls only at a rising clock edge, on the 2nd to 4th edge after
//     i_rst falls (synchronous deassert through at least two flip-flops),
//     and never while i_rst is high. Pulses of 1 to 40 ns at random phases.
//
// Part 2, clk_gen fed a 125 MHz clock, with i_rst pulsed five times:
//   * each domain reset is 1 at power-up and within 1 ns of o_locked
//     falling or i_rst rising, and is never released while o_locked is low
//     or i_rst is high (clocks run before LOCKED, as on hardware),
//   * each domain reset falls at a rising edge of its OWN clock, on the 2nd
//     to 4th edge after o_locked rises,
//   * clk_pix = 40 ns, clk_ser = 8 ns, clk_sys = 20 ns periods (within
//     10 ps, 45-55% duty) while locked, and every clk_pix rising edge has a
//     clk_ser rising edge at the same instant (phase-aligned),
//   * o_locked rises after power-up and after every i_rst pulse, falls
//     during each pulse (i_rst resets the MMCM), and never falls otherwise.
//
// MMCM parameters are not checked directly: any valid values that give
// these frequencies pass. The MMCME2_BASE model refuses to lock (and says
// why) if a parameter is outside the -1 speed grade's range.
//
// Ends with one PASS or FAIL line. Run with +vcd to write tb_clk_gen.vcd.

`timescale 1ns/1ps
`default_nettype none

module tb_clk_gen;
    integer errors = 0;
    integer seed   = 32'h3202;

    task err(input [8*100-1:0] msg);
        begin
            if (errors < 12)
                $display("ERROR at %0.3f ns: %0s", $realtime, msg);
            errors = errors + 1;
        end
    endtask

    // =============================================================== Part 1
    reg  rs_clk = 1'b0, rs_run = 1'b0, rs_in = 1'b0, rs_chk = 1'b0;
    wire rs_out;

    reset_sync u_rs (
        .i_clk (rs_clk),
        .i_rst (rs_in),
        .o_rst (rs_out)
    );

    // 100 MHz clock that can be stopped (it stops low).
    always begin
        if (rs_run) begin #5 rs_clk = 1'b1; #5 rs_clk = 1'b0; end
        else        @(rs_run);
    end

    integer rs_edges = 0, rs_lat_min = 99, rs_lat_max = 0, rs_rel = 0;
    real    rs_t_edge = -1.0e9;

    always @(posedge rs_clk) begin
        rs_edges  = rs_edges + 1;
        rs_t_edge = $realtime;
    end
    always @(negedge rs_in) rs_edges = 0;

    always @(negedge rs_out) if (rs_chk) begin
        if (rs_in !== 1'b0)
            err("reset_sync: o_rst fell while i_rst is high");
        else if ($realtime - rs_t_edge > 0.5)
            err("reset_sync: o_rst fell between clock edges (deassert must be synchronous)");
        else if (rs_edges < 2)
            err("reset_sync: o_rst fell on the 1st clock edge after i_rst fell (need two flip-flops)");
        else if (rs_edges > 4)
            err("reset_sync: o_rst fell more than 4 clock edges after i_rst fell");
        else begin
            if (rs_edges < rs_lat_min) rs_lat_min = rs_edges;
            if (rs_edges > rs_lat_max) rs_lat_max = rs_edges;
            rs_rel = rs_rel + 1;
        end
    end

    always @(posedge rs_out) if (rs_chk && rs_in !== 1'b1)
        err("reset_sync: o_rst rose while i_rst is low");

    always @(posedge rs_in) if (rs_chk) begin
        #0.5;
        if (rs_out !== 1'b1)
            err("reset_sync: o_rst did not follow i_rst high at once (assert must be asynchronous)");
    end

    always @(rs_out) if (rs_chk && rs_out !== 1'b0 && rs_out !== 1'b1)
        err("reset_sync: o_rst is X or Z");

    task rs_expect_released;
        begin
            repeat (5) @(posedge rs_clk);
            #1;
            if (rs_out !== 1'b0) err("reset_sync: o_rst still high 5 clock edges after i_rst fell");
        end
    endtask

    integer i;
    real    rs_off, rs_hold;
    task run_part1;
        begin
            #1;
            if (rs_out !== 1'b1) err("reset_sync: o_rst must be 1 at power-up (initial value)");
            rs_chk = 1'b1;
            #50;
            if (rs_out !== 1'b1) err("reset_sync: o_rst fell with no clock running");

            // Clock starts: leave power-up reset.
            rs_edges = 0;
            rs_run   = 1'b1;
            rs_expect_released;

            // Assert with the clock stopped: only an asynchronous reset works.
            @(posedge rs_clk); #1 rs_run = 1'b0;
            #20;
            rs_in = 1'b1; #2.25 rs_in = 1'b0;
            #30;
            if (rs_out !== 1'b1) err("reset_sync: o_rst did not stay high with the clock stopped");
            rs_run = 1'b1;
            rs_expect_released;

            // Random pulses at random phases, 1 to 40 ns long.
            for (i = 0; i < 40; i = i + 1) begin
                @(posedge rs_clk);
                rs_off  = 0.25 + 0.5 * ({$random(seed)} % 19);   // 0.25..9.25 ns
                rs_hold = 1 + ({$random(seed)} % 40);            // 1..40 ns
                #(rs_off) rs_in = 1'b1;
                #(rs_hold) rs_in = 1'b0;
                rs_expect_released;
            end
            rs_chk = 1'b0;
            if (rs_rel != 42)
                err("reset_sync: not every reset was released correctly");
        end
    endtask

    // =============================================================== Part 2
    reg  clk = 1'b0, clk_run = 1'b0, g_rst = 1'b0, chk = 1'b0;
    wire clk_pix, clk_ser, clk_sys, locked, rst_pix, rst_ser, rst_sys;

    always begin                                    // 125 MHz board clock
        if (clk_run) begin #4 clk = 1'b1; #4 clk = 1'b0; end
        else         @(clk_run);
    end

    clk_gen dut (
        .i_clk     (clk),
        .i_rst     (g_rst),
        .o_clk_pix (clk_pix),
        .o_clk_ser (clk_ser),
        .o_clk_sys (clk_sys),
        .o_locked  (locked),
        .o_rst_pix (rst_pix),
        .o_rst_ser (rst_ser),
        .o_rst_sys (rst_sys)
    );

    // A domain must be in reset whenever the MMCM is unlocked or i_rst is high.
    wire must_rst = (locked !== 1'b1) || g_rst;

    tb_clk_gen_domain #(.NAME("pix"), .PERIOD(40.0)) c_pix (
        .i_clk(clk_pix), .i_rst(rst_pix), .i_must(must_rst), .i_chk(chk));
    tb_clk_gen_domain #(.NAME("ser"), .PERIOD(8.0))  c_ser (
        .i_clk(clk_ser), .i_rst(rst_ser), .i_must(must_rst), .i_chk(chk));
    tb_clk_gen_domain #(.NAME("sys"), .PERIOD(20.0)) c_sys (
        .i_clk(clk_sys), .i_rst(rst_sys), .i_must(must_rst), .i_chk(chk));

    // Phase alignment: clk_ser rises at every rising edge of clk_pix.
    real    t_ser_rise = -1.0e9, t_pix_rise;
    integer n_align = 0;
    always @(posedge clk_ser) t_ser_rise = $realtime;
    always @(posedge clk_pix) if (chk && !must_rst) begin
        t_pix_rise = $realtime;
        #0.01;
        if (t_pix_rise - t_ser_rise > 0.02 || t_ser_rise - t_pix_rise > 0.02)
            err("clk_ser has no rising edge at a clk_pix rising edge (not phase-aligned)");
        else
            n_align = n_align + 1;
    end

    // LOCKED behavior around i_rst pulses.
    reg     in_pulse = 1'b0, saw_unlock = 1'b0;
    integer n_locks = 0;
    always @(negedge locked) if (chk) begin
        if (in_pulse) saw_unlock = 1'b1;
        else          err("o_locked fell although i_rst was not pulsed");
    end
    always @(posedge locked) if (chk) n_locks = n_locks + 1;
    always @(locked) if (chk && locked !== 1'b0 && locked !== 1'b1)
        err("o_locked is X or Z");

    task wait_lock(output ok);
        integer t;
        begin
            t = 0;
            while (locked !== 1'b1 && t < 20000) begin #10; t = t + 10; end
            ok = (locked === 1'b1);
            if (!ok) err("o_locked never rose (see any MMCME2_BASE messages above)");
        end
    endtask

    task expect_running;
        begin
            #1000;
            if (rst_pix !== 1'b0) err("rst_pix still asserted 1 us after lock");
            if (rst_ser !== 1'b0) err("rst_ser still asserted 1 us after lock");
            if (rst_sys !== 1'b0) err("rst_sys still asserted 1 us after lock");
        end
    endtask

    reg  ok;
    real hold;
    task run_part2;
        begin
            #1;
            if (rst_pix !== 1'b1 || rst_ser !== 1'b1 || rst_sys !== 1'b1)
                err("clk_gen: domain resets must be 1 at power-up, before LOCKED");
            if (locked !== 1'b0) err("clk_gen: o_locked must be 0 before the MMCM has a clock");
            chk     = 1'b1;
            clk_run = 1'b1;
            wait_lock(ok);
            if (ok) begin
                expect_running;
                #7000;                                // measure periods
                for (i = 0; i < 5 && ok; i = i + 1) begin
                    #(100 + ({$random(seed)} % 300) + 0.37);
                    hold       = (i == 0) ? 200.0 : 40 + ({$random(seed)} % 60);
                    in_pulse   = 1'b1;
                    saw_unlock = 1'b0;
                    g_rst      = 1'b1;
                    #(hold) g_rst = 1'b0;
                    #50 in_pulse = 1'b0;
                    if (!saw_unlock)
                        err("o_locked did not fall during an i_rst pulse (i_rst must reset the MMCM)");
                    wait_lock(ok);
                    if (ok) begin
                        expect_running;
                        #2000;
                    end
                end
            end
            chk = 1'b0;
        end
    endtask

    // =============================================================== main
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_clk_gen.vcd");
            $dumpvars(0, tb_clk_gen);
        end

        run_part1;
        run_part2;

        errors = errors + c_pix.errors + c_ser.errors + c_sys.errors;
        if (errors == 0) begin
            if (n_locks != 6)                      err("expected 6 lock events");
            if (c_pix.n_per < 100 || c_ser.n_per < 500 || c_sys.n_per < 200)
                err("too few clock periods measured (clocks not running?)");
            if (c_pix.n_rel != 6 || c_ser.n_rel != 6 || c_sys.n_rel != 6)
                err("each domain reset should be released once per lock");
            if (n_align < 100)                     err("too few clk_pix edges checked for alignment");
        end

        if (errors == 0)
            $display("PASS tb_clk_gen: reset_sync releases after %0d-%0d edges; pix/ser/sys = 40/8/20 ns, ser aligned to pix; 6 locks, domain resets released after %0d-%0d/%0d-%0d/%0d-%0d edges",
                     rs_lat_min, rs_lat_max,
                     c_pix.lat_min, c_pix.lat_max, c_ser.lat_min, c_ser.lat_max,
                     c_sys.lat_min, c_sys.lat_max);
        else
            $display("FAIL tb_clk_gen: %0d error(s)", errors);
        $finish;
    end

    // Watchdog: a hung design must still produce a FAIL line.
    initial begin
        #300_000;
        $display("FAIL tb_clk_gen: timeout");
        $finish;
    end
endmodule

// Checks one clock domain of clk_gen: its clock period and its reset.
module tb_clk_gen_domain #(
    parameter      NAME   = "pix",
    parameter real PERIOD = 40.0
) (
    input wire i_clk,
    input wire i_rst,
    input wire i_must,     // 1 while the domain must be held in reset
    input wire i_chk
);
    integer errors = 0, edges = 0, n_per = 0, n_rel = 0;
    integer lat_min = 99, lat_max = 0;
    real    t_rise = -1.0e9, t_fall = -1.0e9, t_steady = 1.0e9, per, duty;
    reg     overdue = 1'b0;

    task err(input [8*100-1:0] msg);
        begin
            if (errors < 6)
                $display("ERROR at %0.3f ns: clk_%0s/rst_%0s: %0s", $realtime, NAME, NAME, msg);
            errors = errors + 1;
        end
    endtask

    always @(negedge i_clk) t_fall = $realtime;

    always @(posedge i_clk) begin
        if (i_chk && !i_must && t_rise >= t_steady) begin
            per = $realtime - t_rise;
            if (per > PERIOD + 0.01 || per < PERIOD - 0.01) begin
                if (errors < 6)
                    $display("ERROR at %0.3f ns: clk_%0s period is %0.3f ns, expected %0.3f ns",
                             $realtime, NAME, per, PERIOD);
                errors = errors + 1;
            end else if (t_fall > t_rise) begin
                duty = (t_fall - t_rise) / per;
                if (duty < 0.45 || duty > 0.55) err("duty cycle is not 50%");
                else n_per = n_per + 1;
            end
        end
        t_rise = $realtime;
        edges  = edges + 1;
        if (i_chk && !i_must && i_rst === 1'b1 && edges > 4 && !overdue) begin
            err("reset still asserted 4 clock edges after LOCKED rose");
            overdue = 1'b1;
        end
    end

    // The domain may leave reset from here on: count edges from now.
    always @(negedge i_must) begin
        edges    = 0;
        overdue  = 1'b0;
        t_steady = $realtime;
    end
    always @(posedge i_must) t_steady = 1.0e9;

    always @(posedge i_must) if (i_chk) begin
        #1;
        if (i_rst !== 1'b1)
            err("reset not asserted within 1 ns of LOCKED falling or i_rst rising");
    end

    always @(negedge i_rst) if (i_chk) begin
        if (i_must)
            err("reset released while LOCKED is low or i_rst is high");
        else if ($realtime - t_rise > 0.5)
            err("reset released between edges of its own clock (use a reset_sync per domain)");
        else if (edges < 2)
            err("reset released on the 1st edge after LOCKED rose (need two flip-flops)");
        else if (edges > 4)
            err("reset released more than 4 edges after LOCKED rose");
        else begin
            if (edges < lat_min) lat_min = edges;
            if (edges > lat_max) lat_max = edges;
            n_rel = n_rel + 1;
        end
    end

    always @(posedge i_rst) if (i_chk) begin
        #0.01;
        if (!i_must) err("reset rose while LOCKED is high and i_rst is low");
    end

    always @(i_rst) if (i_chk && i_rst !== 1'b0 && i_rst !== 1'b1) err("reset is X or Z");
    always @(i_clk) if (i_chk && i_clk !== 1'b0 && i_clk !== 1'b1) err("clock is X or Z");
endmodule

`default_nettype wire
