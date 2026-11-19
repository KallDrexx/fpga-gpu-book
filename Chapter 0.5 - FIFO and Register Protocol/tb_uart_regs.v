// tb_uart_regs -- self-checking testbench for Step 0.5 (FIFO and register
// protocol): the register interface behind the UART.
//
// Copy to sim/tb_uart_regs.v. Expects a module `uart_regs` in rtl/ (for
// example rtl/common/uart_regs.v), built from your uart_rx, fifo, parser and
// uart_tx:
//
//   module uart_regs #(parameter integer CLK_HZ = 25_000_000,
//                      parameter integer BAUD   = 115_200)
//                     (input  wire       i_clk,
//                      input  wire       i_rx,       // raw serial line from the PC
//                      output wire       o_tx,       // serial line to the PC
//                      output wire [7:0] o_reg0,     // register 0 (LEDs); reg is fine too
//                      output wire [7:0] o_reg1);    // register 1 (display)
//
// The protocol (all values are single bytes):
//   WRITE: 0x57 ('W'), addr, data   -- no reply
//   READ:  0x52 ('R'), addr         -- reply: one byte, the register's value
// There are 16 registers, addresses 0x00 to 0x0F, all 00 at power-up.
// Writes to addresses 0x10 and up are ignored; reads of them return 00. A
// byte that arrives when an opcode is expected and is neither 'W' nor 'R' is
// skipped.
//
// Runs with CLK_HZ = 1.6 MHz and BAUD = 100,000, so CPB = 16 clocks per bit.
// The testbench is the PC: it drives i_rx asynchronously at the nominal bit
// time and decodes o_tx. It checks:
//   * o_tx is 1 and o_reg0 = o_reg1 = 00 at time zero, never X or Z.
//   * Every READ gets exactly one reply, in order, holding the value of the
//     register after all earlier WRITEs; WRITEs and skipped bytes get none.
//   * Addresses 0x10 and up do not alias onto registers 0 to 15.
//   * Bytes equal to 'W' or 'R' used as an address or data are not mistaken
//     for opcodes.
//   * o_reg0 and o_reg1 change only when registers 0 and 1 are written, and
//     show the new value within one bit time after the end of the WRITE's
//     last byte.
//   * All of that holds in long bursts of packets sent back-to-back.
//
// Prints exactly one PASS or FAIL line, then $finish. Run with +vcd to dump
// tb_uart_regs.vcd.

`timescale 1ns/1ps
`default_nettype none

