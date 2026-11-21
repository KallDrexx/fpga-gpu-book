// tb_light_budget.v -- the capstone's frame budget and per-frame host traffic (Chapter 6.7).
//
// uart_gpu at 2.5 Mbaud, a modelled display (60 Hz: a vertical blank of 70,000 clocks every 833,333 clocks; the flip acknowledge
// arrives at the start of the blank) and no framebuffer: the evidence is time and the counters.
//
// What it does, in order:
//   1. sends sim/budget_setup.hex: projection, light, depth test, mesh mode, the torus (the same slots as in the full scene: vertices
//      and normals 66 to 161, entries 92 to 283) and its display list (list 2: TRANSFORM 66 96, CLEAR_ALL, DRAW_MESH 92 192, FLIP);
//   2. measures the commands of the torus list one at a time, in engine clocks, with GET_MESH_STATS as the marker (the time
//      from the last stop bit of the command to the first start bit of the 12-byte reply): TRANSFORM with lighting off,
//      TRANSFORM with lighting on, CLEAR_ALL, DRAW_MESH. The difference of the two TRANSFORMs is the cost of lighting;
//   3. clears the counters and runs 3 frames the way a host runs a game (protocol.md section 14): wait until the previous
//      FLIP has been acknowledged, wait for the first clock after the blank, then send SET_MATRIX + RUN_LIST and nothing else.
//      This testbench builds those 29 bytes itself, from the matrices in sim/budget_frames.hex, and counts them. Per frame it
//      measures the render time: the last stop bit of RUN_LIST to the engine's FLIP request;
//   4. reads GET_MESH_STATS and GET_STATS once and compares all 24 bytes with sim/budget_expect.hex (the clock bytes of
//      GET_STATS are masked), so the three frames drew exactly the model's triangles and pixels.
//
// Verdict: FAIL if a frame needs other than 29 bytes, if a render takes more than 30 ms (1,500,000 clocks), if the three frames
// do not run at 30 frames per second or better (frame starts at most 1,666,667 clocks apart on average), or if any counter is wrong.
// The PASS line reports the numbers the chapter's budget table quotes.
//
// Run from build/sim:   make test TESTS="tb_light_budget"      (a few minutes; add +progress to the vvp command line to see the phases)
`timescale 1ns/1ps
`default_nettype none

