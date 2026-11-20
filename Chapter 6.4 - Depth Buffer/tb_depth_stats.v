// tb_depth_stats.v -- the GET_STATS performance counters (Chapter 6.4).
//
// The testbench is the host: it sends sim/depth_stats_stream.hex into uart_gpu as 8N1 serial at 2.5 Mbaud
// (20 clocks per bit at 50 MHz, waiting for o_ready), decodes the reply bytes on o_tx, and does not model
// the framebuffer (the pixels are checked by `make frame-depth`; here only the counters matter).
//
// Stream entries (16 bits):  00NN  send byte NN
//                            1000  wait for the next 12 reply bytes (a GET_STATS reply) and check them
//                                  against the next row of sim/depth_stats_expect.hex:
//                                    pixels   exactly,   written  exactly,   clocks  from LO to HI
//                            1001  wait for 12 more reply bytes and require them to be identical to the
//                                  previous reply (GET_STATS does not change the counters, and commands
//                                  that draw no triangle pixels do not either)
//                            FFFF  end
// expect.hex holds four 32-bit hex words per row: pixels, written, LO, HI.
//
// What the counters must be (Chapter 6.4, Section 8): `pixels` = the pixels the rasterizer produced, for flat
// and shaded triangles alike; `written` = those that were drawn (not refused by the depth test); `clocks` =
// the clocks the engine spent drawing triangles. The first two are fixed by the model. The third depends on
// the setup time, so only a window is checked: at least one clock per pixel (the rasterizer emits at most
// one per clock) and at most the sum over the triangles of the setup and box allowance of Chapter 6.3 plus 8
// clocks to drain the depth pipeline. Any legal design passes.
//
// Run from build/sim:   make test TESTS="tb_depth_stats"     (about 10 seconds)
`timescale 1ns/1ps
`default_nettype none

