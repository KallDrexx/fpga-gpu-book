// tb_reg_parser -- self-checking testbench for Step 0.5 (FIFO and register
// protocol): the packet parser and register file, behind your FIFO, with a
// transmitter that is often busy for a long time.
//
// Copy to sim/tb_reg_parser.v. Expects your `fifo` (see tb_fifo.v) and a
// module `reg_parser` in rtl/ (for example rtl/common/reg_parser.v):
//
//   module reg_parser (input  wire       i_clk,
//                      // read side of the FIFO
//                      input  wire       i_empty,
//                      input  wire [7:0] i_rd_data,
//                      output wire       o_rd_en,      // reg is fine too
//                      // uart_tx's handshake
//                      input  wire       i_tx_busy,
//                      output reg        o_tx_valid,   // wire is fine too
//                      output reg  [7:0] o_tx_data,
//                      // registers 0 and 1, for the LEDs and the display
//                      output wire [7:0] o_reg0,
//                      output wire [7:0] o_reg1);
//
// The protocol: WRITE is 0x57 ('W'), addr, data, with no reply; READ is 0x52
// ('R'), addr, replied to with one byte. 16 registers at 0x00..0x0F, all 00 at
// power-up; writes to 0x10 and up are ignored and reads of them return 00. A
// byte that is neither 'W' nor 'R' where an opcode is expected is skipped.
//
// The testbench stands in for uart_rx and uart_tx. It pushes the packet
// stream into a fifo #(8, 16) whenever the FIFO is not full, and plays a
// transmitter that accepts a reply on an edge where o_tx_valid = 1 and
// i_tx_busy = 0, then stays busy for a random time: 2 to 200 clocks in some
// phases, 3,000 clocks in others, so that the parser has to wait with a
// reply and the FIFO fills to full behind it. It checks:
//   * At time zero o_tx_valid = 0 and o_reg0 = o_reg1 = 00.
//   * Every READ produces exactly one accepted reply, in order, with the
//     value the register held after all earlier WRITEs. Nothing else is sent.
//     No reply may be lost because the transmitter was busy, and no byte may
//     be lost because the parser read the FIFO while it could not use it.
//   * o_reg0 and o_reg1 step through exactly the values written to registers
//     0 and 1, in order, and end on the last one.
//   * Out-of-range addresses do not alias, opcode values used as addresses
//     or data are not taken for opcodes, and junk bytes are skipped.
//
// Prints exactly one PASS or FAIL line, then $finish. Run with +vcd to dump
// tb_reg_parser.vcd.

`timescale 1ns/1ps
`default_nettype none

