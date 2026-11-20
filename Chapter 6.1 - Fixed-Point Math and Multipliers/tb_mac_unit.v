// tb_mac_unit.v -- self-checking testbench for mac_unit (Chapter 6.1).
//
// Reads sim/mac_in.hex (beats) and sim/mac_exp.hex (one expected result per sum), both written by
// sim/py/ref_mac.py from the Python fixed-point model. About 100,000 sums: random, plus directed
// edge cases (extremes, -1, rounding ties, saturation at both ends, 16-product sums).
//
//   Phase 1  the first sum (one product) is applied alone; the testbench measures the latency L,
//            the number of clocks from the clock edge that takes i_valid to the clock edge after which
//            o_valid is first seen. Any fixed L from 1 to 40 is accepted.
//   Phase 2  every other beat is streamed, with the idle gaps recorded in the file (back to back
//            when the gap is 0). During idle clocks i_valid = 0 and the other inputs are random junk.
//            Each result must appear exactly L clocks after the beat that ended its sum, with
//            o_valid high for that clock only and o_data bit-for-bit equal to the model.
//
// Run from build/sim/:   vvp -n tb_mac_unit.vvp      (about 5 seconds; +dir=PATH/ for another hex folder)
// Prints one PASS or FAIL line at the end (plus the first few problems before it).
`timescale 1ns/1ps
`default_nettype none

