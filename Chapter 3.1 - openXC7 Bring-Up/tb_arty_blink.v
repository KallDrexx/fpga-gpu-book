// tb_arty_blink -- self-checking testbench for the Step 3.1 Arty Z7 top level
//
// The design under test is top_artyz7 from projects/3.1-arty-blink/. It is
// not under rtl/, so the simulator needs its path (see "Testbench
// requirements" in Chapter 3.1):
//
//   tb_arty_blink_SRCS := $(ROOT)/projects/3.1-arty-blink/top_artyz7.v
//
// Two copies of top_artyz7 are simulated:
//
//   dut   CLK_HZ = 1250, the 125 MHz board scaled down by 100,000, so one
//         simulated "second" is 1250 clocks. Checks that
//           * led[0] starts low, then toggles every CLK_HZ/2 clocks
//             (60 blinks in 60 "seconds", 50% duty),
//           * led[0] = blink OR btn[0]   (OR, not XOR: a held btn[0] keeps
//             it lit through a blink edge),
//           * led[3:1] follow btn[3:1], LD4 green follows sw[0], LD5 green
//             follows sw[1], and the red and blue RGB channels stay off,
//           * no output is ever X or Z.
//   full  default parameters. Checks that CLK_HZ defaults to 125,000,000
//         and that its LED does not blink within the short simulation.
//
// Inputs change on the falling clock edge and each pattern is held for
// HOLD clocks. Routing is checked only in the second half of each hold, so
// a design that synchronizes its buttons (up to HOLD/2 clocks of latency)
// passes too. The blink may also start up to MAX_LAT clocks late.
//
// Ends with one PASS or FAIL line. Run with +vcd to write tb_arty_blink.vcd.

`timescale 1ns/1ps
`default_nettype none

