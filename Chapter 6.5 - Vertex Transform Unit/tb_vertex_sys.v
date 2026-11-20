// tb_vertex_sys.v -- the vertex transform through the real command path (Chapter 6.5).
//
// The testbench is the host: it sends sim/vertex_stream.hex into uart_gpu as 8N1 serial at 2.5 Mbaud (20 clocks per
// bit at 50 MHz, waiting for o_ready), receives every reply byte on o_tx, and compares each one, as it arrives, with
// the next row of sim/vertex_expect.hex. It does not model the framebuffer: only the vertex path is under test.
//
// What the stream does (sim/py/ref_vertex.py, section "tb_vertex_sys files"): GET_INFO and the power-up matrix, f and
// near; 1,024 random vertices uploaded in scrambled 42-vertex packets; twenty random matrices, each transforming 50
// vertices that are all read back (1,000 vertices, plus the rest of the checks: about 1,100 READ_VERTEX replies); the
// end of the buffer; every wrong length (BAD_LEN in GET_INFO, nothing changed); a display list with SET_MATRIX,
// SET_PROJECTION and TRANSFORM, and with UPLOAD_VERTICES and READ_VERTEX that a list may not hold; the near plane to
// the bit; saturation, the CLAMP flag, near = 0 and a negative focal length.
//
// Stream entries (16 bits):  00NN  send byte NN
//                            1NNN  wait until NNN more reply bytes have arrived (NNN = 1..4095)
//                            FFFF  end
// expect rows (16 bits):     MMBB  expected reply byte BB; only the bits set in MM are compared
//                            (a NEAR vertex's position fields are undefined, so their rows have MM = 00)
// Every reply byte must match its row, no reply may be missing and none extra (a READ_VERTEX of a slot that does not
// exist makes no reply; the next GET_INFO proves it).
//

//
// Run from build/sim:   make test TESTS="tb_vertex_sys"      (about 2 minutes)
`timescale 1ns/1ps
`default_nettype none

module tb_vertex_sys;
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

    reg [15:0] stream [0:65535];
    reg [15:0] expect_w [0:65535];
    integer    n_rows = 0, n_got = 0, rx_err = 0, bad = 0, k, j, pc = 0, fences = 0, target = 0, sent = 0;
    reg        timeout = 1'b0;
    reg  [7:0] b;
    reg [15:0] w, row;

    // reply receiver: decodes the serial line and checks each byte against its row
    always begin
        @(negedge tx);
        #(BIT_NS / 2);
        if (tx !== 1'b0) rx_err = rx_err + 1;
        else begin
            for (k = 0; k < 8; k = k + 1) begin #(BIT_NS); b[k] = tx; end
            #(BIT_NS);
            if (tx !== 1'b1) rx_err = rx_err + 1;
            row = expect_w[n_got];
            if (n_got >= n_rows) begin
                if (bad < 8) $display("tb_vertex_sys: extra reply byte %02x (number %0d)", b, n_got);
                bad = bad + 1;
            end else if (((b ^ row[7:0]) & row[15:8]) !== 8'h00) begin
                if (bad < 8) $display("tb_vertex_sys: reply byte %0d is %02x, expected %02x (mask %02x)", n_got, b, row[7:0], row[15:8]);
                bad = bad + 1;
            end
            n_got = n_got + 1;
            #(BIT_NS / 4);
        end
    end

    time tw;
    task send_byte(input [7:0] d);
        begin
            @(negedge clk);
            tw = $time;
            while (ready !== 1'b1 && !timeout) begin
                @(negedge clk);
                if ($time - tw > 50_000_000) begin $display("tb_vertex_sys: o_ready stayed low for 50 ms"); timeout = 1'b1; end
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
                if ($time - t0 > 50_000_000) begin
                    $display("tb_vertex_sys: replies stopped after byte %0d of %0d (fence %0d)", n_got, upto, fences);
                    timeout = 1'b1;
                end
            end
        end
    endtask

    initial begin
        for (j = 0; j < 65536; j = j + 1) begin stream[j] = 16'hFFFF; expect_w[j] = 16'hxxxx; end
        $readmemh("../../sim/vertex_stream.hex", stream);
        $readmemh("../../sim/vertex_expect.hex", expect_w);
        if (stream[0] === 16'hxxxx || expect_w[0] === 16'hxxxx) begin
            $display("FAIL: tb_vertex_sys cannot read sim/vertex_stream.hex / vertex_expect.hex (run `.venv/bin/python sim/py/ref_vertex.py sim`, and run from build/sim)");
            $finish;
        end
        while (^expect_w[n_rows] !== 1'bx) n_rows = n_rows + 1;
        repeat (10) @(posedge clk);
        rst = 1'b0;
        repeat (10) @(posedge clk);
        w = stream[0];
        while (w != 16'hFFFF && !timeout) begin
            if (w[15:12] == 4'h0) send_byte(w[7:0]);
            else begin
                target = target + w[11:0];
                fences = fences + 1;
                wait_replies(target);
            end
            pc = pc + 1;
            w = stream[pc];
        end
        #(BIT_NS * 100);
        if (timeout) $display("FAIL: tb_vertex_sys (the GPU stopped answering)");
        else if (rx_err != 0) $display("FAIL: tb_vertex_sys (bad serial framing on o_tx)");
        else if (overflow !== 1'b0) $display("FAIL: tb_vertex_sys (FIFO overflow)");
        else if (n_got != n_rows) $display("FAIL: tb_vertex_sys (%0d reply bytes, expected %0d)", n_got, n_rows);
        else if (bad != 0) $display("FAIL: tb_vertex_sys (%0d wrong reply bytes, see above)", bad);
        else $display("PASS: tb_vertex_sys (%0d command bytes, %0d reply bytes, %0d fences, all as the model says)", sent, n_got, fences);
        $finish;
    end

    initial begin #(4_000_000_000); $display("FAIL: tb_vertex_sys watchdog timeout"); $finish; end
endmodule
`default_nettype wire