module tb_uart_regs;
    localparam integer CLK_HZ    = 1_600_000;
    localparam integer BAUD      = 100_000;
    localparam integer CPB       = CLK_HZ / BAUD;  // 16
    localparam real    BIT_NS    = CPB * 10.0;
    localparam integer MAX_REPLY = 4096;
    localparam integer MAX_SHOWN = 6;
    localparam [7:0]   OP_W      = 8'h57;
    localparam [7:0]   OP_R      = 8'h52;

    reg clk = 1'b0;
    always #5 clk = ~clk;

    reg        rx = 1'b1;
    wire       tx;
    wire [7:0] reg0, reg1;

    uart_regs #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) dut (
        .i_clk(clk), .i_rx(rx), .o_tx(tx), .o_reg0(reg0), .o_reg1(reg1)
    );

    integer   errors  = 0;
    integer   seed    = 505;
    integer   cyc     = 0;
    reg [7:0] model [0:15];                 // what each register should hold
    reg [7:0] exp_reply [0:MAX_REPLY-1];    // replies the PC is waiting for
    reg [7:0] exp_addr  [0:MAX_REPLY-1];
    integer   n_exp   = 0;
    integer   n_got   = 0;
    integer   n_bytes = 0;
    integer   n_write = 0;
    integer   n_read  = 0;
    integer   last_rx_cyc = 0;

    task err_msg(input [8*100-1:0] msg);
        begin
            if (errors < MAX_SHOWN) $display("  t=%0t: %0s", $time, msg);
            errors = errors + 1;
        end
    endtask

    always @(posedge clk) cyc = cyc + 1;

    // ---- o_reg0 / o_reg1 must always show the old or the new value of their
    // register, and the new one once the deadline has passed.
    reg [7:0] out_old [0:1];
    reg [7:0] out_new [0:1];
    integer   out_due [0:1];
    integer   r;
    reg [8*100-1:0] m;
    initial begin
        for (r = 0; r < 16; r = r + 1) model[r] = 8'h00;
        for (r = 0; r < 2; r = r + 1) begin
            out_old[r] = 8'h00; out_new[r] = 8'h00; out_due[r] = 0;
        end
    end
    reg       out_bad [0:1];                // report each divergence once
    initial begin out_bad[0] = 1'b0; out_bad[1] = 1'b0; end
    always @(negedge clk) begin : watch_outputs
        integer j;
        reg [7:0] v;
        reg       ok;
        for (j = 0; j < 2; j = j + 1) begin
            v  = (j == 0) ? reg0 : reg1;
            ok = (v === out_new[j]) || (cyc < out_due[j] && v === out_old[j]);
            if (!ok && !out_bad[j]) begin
                if (^v === 1'bx)
                    $sformat(m, "o_reg%0d is X or Z", j);
                else if (cyc < out_due[j])
                    $sformat(m, "o_reg%0d = %h, expected %h or %h", j, v, out_old[j], out_new[j]);
                else
                    $sformat(m, "o_reg%0d = %h, expected %h (register %0d)", j, v, out_new[j], j);
                err_msg(m);
            end
            out_bad[j] = !ok;
        end
    end

    // ---- Decode o_tx, the way the PC's UART would.
    reg [7:0] got;
    integer   i;
    initial begin
        #1;
        if (tx !== 1'b1)    err_msg("o_tx should start at 1 (idle)");
        if (reg0 !== 8'h00) err_msg("o_reg0 should start at 00");
        if (reg1 !== 8'h00) err_msg("o_reg1 should start at 00");
        forever begin
            @(negedge clk);
            if (tx !== 1'b0 && tx !== 1'b1) begin
                err_msg("o_tx is X or Z");
                @(posedge tx);
            end else if (tx === 1'b0) begin
                repeat (CPB / 2 - 1) @(negedge clk);            // middle of the start bit
                if (tx !== 1'b0) err_msg("start bit on o_tx shorter than half a bit");
                for (i = 0; i < 8; i = i + 1) begin
                    repeat (CPB) @(negedge clk);
                    got[i] = tx;
                end
                repeat (CPB) @(negedge clk);
                if (tx !== 1'b1) begin
                    $sformat(m, "reply %0d: stop bit is %b, expected 1", n_got, tx);
                    err_msg(m);
                end
                if (n_got >= n_exp) begin
                    $sformat(m, "reply byte %h arrived, but no READ was waiting for one (%0d replies so far)", got, n_got);
                    err_msg(m);
                end else if (got !== exp_reply[n_got]) begin
                    $sformat(m, "reply %0d (READ %h): got %h, expected %h", n_got, exp_addr[n_got], got, exp_reply[n_got]);
                    err_msg(m);
                end
                n_got       = n_got + 1;
                last_rx_cyc = cyc;
                while (tx === 1'b0) @(negedge clk);
            end
        end
    end

    // ---- The PC's side: bytes and packets.
    task send_byte(input [7:0] b);
        integer k;
        begin
            n_bytes = n_bytes + 1;
            rx = 1'b0; #(BIT_NS);
            for (k = 0; k < 8; k = k + 1) begin
                rx = b[k]; #(BIT_NS);
            end
            rx = 1'b1; #(BIT_NS);
        end
    endtask

    task gap(input real bits);
        #(bits * BIT_NS);
    endtask

    task send_write(input [7:0] addr, input [7:0] data);
        begin
            n_write = n_write + 1;
            send_byte(OP_W);
            send_byte(addr);
            if (addr < 2) begin
                // From now on the output may show the new value; one bit time
                // after this byte's stop bit it must.
                out_old[addr] = out_new[addr];
                out_new[addr] = data;
                out_due[addr] = cyc + 11 * CPB + 2;
            end
            send_byte(data);
            if (addr < 16) model[addr] = data;
        end
    endtask

    task send_read(input [7:0] addr);
        begin
            n_read = n_read + 1;
            if (n_exp < MAX_REPLY) begin
                exp_reply[n_exp] = (addr < 16) ? model[addr] : 8'h00;
                exp_addr[n_exp]  = addr;
                n_exp = n_exp + 1;
            end
            send_byte(OP_R);
            send_byte(addr);
        end
    endtask

    // A byte that is neither 'W' nor 'R'.
    function [7:0] junk(input integer x);
        reg [7:0] b;
        begin
            b = x;
            if (b == OP_W || b == OP_R) b = b ^ 8'h01;
            junk = b;
        end
    endfunction

    // Mostly 0..15, sometimes out of range.
    function [7:0] rand_addr(input integer x);
        begin
            if ((x & 32'h70) == 32'h70) rand_addr = x >> 8;
            else                        rand_addr = x & 32'h0f;
        end
    endfunction

    task wait_drain;
        begin
            while (n_got < n_exp && cyc < last_rx_cyc + 40 * CPB) #(BIT_NS);
            #(3 * BIT_NS);
        end
    endtask

    task read_all_spaced;
        integer a;
        begin
            for (a = 0; a < 16; a = a + 1) begin
                send_read(a);
                gap(1.0 + (($random(seed) & 15) / 4.0));
            end
            wait_drain;
        end
    endtask

    integer a, bu, j, nj;
    integer x;
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_uart_regs.vcd");
            $dumpvars(0, tb_uart_regs);
        end
        last_rx_cyc = 0;
        #(4 * BIT_NS + 2.71);                           // idle, off the clock grid

        // Power-up values, and reads of addresses that do not exist.
        read_all_spaced;
        send_read(8'h10); gap(2); send_read(8'h52); gap(2);
        send_read(8'h80); gap(2); send_read(8'hff); gap(2);
        wait_drain;

        // Write every register, spaced out, then read them all back.
        for (a = 0; a < 16; a = a + 1) begin
            send_write(a, 8'h11 * a ^ 8'ha5);
            gap(1.5 + (($random(seed) & 15) / 4.0));
        end
        read_all_spaced;

        // Out-of-range writes must not alias onto registers 0..15.
        send_write(8'h10, 8'h3c); gap(2);
        send_write(8'h11, 8'hc3); gap(2);
        send_write(8'h1f, 8'h77); gap(2);
        send_write(8'h57, 8'h52); gap(2);
        send_write(8'hf0, 8'h99); gap(2);
        send_write(8'hff, 8'h01); gap(2);
        send_read(8'h10); gap(2); send_read(8'h1f); gap(2);
        read_all_spaced;

        // Opcode values used as addresses and data.
        send_write(8'h02, OP_W); send_write(8'h03, OP_R);
        send_write(8'h00, OP_R); send_write(8'h01, OP_W);
        send_read(8'h02); send_read(8'h03); send_read(OP_W); send_read(OP_R);
        send_read(8'h00); send_read(8'h01);
        wait_drain;

        // Junk bytes between packets are skipped.
        send_byte(8'h00); send_byte(8'hff); send_byte(8'h0d); send_byte(8'h0a);
        send_write(8'h04, 8'h5a);
        send_byte(8'h77); send_byte(8'h72);             // 'w', 'r': not opcodes
        send_read(8'h04);
        send_byte(8'h53);
        send_write(8'h00, 8'h0f);
        send_read(8'h00);
        wait_drain;

        // Back-to-back bursts of random packets, with some junk mixed in.
        for (bu = 0; bu < 6; bu = bu + 1) begin
            for (j = 0; j < 60; j = j + 1) begin
                x = $random(seed);
                if ((x & 32'h0f00) == 32'h0f00)
                    for (nj = 0; nj < 1 + ((x >> 12) & 3); nj = nj + 1)
                        send_byte(junk($random(seed)));
                x = $random(seed);
                if (x & 1) send_write(rand_addr(x >> 1), $random(seed));
                else       send_read(rand_addr(x >> 1));
            end
            wait_drain;
        end

        // A burst of reads only: the replies keep the transmitter busy half
        // the time.
        for (j = 0; j < 100; j = j + 1) send_read(j & 15);
        wait_drain;

        // Leave the LEDs showing 0101 and the display showing 0x5A.
        send_write(8'h00, 8'h05);
        send_write(8'h01, 8'h5a);
        send_read(8'h00); send_read(8'h01);
        wait_drain;
        #(20 * BIT_NS);

        if (n_got != n_exp) begin
            $sformat(m, "%0d READs sent, %0d replies received", n_exp, n_got);
            err_msg(m);
        end
        if (errors == 0)
            $display("PASS tb_uart_regs: %0d WRITEs and %0d READs (%0d bytes), every reply correct; o_reg0 = %h, o_reg1 = %h",
                     n_write, n_read, n_bytes, reg0, reg1);
        else
            $display("FAIL tb_uart_regs: %0d error(s), %0d of %0d replies received", errors, n_got, n_exp);
        $finish;
    end

    // Watchdog: the stimulus is about 15,000 bit times (2.4 ms); allow about
    // four times that.
    initial begin
        #10_000_000;
        $display("FAIL tb_uart_regs: timeout, %0d bytes sent, %0d of %0d replies received", n_bytes, n_got, n_exp);
        $finish;
    end
endmodule

`default_nettype wire