module tb_reg_parser;
    localparam integer MAX_BYTES = 16384;
    localparam integer MAX_REPLY = 4096;
    localparam integer MAX_VALS  = 2048;
    localparam integer MAX_SHOWN = 6;
    localparam [7:0]   OP_W      = 8'h57;
    localparam [7:0]   OP_R      = 8'h52;

    reg clk = 1'b0;
    always #5 clk = ~clk;

    // ---- The FIFO, written by the testbench, read by the parser.
    reg        wr_en   = 1'b0;
    reg  [7:0] wr_data = 8'h00;
    wire [7:0] rd_data;
    wire       rd_en, empty, full;
    wire [4:0] count;

    fifo #(.WIDTH(8), .DEPTH(16)) u_fifo (
        .i_clk(clk),
        .i_wr_en(wr_en), .i_wr_data(wr_data),
        .i_rd_en(rd_en), .o_rd_data(rd_data),
        .o_empty(empty), .o_full(full), .o_count(count)
    );

    reg        tx_busy = 1'b0;
    wire       tx_valid;
    wire [7:0] tx_data;
    wire [7:0] reg0, reg1;

    reg_parser dut (
        .i_clk(clk),
        .i_empty(empty), .i_rd_data(rd_data), .o_rd_en(rd_en),
        .i_tx_busy(tx_busy), .o_tx_valid(tx_valid), .o_tx_data(tx_data),
        .o_reg0(reg0), .o_reg1(reg1)
    );

    integer errors = 0;
    integer seed   = 1505;
    integer cyc    = 0;
    reg [8*100-1:0] m;

    task err_msg(input [8*100-1:0] msg);
        begin
            if (errors < MAX_SHOWN) $display("  cycle %0d: %0s", cyc, msg);
            errors = errors + 1;
        end
    endtask

    // ---- The stream and what it should produce, built ahead of time.
    reg [7:0] stream    [0:MAX_BYTES-1];
    reg [7:0] exp_reply [0:MAX_REPLY-1];
    reg [7:0] exp_addr  [0:MAX_REPLY-1];
    reg [7:0] vals0     [0:MAX_VALS-1];     // values written to register 0, in order
    reg [7:0] vals1     [0:MAX_VALS-1];
    reg [7:0] model     [0:15];
    integer   n_stream = 0, n_exp = 0, n_vals0 = 1, n_vals1 = 1;
    integer   n_write = 0, n_read = 0;
    integer   k;
    initial begin
        for (k = 0; k < 16; k = k + 1) model[k] = 8'h00;
        vals0[0] = 8'h00;
        vals1[0] = 8'h00;
    end

    task put(input [7:0] b);
        begin
            if (n_stream < MAX_BYTES) stream[n_stream] = b;
            n_stream = n_stream + 1;
        end
    endtask

    task gen_write(input [7:0] addr, input [7:0] data);
        begin
            n_write = n_write + 1;
            put(OP_W); put(addr); put(data);
            if (addr < 16) model[addr] = data;
            if (addr == 0 && n_vals0 < MAX_VALS) begin vals0[n_vals0] = data; n_vals0 = n_vals0 + 1; end
            if (addr == 1 && n_vals1 < MAX_VALS) begin vals1[n_vals1] = data; n_vals1 = n_vals1 + 1; end
        end
    endtask

    task gen_read(input [7:0] addr);
        begin
            n_read = n_read + 1;
            put(OP_R); put(addr);
            if (n_exp < MAX_REPLY) begin
                exp_reply[n_exp] = (addr < 16) ? model[addr] : 8'h00;
                exp_addr[n_exp]  = addr;
            end
            n_exp = n_exp + 1;
        end
    endtask

    function [7:0] junk(input integer x);
        reg [7:0] b;
        begin
            b = x;
            if (b == OP_W || b == OP_R) b = b ^ 8'h01;
            junk = b;
        end
    endfunction

    function [7:0] rand_addr(input integer x);
        begin
            if ((x & 32'h70) == 32'h70) rand_addr = x >> 8;
            else                        rand_addr = x & 32'h0f;
        end
    endfunction

    task gen_random(input integer npk, input integer pct_read);
        integer j, nj, x;
        begin
            for (j = 0; j < npk; j = j + 1) begin
                x = $random(seed);
                if ((x & 32'h0f00) == 32'h0f00)
                    for (nj = 0; nj < 1 + ((x >> 12) & 3); nj = nj + 1)
                        put(junk($random(seed)));
                x = $random(seed);
                if ((x & 32'h7f) % 100 < pct_read) gen_read(rand_addr(x >> 8));
                else                               gen_write(rand_addr(x >> 8), $random(seed));
            end
        end
    endtask

    // ---- The producer (uart_rx's role): push the next byte whenever the
    // FIFO is not full, with probability push_pct. Decided between edges, so
    // `full` is exactly what the next edge sees.
    integer n_pushed = 0;
    integer push_pct = 100;
    integer full_cycles = 0;
    always @(negedge clk) begin
        wr_en = 1'b0;
        if (full) full_cycles = full_cycles + 1;
        if (n_pushed < n_stream && !full && ($random(seed) & 32'h7f) % 100 < push_pct) begin
            wr_en    = 1'b1;
            wr_data  = stream[n_pushed];
            n_pushed = n_pushed + 1;
        end
    end

    // ---- The transmitter (uart_tx's role).
    integer n_got = 0;
    integer busy_left = 0;
    integer busy_min = 2, busy_max = 2;
    always @(posedge clk) begin
        cyc = cyc + 1;
        if (tx_valid === 1'b1 && !tx_busy) begin
            if (n_got >= n_exp) begin
                $sformat(m, "reply %h sent, but no READ was waiting for one (%0d replies so far)", tx_data, n_got);
                err_msg(m);
            end else if (tx_data !== exp_reply[n_got]) begin
                $sformat(m, "reply %0d (READ %h): sent %h, expected %h", n_got, exp_addr[n_got], tx_data, exp_reply[n_got]);
                err_msg(m);
            end
            n_got     = n_got + 1;
            busy_left = busy_min + ((($random(seed) & 32'h7fff_ffff)) % (busy_max - busy_min + 1)) - 1;
            tx_busy  <= 1'b1;
        end else if (tx_busy) begin
            if (busy_left <= 0) tx_busy <= 1'b0;
            else                busy_left = busy_left - 1;
        end
        if (tx_valid !== 1'b0 && tx_valid !== 1'b1) err_msg("o_tx_valid is X or Z");
    end

    // ---- o_reg0 / o_reg1 must step through the values written, in order.
    integer p0 = 0, p1 = 0;
    reg     bad0 = 1'b0, bad1 = 1'b0;
    task follow(input integer which, input [7:0] v, inout integer p, inout reg bad);
        integer q;
        reg [7:0] cur;
        begin
            cur = (which == 0) ? vals0[p] : vals1[p];
            q = p + 1;
            while (q < ((which == 0) ? n_vals0 : n_vals1) &&
                   ((which == 0) ? vals0[q] : vals1[q]) === cur)
                q = q + 1;
            if (v === cur) begin
                bad = 1'b0;
            end else if (q < ((which == 0) ? n_vals0 : n_vals1) &&
                         v === ((which == 0) ? vals0[q] : vals1[q])) begin
                p = q;
                bad = 1'b0;
            end else begin
                if (!bad) begin
                    $sformat(m, "o_reg%0d = %h, expected %h (write #%0d to register %0d) or the next value written",
                             which, v, cur, p, which);
                    err_msg(m);
                end
                bad = 1'b1;
            end
        end
    endtask
    always @(negedge clk) begin
        if (cyc > 0) begin
            follow(0, reg0, p0, bad0);
            follow(1, reg1, p1, bad1);
        end
    end

    // Run until everything is pushed, parsed and answered.
    task run_phase(input integer limit);
        integer t0;
        begin
            t0 = cyc;
            while ((n_pushed < n_stream || !empty || n_got < n_exp || tx_busy) && cyc < t0 + limit)
                @(negedge clk);
            repeat (50) @(negedge clk);
            if (n_got != n_exp) begin
                $sformat(m, "phase ended with %0d of %0d replies sent", n_got, n_exp);
                err_msg(m);
                n_got = n_exp;                      // resynchronize for the next phase
            end
            if (reg0 !== model[0] || reg1 !== model[1]) begin
                $sformat(m, "after the phase o_reg0 = %h, o_reg1 = %h, expected %h, %h", reg0, reg1, model[0], model[1]);
                err_msg(m);
            end
        end
    endtask

    integer a, j;
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_reg_parser.vcd");
            $dumpvars(0, tb_reg_parser);
        end
        #1;
        if (tx_valid !== 1'b0) err_msg("o_tx_valid should start at 0");
        if (reg0 !== 8'h00)    err_msg("o_reg0 should start at 00");
        if (reg1 !== 8'h00)    err_msg("o_reg1 should start at 00");
        repeat (5) @(negedge clk);

        // 1. Power-up values, writes, read-back; transmitter always quick.
        push_pct = 30; busy_min = 2; busy_max = 2;
        for (a = 0; a < 16; a = a + 1) gen_read(a);
        gen_read(8'h10); gen_read(8'h80); gen_read(8'hff);
        for (a = 0; a < 16; a = a + 1) gen_write(a, 8'h11 * a ^ 8'h3c);
        for (a = 0; a < 16; a = a + 1) gen_read(a);
        run_phase(20000);

        // 2. Out of range, opcode values as operands, junk between packets.
        push_pct = 100; busy_min = 2; busy_max = 40;
        gen_write(8'h10, 8'hee); gen_write(8'h11, 8'hdd); gen_write(8'h1f, 8'hcc);
        gen_write(OP_W, 8'h01);  gen_write(8'hf0, 8'hbb);  gen_write(8'hff, 8'haa);
        for (a = 0; a < 16; a = a + 1) gen_read(a);
        gen_read(8'h10); gen_read(8'h1f);
        gen_write(8'h02, OP_W); gen_write(8'h03, OP_R); gen_write(8'h00, OP_R); gen_write(8'h01, OP_W);
        gen_read(8'h02); gen_read(8'h03); gen_read(OP_W); gen_read(OP_R); gen_read(8'h00); gen_read(8'h01);
        put(8'h00); put(8'hff); put(8'h77); put(8'h72); put(8'h0d); put(8'h0a);
        gen_write(8'h04, 8'h5a);
        put(8'h53);
        gen_read(8'h04);
        run_phase(20000);

        // 3. Random packets, a transmitter with moderate random delays.
        push_pct = 100; busy_min = 2; busy_max = 200;
        gen_random(400, 50);
        run_phase(400000);

        // 4. A very slow transmitter: the parser waits with each reply, the
        //    FIFO fills, and the producer is held off by o_full.
        push_pct = 100; busy_min = 3000; busy_max = 3000;
        gen_random(120, 60);
        for (j = 0; j < 30; j = j + 1) gen_read(j & 15);
        run_phase(1000000);

        // 5. Random again with a mix of short and long waits.
        push_pct = 70; busy_min = 1; busy_max = 600;
        gen_random(300, 50);
        gen_write(8'h00, 8'h05); gen_write(8'h01, 8'h5a);
        gen_read(8'h00); gen_read(8'h01);
        run_phase(400000);

        if (full_cycles < 100) err_msg("internal: the FIFO never filled up");
        if (n_stream > MAX_BYTES || n_exp > MAX_REPLY) err_msg("internal: stream too long");

        if (errors == 0)
            $display("PASS tb_reg_parser: %0d WRITEs and %0d READs (%0d bytes), every reply correct, FIFO full for %0d clocks while the transmitter stalled",
                     n_write, n_read, n_stream, full_cycles);
        else
            $display("FAIL tb_reg_parser: %0d error(s), %0d of %0d replies sent", errors, n_got, n_exp);
        $finish;
    end

    // Watchdog: the run is about 400,000 clocks (4 ms); a parser that hangs
    // is caught by the phase limits first.
    initial begin
        #40_000_000;
        $display("FAIL tb_reg_parser: timeout, %0d of %0d bytes pushed, %0d of %0d replies sent",
                 n_pushed, n_stream, n_got, n_exp);
        $finish;
    end
endmodule

`default_nettype wire