module tb_light_budget;
    localparam integer BAUD = 2_500_000, BIT_NS = 1_000_000_000 / BAUD;
    localparam integer PERIOD = 833_333, BLANK = 70_000, NFR = 3;
    localparam integer RENDER_LIMIT = 1_500_000, INTERVAL_LIMIT = 1_666_667;

    reg clk = 1'b0;
    always #10 clk = ~clk;

    reg         rst = 1'b1, rx = 1'b1;
    wire        tx, ready, overflow, fb_we, pal_we, fb_buf, flip_req;
    reg         flip_ack = 1'b0;
    wire [16:0] fb_addr;
    wire  [7:0] fb_wdata, pal_addr;
    wire [23:0] pal_wdata;
    uart_gpu #(.CLK_HZ(50_000_000), .BAUD(BAUD)) dut (
        .i_clk(clk), .i_rst(rst), .i_rx(rx), .o_tx(tx), .o_ready(ready), .o_overflow(overflow),
        .o_fb_we(fb_we), .o_fb_addr(fb_addr), .o_fb_wdata(fb_wdata), .i_fb_rdata(8'd0),
        .o_pal_we(pal_we), .o_pal_addr(pal_addr), .o_pal_wdata(pal_wdata),
        .o_fb_buf(fb_buf), .o_flip_req(flip_req), .i_flip_ack(flip_ack));

    // the display: frame starts at cnt == 0, the blank begins at PERIOD - BLANK and acknowledges the flip
    integer cnt = 0, vb_n = 0;
    reg     frame_start = 1'b0;
    always @(posedge clk) begin
        cnt <= (cnt == PERIOD - 1) ? 0 : cnt + 1;
        frame_start <= (cnt == PERIOD - 1);
        if (cnt == PERIOD - BLANK) begin flip_ack <= flip_req; vb_n <= vb_n + 1; end
    end

    // reply receiver with a time stamp on each byte's start bit
    reg  [7:0] rep [0:255];
    time       rep_t [0:255];
    integer    n_got = 0, rx_err = 0, k, j;
    reg  [7:0] b;
    always begin
        @(negedge tx);
        if (n_got < 256) rep_t[n_got] = $time;
        #(BIT_NS / 2);
        if (tx !== 1'b0) rx_err = rx_err + 1;
        else begin
            for (k = 0; k < 8; k = k + 1) begin #(BIT_NS); b[k] = tx; end
            #(BIT_NS);
            if (tx !== 1'b1) rx_err = rx_err + 1;
            if (n_got < 256) rep[n_got] = b;
            n_got = n_got + 1;
            #(BIT_NS / 4);
        end
    end

    reg  timeout = 1'b0;
    integer sent = 0;
    time tw;
    task send_byte(input [7:0] d);
        begin
            @(negedge clk);
            tw = $time;
            while (ready !== 1'b1 && !timeout) begin
                @(negedge clk);
                if ($time - tw > 50_000_000) begin $display("tb_light_budget: o_ready stayed low for 50 ms"); timeout = 1'b1; end
            end
            if (!timeout) begin
                rx = 1'b0; #(BIT_NS);
                for (j = 0; j < 8; j = j + 1) begin rx = d[j]; #(BIT_NS); end
                rx = 1'b1; #(BIT_NS);
                sent = sent + 1;
            end
        end
    endtask

    task wait_replies(input integer upto);
        time t0;
        begin
            t0 = $time;
            while (n_got < upto && !timeout) begin
                #1000;
                if ($time - t0 > 100_000_000) begin
                    $display("tb_light_budget: replies stopped after byte %0d of %0d", n_got, upto);
                    timeout = 1'b1;
                end
            end
        end
    endtask

    reg [15:0] setup [0:65535];
    reg [15:0] frames_w [0:127];
    reg [15:0] expect_w [0:63];
    reg [15:0] mw;
    integer    pc, i, q, bytes_frame, bad_frames = 0, expect_n;
    integer    t_transform, t_transform_lit, t_clear, t_draw, base, goal;
    time       t_end, t_flip, t_ack, t_fs [0:NFR-1];
    integer    render [0:NFR-1];
    integer    render_max = 0, render_sum = 0, interval_sum, missed = 0;
    reg  [15:0] lt [0:3];

    // one measured command: send `n` bytes from pk, then GET_MESH_STATS; the time from the end of the command to the reply's first start bit
    reg [7:0] pk [0:63];
    integer   measured_clocks;
    task measure(input integer n);
        integer q;
        time te;
        begin
            for (q = 0; q < n; q = q + 1) send_byte(pk[q]);
            te = $time;
            send_byte(8'h69); send_byte(8'h00);
            goal = n_got + 12;
            wait_replies(goal);
            measured_clocks = (rep_t[goal - 12] - te) / 20;
        end
    endtask

    task put_matrix(input integer frame);
        integer q;
        begin
            pk[0] = 8'h71; pk[1] = 8'd24;
            for (q = 0; q < 12; q = q + 1) begin
                mw = frames_w[4 + 12 * frame + q];
                pk[2 + 2 * q] = mw[7:0]; pk[3 + 2 * q] = mw[15:8];
            end
        end
    endtask
    task put_light(input enable);
        begin
            pk[0] = 8'h6C; pk[1] = 8'd9; pk[2] = enable ? 8'h01 : 8'h00;
            pk[3] = lt[0][7:0]; pk[4] = lt[0][15:8]; pk[5] = lt[1][7:0]; pk[6] = lt[1][15:8];
            pk[7] = lt[2][7:0]; pk[8] = lt[2][15:8]; pk[9] = lt[3][7:0]; pk[10] = lt[3][15:8];
        end
    endtask

    initial begin
        for (i = 0; i < 65536; i = i + 1) setup[i] = 16'hFFFF;
        for (i = 0; i < 128; i = i + 1) frames_w[i] = 16'hxxxx;
        for (i = 0; i < 64; i = i + 1) expect_w[i] = 16'hxxxx;
        $readmemh("../../sim/budget_setup.hex", setup);
        $readmemh("../../sim/budget_frames.hex", frames_w);
        $readmemh("../../sim/budget_expect.hex", expect_w);
        if (setup[0] === 16'hxxxx || frames_w[0] === 16'hxxxx || expect_w[0] === 16'hxxxx) begin
            $display("FAIL: tb_light_budget cannot read sim/budget_*.hex (run `.venv/bin/python sim/py/ref_lit.py --files sim`, and run from build/sim)");
            $finish;
        end
        expect_n = 0;
        while (^expect_w[expect_n] !== 1'bx) expect_n = expect_n + 1;
        for (i = 0; i < 4; i = i + 1) lt[i] = frames_w[i];
        repeat (10) @(posedge clk);
        rst = 1'b0;
        repeat (10) @(posedge clk);
        // 1. setup
        if ($test$plusargs("progress")) $display("progress: setup");
        pc = 0;
        while (setup[pc] != 16'hFFFF && !timeout) begin send_byte(setup[pc][7:0]); pc = pc + 1; end
        // 2. measurements on the torus (slots 66..161, entries 92..283)
        if ($test$plusargs("progress")) $display("progress: measurements at %0t, %0d bytes sent", $time, sent);
        put_matrix(0); for (i = 0; i < 26; i = i + 1) send_byte(pk[i]);
        put_light(1'b0); for (i = 0; i < 11; i = i + 1) send_byte(pk[i]);
        pk[0] = 8'h73; pk[1] = 8'd4; pk[2] = 8'd66; pk[3] = 8'd0; pk[4] = 8'd96; pk[5] = 8'd0;
        measure(6); t_transform = measured_clocks;
        put_light(1'b1); for (i = 0; i < 11; i = i + 1) send_byte(pk[i]);
        pk[0] = 8'h73; pk[1] = 8'd4; pk[2] = 8'd66; pk[3] = 8'd0; pk[4] = 8'd96; pk[5] = 8'd0;
        measure(6); t_transform_lit = measured_clocks;
        pk[0] = 8'h63; pk[1] = 8'd3; pk[2] = 8'd0; pk[3] = 8'hFF; pk[4] = 8'hFF;
        measure(5); t_clear = measured_clocks;
        pk[0] = 8'h68; pk[1] = 8'd4; pk[2] = 8'd92; pk[3] = 8'd0; pk[4] = 8'd192; pk[5] = 8'd0;
        measure(6); t_draw = measured_clocks;
        base = n_got;
        if ($test$plusargs("progress")) $display("progress: frames at %0t: TRANSFORM %0d lit %0d CLEAR_ALL %0d DRAW_MESH %0d", $time, t_transform, t_transform_lit, t_clear, t_draw);
        // 3. the frames: only SET_MATRIX + RUN_LIST 2 from here on (the two counter clears are setup)
        send_byte(8'h6A); send_byte(8'h00); send_byte(8'h65); send_byte(8'h00);
        for (i = 0; i < NFR && !timeout && render_max <= RENDER_LIMIT; i = i + 1) begin
            while (flip_ack !== flip_req) @(posedge clk);                       // the previous picture is on the screen
            @(posedge clk); while (!frame_start) @(posedge clk);
            t_fs[i] = $time;
            put_matrix(i);
            bytes_frame = 0;
            for (q = 0; q < 26; q = q + 1) begin send_byte(pk[q]); bytes_frame = bytes_frame + 1; end
            send_byte(8'h52); send_byte(8'h01); send_byte(8'h02); bytes_frame = bytes_frame + 3;
            t_end = $time;
            if (bytes_frame != 29) bad_frames = bad_frames + 1;
            while (flip_req === flip_ack && !timeout) begin
                @(posedge clk);
                if ($time - t_end > 100_000_000) begin $display("tb_light_budget: frame %0d: no FLIP within 100 ms", i); timeout = 1'b1; end
            end
            t_flip = $time;
            if ($test$plusargs("progress")) $display("progress: frame %0d rendered in %0d clocks at %0t", i, (t_flip - t_end) / 20, $time);
            render[i] = (t_flip - t_end) / 20;
            if (render[i] > render_max) render_max = render[i];
            render_sum = render_sum + render[i];
            if ($time - t_fs[i] > (PERIOD - BLANK) * 20) missed = missed + 1;     // the flip came after the blank started: it waits for the next
        end
        while (flip_ack !== flip_req && !timeout) @(posedge clk);
        // 4. the counters
        send_byte(8'h69); send_byte(8'h00); send_byte(8'h64); send_byte(8'h00);
        wait_replies(base + 24);
        #(BIT_NS * 20);
        interval_sum = (t_fs[NFR-1] - t_fs[0]) / 20;           // (only meaningful when all frames ran: a slow render stops the loop and fails first)
        begin : verdict
            integer bad, e;
            bad = 0;
            for (e = 0; e < 24; e = e + 1)
                if (((rep[base + e] ^ expect_w[e][7:0]) & expect_w[e][15:8]) !== 8'h00) begin
                    if (bad < 6) $display("tb_light_budget: counter byte %0d is %02x, expected %02x (mask %02x)", e, rep[base + e], expect_w[e][7:0], expect_w[e][15:8]);
                    bad = bad + 1;
                end
            if (timeout) $display("FAIL: tb_light_budget (the GPU stopped answering)");
            else if (rx_err != 0) $display("FAIL: tb_light_budget (bad serial framing on o_tx)");
            else if (overflow !== 1'b0) $display("FAIL: tb_light_budget (FIFO overflow)");
            else if (bad_frames != 0) $display("FAIL: tb_light_budget (a frame needed other than 29 bytes)");
            else if (render_max > RENDER_LIMIT) $display("FAIL: tb_light_budget (a frame took %0d clocks to render, more than %0d = 30 ms)", render_max, RENDER_LIMIT);
            else if (n_got != base + 24) $display("FAIL: tb_light_budget (%0d reply bytes, expected %0d)", n_got, base + 24);
            else if (bad != 0) $display("FAIL: tb_light_budget (%0d wrong counter bytes, see above)", bad);
            else if (interval_sum / (NFR - 1) > INTERVAL_LIMIT) $display("FAIL: tb_light_budget (frames %0d clocks apart on average: slower than 30 frames per second)", interval_sum / (NFR - 1));
            else
                $display("PASS: tb_light_budget (%0d frames of 29 bytes; render %0d clocks (%0d us) at most; TRANSFORM %0d (+%0d for lighting), CLEAR_ALL %0d, DRAW_MESH %0d clocks; frames %0d clocks apart = %0d frames/s; %0d flips after the blank start)",
                         NFR, render_max, render_max / 50, t_transform, t_transform_lit - t_transform, t_clear, t_draw,
                         interval_sum / (NFR - 1), 50_000_000 / (interval_sum / (NFR - 1)), missed);
        end
        $finish;
    end

    initial begin #(3_000_000_000); $display("FAIL: tb_light_budget watchdog timeout"); $finish; end
endmodule
`default_nettype wire
