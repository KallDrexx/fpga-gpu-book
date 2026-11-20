// tb_depth_unit.v -- the depth buffer's read-compare-write pipeline on its own (Chapter 6.4).
//
// Random pixel streams are pushed into depth_unit and every pixel's verdict is checked against a plain
// behavioural model (an array and a loop) that sees the same pixels in the same order. The streams are
// built to hit the read-after-write hazard: addresses come from a pool of 16, half the pixels repeat the
// previous address, depths from a pool of 8 values so that equal depths are common, and the pixel
// spacing is 0 to 3 idle clocks. All four mode combinations (test, write) occur, and the clear port is
// used between bursts. Any pipeline latency is accepted: the testbench compares the sequence of colour
// writes (address, colour), not their timing, and waits for o_busy to fall at the end of each burst.
//
// Run from build/sim:   make test TESTS="tb_depth_unit"      (about a second)
`timescale 1ns/1ps
`default_nettype none

module tb_depth_unit;
    reg clk = 1'b0;
    always #10 clk = ~clk;

    reg         v = 1'b0, tst = 1'b0, zw = 1'b0;
    reg  [16:0] a = 17'd0;
    reg  [15:0] z = 16'd0;
    reg   [7:0] c = 8'd0;
    reg         cw_en = 1'b0;
    reg  [16:0] cw_a = 17'd0;
    reg  [15:0] cw_d = 16'd0;
    wire        we, busy;
    wire [16:0] oa;
    wire  [7:0] oc;

    depth_unit dut (.i_clk(clk), .i_valid(v), .i_addr(a), .i_z(z), .i_color(c), .i_test(tst), .i_zwrite(zw),
                    .i_cw_en(cw_en), .i_cw_addr(cw_a), .i_cw_data(cw_d),
                    .o_we(we), .o_addr(oa), .o_color(oc), .o_busy(busy));

    // behavioural model and the queue of expected colour writes
    reg [15:0] ref_mem [0:76799];
    reg [24:0] expq [0:65535];            // {addr, colour}
    integer    qh = 0, qt = 0;
    integer    bad = 0, npix = 0, nwrites = 0, nrej = 0, nfwdcase = 0;
    reg [24:0] e;

    reg [16:0] pool_a [0:15];
    reg [15:0] pool_z [0:7];
    integer    i, gap, burst;
    reg [16:0] prev_a;
    reg        prev_wrote;

    // o_we and o_busy must never be X (an X write enable would slip past the check below)
    always @(posedge clk) if ((we !== 1'b0 && we !== 1'b1) || (busy !== 1'b0 && busy !== 1'b1)) begin
        if (bad < 5) $display("tb_depth_unit: o_we or o_busy is X at %0t", $time);
        bad = bad + 1;
    end

    // check every DUT write against the queue, in order
    always @(posedge clk) if (we) begin
        nwrites = nwrites + 1;
        if (qh == qt) begin
            if (bad < 5) $display("tb_depth_unit: unexpected colour write at address %0d", oa);
            bad = bad + 1;
        end else begin
            e = expq[qh]; qh = qh + 1;
            if (e !== {oa, oc}) begin
                if (bad < 5) $display("tb_depth_unit: write %0d: expected address %0d colour %0d, got %0d colour %0d",
                                      nwrites, e[24:8], e[7:0], oa, oc);
                bad = bad + 1;
            end
        end
    end

    // one pixel per clock when gap == 0; idle clocks after the pixel otherwise
    task push_fast(input [16:0] pa, input [15:0] pz, input [7:0] pc, input pt, input pw, input idle);
        reg pass;
        integer j;
        begin
            pass = !pt || (pz < ref_mem[pa]);
            if (pa == prev_a && prev_wrote) nfwdcase = nfwdcase + 1;
            prev_a = pa; prev_wrote = pass && pw;
            if (pass) begin
                expq[qt] = {pa, pc}; qt = qt + 1;
                if (pw) ref_mem[pa] = pz;
            end else nrej = nrej + 1;
            npix = npix + 1;
            v = 1'b1; a = pa; z = pz; c = pc; tst = pt; zw = pw;
            @(negedge clk);
            v = 1'b0;
            if (idle) for (j = 0; j < gap; j = j + 1) @(negedge clk);
        end
    endtask

    task drain;
        begin
            v = 1'b0;
            repeat (2) @(negedge clk);
            while (busy) @(negedge clk);
            repeat (2) @(negedge clk);
        end
    endtask

    task clear_all(input [15:0] val);
        integer j;
        begin
            for (j = 0; j < 16; j = j + 1) begin
                cw_en = 1'b1; cw_a = pool_a[j]; cw_d = val; ref_mem[pool_a[j]] = val;
                @(negedge clk);
            end
            cw_en = 1'b0;
            @(negedge clk);
        end
    endtask

    initial begin
        if ($test$plusargs("vcd")) begin $dumpfile("tb_depth_unit.vcd"); $dumpvars(0, tb_depth_unit); end
        for (i = 0; i < 76800; i = i + 1) ref_mem[i] = 16'd0;
        pool_a[0] = 0;      pool_a[1] = 1;      pool_a[2] = 2047;   pool_a[3] = 2048;
        pool_a[4] = 319;    pool_a[5] = 320;    pool_a[6] = 32767;  pool_a[7] = 32768;
        pool_a[8] = 65535;  pool_a[9] = 65536;  pool_a[10] = 76799; pool_a[11] = 76480;
        pool_a[12] = 40000; pool_a[13] = 40001; pool_a[14] = 40320; pool_a[15] = 12345;
        pool_z[0] = 0;      pool_z[1] = 1;      pool_z[2] = 100;    pool_z[3] = 100;
        pool_z[4] = 30000;  pool_z[5] = 30001;  pool_z[6] = 65534;  pool_z[7] = 65535;
        prev_a = 17'h1FFFF; prev_wrote = 1'b0;
        repeat (4) @(negedge clk);
        for (burst = 0; burst < 400; burst = burst + 1) begin
            // bring the model and the unit to a known state, with a clear value that changes
            clear_all(burst % 3 == 0 ? 16'hFFFF : burst % 3 == 1 ? 16'd100 : 16'd30000);
            gap = (burst % 4);                           // 0: one pixel per clock
            prev_a = 17'h1FFFF; prev_wrote = 1'b0;
            for (i = 0; i < 80; i = i + 1) begin : pix
                reg [16:0] pa;
                if ($urandom % 2 == 0 && i > 0) pa = prev_a; else pa = pool_a[$urandom % 16];
                push_fast(pa, pool_z[$urandom % 8], $urandom % 256, ($urandom % 4) != 0, ($urandom % 4) != 0, 1'b1);
            end
            drain;
        end
        // directed: the same address in 6 consecutive clocks with falling, equal, rising z
        clear_all(16'hFFFF);
        gap = 0; prev_a = 17'h1FFFF; prev_wrote = 1'b0;
        push_fast(17'd777, 16'd500, 8'd1, 1'b1, 1'b1, 1'b1);
        push_fast(17'd777, 16'd400, 8'd2, 1'b1, 1'b1, 1'b1);
        push_fast(17'd777, 16'd400, 8'd3, 1'b1, 1'b1, 1'b1);
        push_fast(17'd777, 16'd401, 8'd4, 1'b1, 1'b1, 1'b1);
        push_fast(17'd777, 16'd300, 8'd5, 1'b1, 1'b0, 1'b1);
        push_fast(17'd777, 16'd350, 8'd6, 1'b1, 1'b1, 1'b1);
        drain;
        #100;
        if (qh != qt) begin $display("tb_depth_unit: %0d expected colour writes never came", qt - qh); bad = bad + 1; end
        if (nfwdcase < 1000) begin $display("tb_depth_unit: test too weak: only %0d back-to-back same-address pixels", nfwdcase); bad = bad + 1; end
        if (bad == 0)
            $display("PASS: tb_depth_unit (%0d pixels, %0d drawn, %0d rejected, %0d back-to-back same-address pixels where the first one wrote depth)",
                     npix, nwrites, nrej, nfwdcase);
        else
            $display("FAIL: tb_depth_unit (%0d mismatches in %0d pixels)", bad, npix);
        $finish;
    end

    initial begin #(200_000_000); $display("FAIL: tb_depth_unit watchdog"); $finish; end
endmodule
`default_nettype wire