module tb_mac_unit;
    localparam A_W = 16, B_W = 16, ACC_W = 48, FRAC = 14, OUT_W = 16;
    localparam N_BEAT = 322270, N_SUM = 100046;   // lines in the two files (ref_mac.py prints them)
    localparam MAX_LAT = 40;

    reg                      i_clk = 0;
    reg                      i_valid = 0, i_clear = 0, i_last = 0;
    reg  signed [A_W-1:0]    i_a = 0;
    reg  signed [B_W-1:0]    i_b = 0;
    wire                     o_valid;
    wire signed [OUT_W-1:0]  o_data;

    mac_unit #(.A_W(A_W), .B_W(B_W), .ACC_W(ACC_W), .FRAC(FRAC), .OUT_W(OUT_W)) dut (
        .i_clk(i_clk), .i_valid(i_valid), .i_clear(i_clear), .i_last(i_last),
        .i_a(i_a), .i_b(i_b), .o_valid(o_valid), .o_data(o_data));

    always #10 i_clk = ~i_clk;                      // 50 MHz; inputs driven and outputs sampled on the falling edge

    reg [39:0]  beat [0:N_BEAT-1];
    reg [15:0]  exp  [0:N_SUM-1];
    integer     q_time [0:N_SUM-1];               // clock number at which each sum's last beat was driven

    integer cyc = 0;                                // counts falling edges
    integer n_err, L, bi, gap_left, n_driven, n_seen, k;
    reg [1023:0] dir, fname;
    reg done_driving = 0;
    reg [39:0] w;
    reg got;

    task error(input [511:0] msg, input integer idx);
        begin
            n_err = n_err + 1;
            if (n_err <= 10) $display("  error at clock %0d, sum %0d: %0s", cyc, idx, msg);
        end
    endtask

    task junk;                                      // idle clock: valid low, everything else random
        begin
            i_valid = 0; i_clear = $random; i_last = $random;
            i_a = $random; i_b = $random;
        end
    endtask

    task drive(input [39:0] x);
        begin
            i_valid = 1; i_clear = x[36]; i_last = x[37];
            i_a = x[31:16]; i_b = x[15:0];
        end
    endtask

    // Watchdog: a hang must FAIL.
    initial begin
        #400_000_000;
        $display("FAIL: watchdog (testbench did not finish)");
        $finish;
    end

    initial begin
        n_err = 0;
        if (!$value$plusargs("dir=%s", dir)) dir = "../../sim/";
        $sformat(fname, "%0s%0s", dir, "mac_in.hex");  $readmemh(fname, beat);
        $sformat(fname, "%0s%0s", dir, "mac_exp.hex"); $readmemh(fname, exp);
        if (^beat[N_BEAT-1] === 1'bx || ^exp[N_SUM-1] === 1'bx) begin
            $display("FAIL: vectors missing or wrong length (run sim/py/ref_mac.py sim; check N_BEAT and N_SUM)");
            $finish;
        end

        // power-up: eight idle clocks with junk inputs, o_valid must stay low
        repeat (8) begin
            @(negedge i_clk); cyc = cyc + 1; junk;
            if (o_valid !== 1'b0) error("o_valid not low while idle at start", -1);
        end

        // phase 1: first sum alone, measure the latency
        @(negedge i_clk); cyc = cyc + 1;
        drive(beat[0]);
        L = -1;
        k = cyc;
        got = 0;
        while (!got && cyc - k < MAX_LAT) begin
            @(negedge i_clk); cyc = cyc + 1; junk;
            if (o_valid === 1'b1) begin
                got = 1; L = cyc - k;
                if (o_data !== exp[0]) begin
                    error("first sum wrong", 0);
                    $display("    expected %h got %h", exp[0], o_data);
                end
            end else if (o_valid !== 1'b0) error("o_valid is X", 0);
        end
        if (!got) begin
            $display("FAIL: no o_valid within %0d clocks of the first sum", MAX_LAT);
            $finish;
        end
        // let the pipeline drain: o_valid must fall again and stay low
        repeat (MAX_LAT) begin
            @(negedge i_clk); cyc = cyc + 1; junk;
            if (o_valid !== 1'b0) error("extra o_valid after the first sum", 0);
        end

        // phase 2: stream the rest
        bi = 1; n_driven = 1; n_seen = 1;           // sum 0 is done
        gap_left = (N_BEAT > 1) ? beat[1][35:32] : 0;
        q_time[0] = 0;
        while (n_seen < N_SUM && n_err < 10) begin
            @(negedge i_clk); cyc = cyc + 1;
            // check first: what the DUT shows now was produced by beats driven at earlier clocks
            if (o_valid === 1'b1) begin
                if (n_seen >= n_driven)                   error("o_valid with no sum finished", n_seen);
                else if (q_time[n_seen] + L != cyc)       error("result at the wrong time (latency not fixed)", n_seen);
                else if (o_data !== exp[n_seen])          begin
                    error("wrong value", n_seen);
                    if (n_err <= 10) $display("    expected %h got %h", exp[n_seen], o_data);
                end
                n_seen = n_seen + 1;
            end else if (o_valid !== 1'b0) error("o_valid is X", n_seen);
            else if (n_seen < n_driven && q_time[n_seen] + L < cyc) begin
                error("result missing", n_seen);
                n_seen = n_seen + 1;
            end
            // then drive
            if (bi < N_BEAT) begin
                if (gap_left > 0) begin junk; gap_left = gap_left - 1; end
                else begin
                    w = beat[bi];
                    drive(w);
                    if (w[37]) begin q_time[n_driven] = cyc; n_driven = n_driven + 1; end
                    bi = bi + 1;
                    if (bi < N_BEAT) gap_left = beat[bi][35:32];
                end
            end else junk;
        end
        // drain: nothing more may appear
        repeat (MAX_LAT) begin
            @(negedge i_clk); cyc = cyc + 1; junk;
            if (o_valid !== 1'b0 && n_err == 0) error("extra o_valid at the end", n_seen);
        end
        if (n_err == 0 && n_seen == N_SUM && bi == N_BEAT)
            $display("PASS: %0d sums, %0d beats, latency %0d", N_SUM, N_BEAT, L);
        else
            $display("FAIL: %0d errors (%0d of %0d sums checked)", n_err, n_seen, N_SUM);
        $finish;
    end
endmodule
`default_nettype wire