module tb_arty_blink;
    localparam integer CLK_HZ    = 1250;            // 125 MHz / 100,000
    localparam integer HALF      = CLK_HZ / 2;      // 625 clocks per half blink
    localparam integer N_BLINKS  = 60;
    localparam integer N_TOGGLES = 2 * N_BLINKS;
    localparam integer HOLD      = 8;               // clocks per input pattern
    localparam integer MAX_LAT   = HOLD / 2;        // latency tolerated
    localparam integer PHASE_A   = 400;             // clocks of button-0 tests (< HALF)

    // ---------------------------------------------------------------- clock
    reg clk = 1'b0;
    always #5 clk = ~clk;                           // 10 ns period

    // ---------------------------------------------------------------- DUTs
    reg  [3:0] btn = 4'b0000;
    reg  [1:0] sw  = 2'b00;
    wire [3:0] led;
    wire       led4_r, led4_g, led4_b, led5_r, led5_g, led5_b;

    top_artyz7 #(.CLK_HZ(CLK_HZ)) dut (
        .clk    (clk),
        .btn    (btn),
        .sw     (sw),
        .led    (led),
        .led4_r (led4_r), .led4_g (led4_g), .led4_b (led4_b),
        .led5_r (led5_r), .led5_g (led5_g), .led5_b (led5_b)
    );

    wire [3:0] full_led;
    wire       f4r, f4g, f4b, f5r, f5g, f5b;

    top_artyz7 full (
        .clk    (clk),
        .btn    (4'b0000),
        .sw     (2'b00),
        .led    (full_led),
        .led4_r (f4r), .led4_g (f4g), .led4_b (f4b),
        .led5_r (f5r), .led5_g (f5g), .led5_b (f5b)
    );

    // ---------------------------------------------------------------- state
    integer errors  = 0;
    integer clocks  = 0;        // rising edges since time 0
    integer hold_i  = 0;        // clocks since the inputs last changed
    integer phase   = 0;        // 0: A (all inputs), 1: B (btn[0] released), 2: C, 3: done
    integer toggles = 0;
    integer last_t  = 0;        // clock count at the previous led[0] toggle
    integer first_t = -1;
    reg     prev0   = 1'b0;
    reg     full_lit = 1'b0;
    integer seed    = 32'h3141;

    task err(input [8*96-1:0] msg);
        begin
            if (errors < 10)
                $display("ERROR at clock %0d: %0s", clocks, msg);
            errors = errors + 1;
        end
    endtask

    // Count rising edges.
    always @(posedge clk) clocks <= clocks + 1;

    // Drive new inputs on the falling edge, every HOLD clocks.
    always @(negedge clk) begin
        if (phase < 2) begin
            hold_i = hold_i + 1;
            if (hold_i >= HOLD) begin
                hold_i = 0;
                btn[3:1] = $random(seed);
                sw       = $random(seed);
                btn[0]   = (phase == 0) ? $random(seed) : 1'b0;
            end
        end
    end

    // Check routing just after each falling edge, in the stable half of a hold.
    always @(negedge clk) begin
        #1;
        if (phase < 2 && hold_i >= MAX_LAT) begin
            if (led[3:1] !== btn[3:1]) err("led[3:1] does not follow btn[3:1]");
            if (led4_g !== sw[0])      err("led4_g does not follow sw[0]");
            if (led5_g !== sw[1])      err("led5_g does not follow sw[1]");
            if (phase == 0 && led[0] !== btn[0])
                err("led[0] does not follow btn[0] before the first blink");
        end
        if ({led4_r, led4_b, led5_r, led5_b} !== 4'b0000)
            err("an RGB red or blue channel is not driven low");
        if (^{led, led4_r, led4_g, led4_b, led5_r, led5_g, led5_b} === 1'bx)
            err("an output is X or Z");
        if (full_led[0] !== 1'b0) full_lit = 1'b1;
    end

    // ---------------------------------------------------------------- main
    integer k;
    reg     went_low;
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_arty_blink.vcd");
            $dumpvars(0, tb_arty_blink);
        end

        if (full.CLK_HZ != 125_000_000) begin
            $display("ERROR: top_artyz7 CLK_HZ defaults to %0d, expected 125000000", full.CLK_HZ);
            errors = errors + 1;
        end

        #1;
        if (led[0] !== 1'b0) err("led[0] should start low");

        // Phase A: every input random, including btn[0]; blink is still low.
        while (clocks < PHASE_A) @(posedge clk);
        @(negedge clk); #2;
        phase = 1;     // btn[0] is forced low from the next pattern on

        // Wait for btn[0]'s release to reach led[0].
        repeat (HOLD + MAX_LAT) @(posedge clk);
        #1;
        if (led[0] !== 1'b0) err("led[0] did not go low after btn[0] was released");
        prev0 = led[0];

        // Phase B: measure 120 toggles of led[0] with btn[0] released.
        while (phase == 1) begin
            @(posedge clk); #1;
            if (led[0] !== 1'b0 && led[0] !== 1'b1) begin
                err("led[0] is X or Z");
                phase = 3;
            end else if (led[0] !== prev0) begin
                if (toggles == 0) begin
                    first_t = clocks;
                    if (clocks < HALF || clocks > HALF + MAX_LAT) begin
                        $display("ERROR: first toggle after %0d clocks, expected %0d", clocks, HALF);
                        errors = errors + 1;
                    end
                end else if (clocks - last_t != HALF) begin
                    if (errors < 10)
                        $display("ERROR: toggle %0d came %0d clocks after the previous one, expected %0d",
                                 toggles + 1, clocks - last_t, HALF);
                    errors = errors + 1;
                end
                toggles = toggles + 1;
                last_t  = clocks;
                prev0   = led[0];
                if (toggles == N_TOGGLES) phase = 2;
            end else if (clocks - last_t > HALF + MAX_LAT) begin
                err("led[0] stopped toggling (stuck?)");
                phase = 3;
            end
        end

        if (phase == 2) begin
            if (led[0] !== 1'b0) err("led[0] should be low after a whole number of blinks");
            if (last_t - first_t != (N_TOGGLES - 1) * HALF) begin
                $display("ERROR: %0d blinks took %0d clocks, expected %0d",
                         N_BLINKS, last_t - first_t + HALF, N_BLINKS * CLK_HZ);
                errors = errors + 1;
            end

            // Phase C: OR, not XOR. Wait for the blink to turn on, hold
            // btn[0] across the falling edge of the blink, then release.
            @(posedge led[0]);
            @(negedge clk); btn[0] = 1'b1;
            went_low = 1'b0;
            for (k = 0; k < HALF + 2 * MAX_LAT; k = k + 1) begin
                @(negedge clk); #1;
                if (led[0] !== 1'b1) went_low = 1'b1;
            end
            if (went_low) err("led[0] went low while btn[0] was held (use OR, not XOR)");
            @(negedge clk); btn[0] = 1'b0;
            repeat (2 * MAX_LAT) @(negedge clk);
            #1;
            if (led[0] !== 1'b0) err("led[0] stayed high after btn[0] was released");
        end

        if (full_lit) begin
            $display("ERROR: default-parameter top_artyz7 lit led[0] within %0d clocks (CLK_HZ not used?)",
                     clocks);
            errors = errors + 1;
        end

        if (errors == 0)
            $display("PASS tb_arty_blink: %0d blinks in %0d clocks at CLK_HZ=%0d, buttons and switches routed",
                     N_BLINKS, N_BLINKS * CLK_HZ, CLK_HZ);
        else
            $display("FAIL tb_arty_blink: %0d error(s)", errors);
        $finish;
    end

    // Watchdog: a hung design must still produce a FAIL line.
    initial begin
        #((N_BLINKS + 4) * CLK_HZ * 10 + 100_000);
        $display("FAIL tb_arty_blink: timeout");
        $finish;
    end
endmodule

`default_nettype wire