module tb_depth_stats;
    localparam integer BAUD   = 2_500_000;
    localparam integer BIT_NS = 1_000_000_000 / BAUD;

    reg clk = 1'b0;
    always #10 clk = ~clk;

    reg         rst = 1'b1, rx = 1'b1;
    wire        tx, ready, overflow, fb_we, pal_we, fb_buf, flip_req;
    reg  [3:0]  ack_pipe = 4'b0000;
    wire        flip_ack = ack_pipe[3];
    wire [16:0] fb_addr;
    wire  [7:0] fb_wdata, pal_addr;
    wire [23:0] pal_wdata;
    uart_gpu #(.CLK_HZ(50_000_000), .BAUD(BAUD)) dut (
        .i_clk(clk), .i_rst(rst), .i_rx(rx), .o_tx(tx), .o_ready(ready), .o_overflow(overflow),
        .o_fb_we(fb_we), .o_fb_addr(fb_addr), .o_fb_wdata(fb_wdata), .i_fb_rdata(8'd0),
        .o_pal_we(pal_we), .o_pal_addr(pal_addr), .o_pal_wdata(pal_wdata),
        .o_fb_buf(fb_buf), .o_flip_req(flip_req), .i_flip_ack(flip_ack));
    always @(posedge clk) ack_pipe <= {ack_pipe[2:0], flip_req};

    // reply receiver
    reg [7:0] got [0:4095];
    integer   n_got = 0, rx_err = 0, k;
    reg [7:0] b;
    always begin
        @(negedge tx);
        #(BIT_NS / 2);
        if (tx !== 1'b0) rx_err = rx_err + 1;
        else begin
            for (k = 0; k < 8; k = k + 1) begin #(BIT_NS); b[k] = tx; end
            #(BIT_NS);
            if (tx !== 1'b1) rx_err = rx_err + 1;
            got[n_got] = b; n_got = n_got + 1;
            #(BIT_NS / 4);
        end
    end

    reg [15:0] stream [0:131071];
    reg [31:0] expect_w [0:4095];
    integer    pc = 0, row = 0, bad = 0, checks = 0, same_checks = 0, base = 0, j;
    reg        timeout = 1'b0;
    reg [15:0] w;
    reg [31:0] cov, wr, clk_n;
    reg [31:0] pcov, pwr, pclk;
    time       tw;

    task send_byte(input [7:0] d);
        begin
            @(negedge clk);
            tw = $time;
            while (ready !== 1'b1 && !timeout) begin
                @(negedge clk);
                if ($time - tw > 50_000_000) begin $display("tb_depth_stats: o_ready stayed low for 50 ms"); timeout = 1'b1; end
            end
            if (!timeout) begin
                rx = 1'b0; #(BIT_NS);
                for (j = 0; j < 8; j = j + 1) begin rx = d[j]; #(BIT_NS); end
                rx = 1'b1; #(BIT_NS);
            end
        end
    endtask

    task wait_reply;
        time t0;
        begin
            t0 = $time;
            while (n_got < base + 12 && !timeout) begin
                #1000;
                if ($time - t0 > 50_000_000) begin
                    $display("tb_depth_stats: GET_STATS reply did not arrive (%0d of 12 bytes)", n_got - base);
                    timeout = 1'b1;
                end
            end
            #(BIT_NS * 2);
        end
    endtask

    initial begin
        for (j = 0; j < 131072; j = j + 1) stream[j] = 16'hFFFF;
        $readmemh("../../sim/depth_stats_stream.hex", stream);
        $readmemh("../../sim/depth_stats_expect.hex", expect_w);
        if (stream[0] === 16'hxxxx || expect_w[0] === 32'hxxxxxxxx) begin
            $display("FAIL: tb_depth_stats cannot read sim/depth_stats_stream.hex (run `.venv/bin/python sim/py/ref_depth.py --stats sim`, and run from build/sim)");
            $finish;
        end
        repeat (10) @(posedge clk);
        rst = 1'b0;
        repeat (10) @(posedge clk);
        w = stream[0];
        while (w != 16'hFFFF && !timeout) begin
            if (w[15:8] == 8'h00) send_byte(w[7:0]);
            else if (w == 16'h1000 || w == 16'h1001) begin
                // replies consumed so far: count from there (a reply may already be partly in)
                base = 12 * (checks + same_checks);
                wait_reply;
                if (!timeout) begin
                    cov = {got[base+3], got[base+2], got[base+1], got[base]};
                    wr  = {got[base+7], got[base+6], got[base+5], got[base+4]};
                    clk_n = {got[base+11], got[base+10], got[base+9], got[base+8]};
                    if (w == 16'h1000) begin
                        if (cov !== expect_w[4*row] || wr !== expect_w[4*row+1]) begin
                            if (bad < 5) $display("tb_depth_stats: check %0d (stream entry %0d): pixels %0d written %0d, expected %0d and %0d",
                                                  row, pc, cov, wr, expect_w[4*row], expect_w[4*row+1]);
                            bad = bad + 1;
                        end
                        if (clk_n < expect_w[4*row+2] || clk_n > expect_w[4*row+3]) begin
                            if (bad < 5) $display("tb_depth_stats: check %0d (stream entry %0d): clocks %0d outside %0d to %0d",
                                                  row, pc, clk_n, expect_w[4*row+2], expect_w[4*row+3]);
                            bad = bad + 1;
                        end
                        row = row + 1; checks = checks + 1;
                    end else begin
                        if (cov !== pcov || wr !== pwr || clk_n !== pclk) begin
                            if (bad < 5) $display("tb_depth_stats: stream entry %0d: the counters changed between two GET_STATS (%0d %0d %0d -> %0d %0d %0d)",
                                                  pc, pcov, pwr, pclk, cov, wr, clk_n);
                            bad = bad + 1;
                        end
                        same_checks = same_checks + 1;
                    end
                    if ($test$plusargs("v")) $display("  reply: %0d pixels, %0d written, %0d clocks", cov, wr, clk_n);
                    pcov = cov; pwr = wr; pclk = clk_n;
                end
            end else $display("tb_depth_stats: bad stream entry %h at %0d", w, pc);
            pc = pc + 1;
            w = stream[pc];
        end
        #(BIT_NS * 40);
        if (timeout) $display("FAIL: tb_depth_stats (the GPU stopped answering)");
        else if (rx_err != 0) $display("FAIL: tb_depth_stats (bad serial framing on o_tx)");
        else if (overflow !== 1'b0) $display("FAIL: tb_depth_stats (FIFO overflow)");
        else if (n_got != 12 * (checks + same_checks)) $display("FAIL: tb_depth_stats (%0d reply bytes, expected %0d)", n_got, 12 * (checks + same_checks));
        else if (bad != 0) $display("FAIL: tb_depth_stats (%0d wrong counter values, see above)", bad);
        else $display("PASS: tb_depth_stats (%0d GET_STATS checks, %0d repeat checks; last reply: %0d pixels, %0d written, %0d clocks)",
                      checks, same_checks, pcov, pwr, pclk);
        $finish;
    end

    initial begin #(2_000_000_000); $display("FAIL: tb_depth_stats watchdog timeout"); $finish; end
endmodule
`default_nettype wire
