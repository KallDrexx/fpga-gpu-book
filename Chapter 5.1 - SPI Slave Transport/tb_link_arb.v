// tb_link_arb.v -- self-checking testbench for the link rules of gpu_link (Chapter 5.1).
//
// Small and fast: it drives a few commands over the UART and over SPI into one
// gpu_link and checks the rules that Chapter 5.1 sets for sharing the FIFO and
// for reading replies over SPI:
//
//   1. A UART session works as in Phase 4 (CLEAR, PLOT, FB_CRC, reply on o_tx).
//   2. An SPI session works too. Its FB_CRC reply comes back in a read frame
//      (first byte 0xFF), not on o_tx: status with REPLY set, count 4, the
//      four bytes, then 0x00. A second read frame shows count 0.
//   3. A read frame's MOSI bytes never reach the parser (FF 00 02 01 77 would
//      be CLEAR 77), while 0xFF later in a command frame is an ordinary byte.
//   4. A UART byte that arrives while CS is low is dropped; the SPI frame is
//      not disturbed.
//   5. After UART commands, the next reply goes to the UART again.
//   6. A command frame returns status and count but no reply bytes, and
//      leaves the queue alone.
//   7. Five FB_CRC replies are left unread: the queue holds four and the
//      engine waits with the fifth. The host then sends one byte at a time
//      while READY is high, so the FIFO fills until READY falls; READY must
//      have promised room for 257 bytes each time it was high. Read frames
//      still work while READY is low; a 4-byte one takes 2 reply bytes, the
//      next ones take the rest; the engine then resumes and READY comes back.
//      Nothing is lost.
//   8. Sending 600 bytes without looking at READY while FB_CRCs run sets
//      o_overflow and the OVERFLOW bit of the status byte.
//
// It models the framebuffers like the other Phase 4/5 testbenches, so it
// computes the expected CRCs itself.
`timescale 1ns/1ps
`default_nettype none

module tb_link_arb;
    localparam integer FBN = 76800;
    localparam integer BIT_NS = 400;                       // 2.5 Mbaud

    reg clk = 1'b0;
    always #10 clk = ~clk;

    reg  rst = 1'b1, rx = 1'b1, sck = 1'b0, mosi = 1'b0, cs_n = 1'b1;
    wire tx, miso, ready, overflow, fb_we, pal_we, fb_buf, flip_req;
    reg  [3:0] ack_pipe = 4'd0;
    wire flip_ack = ack_pipe[3];
    wire [16:0] fb_addr; wire [7:0] fb_wdata, pal_addr; wire [23:0] pal_wdata;
    reg  [7:0] fb_rdata = 8'd0;
    gpu_link #(.CLK_HZ(50_000_000), .BAUD(2_500_000)) dut (
        .i_clk(clk), .i_rst(rst), .i_rx(rx), .o_tx(tx),
        .i_sck(sck), .i_mosi(mosi), .i_cs_n(cs_n), .o_miso(miso), .i_vblank(1'b0),
        .o_ready(ready), .o_overflow(overflow),
        .o_fb_we(fb_we), .o_fb_addr(fb_addr), .o_fb_wdata(fb_wdata), .i_fb_rdata(fb_rdata),
        .o_pal_we(pal_we), .o_pal_addr(pal_addr), .o_pal_wdata(pal_wdata),
        .o_fb_buf(fb_buf), .o_flip_req(flip_req), .i_flip_ack(flip_ack));
    always @(posedge clk) ack_pipe <= {ack_pipe[2:0], flip_req};

    reg [7:0] fb0 [0:FBN-1];
    reg [7:0] fb1 [0:FBN-1];
    integer x_we = 0;
    always @(posedge clk) begin
        if (!rst && fb_we === 1'bx) x_we = x_we + 1;
        if (fb_we === 1'b1) begin
            if (fb_buf) fb1[fb_addr] <= fb_wdata; else fb0[fb_addr] <= fb_wdata;
            fb_rdata <= fb_wdata;
        end else
            fb_rdata <= fb_buf ? fb1[fb_addr] : fb0[fb_addr];
    end
    function [31:0] back_crc(input integer dummy);
        integer a, j; reg [31:0] c;
        begin
            c = 32'hFFFFFFFF;
            for (a = 0; a < FBN; a = a + 1) begin
                c = c ^ {24'd0, (flip_ack ? fb0[a] : fb1[a])};
                for (j = 0; j < 8; j = j + 1) c = c[0] ? (c >> 1) ^ 32'hEDB88320 : (c >> 1);
            end
            back_crc = c ^ 32'hFFFFFFFF;
        end
    endfunction

    // ---- UART side ----
    reg [7:0] ug [0:63];
    integer n_ug = 0, k, errs = 0;
    reg [7:0] b;
    always begin
        @(negedge tx);
        #(BIT_NS / 2);
        for (k = 0; k < 8; k = k + 1) begin #(BIT_NS); b[k] = tx; end
        #(BIT_NS);
        ug[n_ug % 64] = b; n_ug = n_ug + 1;
        #(BIT_NS / 4);
    end
    task uart_byte(input [7:0] d);
        integer j;
        begin
            rx = 1'b0; #(BIT_NS);
            for (j = 0; j < 8; j = j + 1) begin rx = d[j]; #(BIT_NS); end
            rx = 1'b1; #(BIT_NS);
        end
    endtask

    // ---- SPI side (mode 0, 5 MHz) ----
    reg [7:0] mtx [0:255];                                 // bytes to send in a frame
    reg [7:0] mrx [0:255];                                 // bytes MISO returned
    task spi_byte(input integer idx, input [7:0] d);
        integer j;
        begin
            for (j = 7; j >= 0; j = j - 1) begin
                #5 mosi = d[j];
                #85 mrx[idx][j] = miso;
                #10 sck = 1'b1;
                #100 sck = 1'b0;
            end
        end
    endtask
    task spi_frame(input integer n);                       // one CS frame of mtx[0..n-1]
        integer j;
        begin
            cs_n = 1'b0; #300;
            for (j = 0; j < n; j = j + 1) spi_byte(j, mtx[j]);
            cs_n = 1'b1; #300;
        end
    endtask
    task wait_ready; begin while (ready !== 1'b1) #100; end endtask
    task spi_cmd(input [7:0] op, input [7:0] len, input [7:0] a0);   // op len [a0], no payload when len == 0
        begin
            wait_ready;
            mtx[0] = op; mtx[1] = len; mtx[2] = a0;
            spi_frame(len != 0 ? 3 : 2);
        end
    endtask
    task spi_read(input integer n);                        // a read frame: FF, then n-1 more bytes
        integer j;
        begin
            mtx[0] = 8'hFF;
            for (j = 1; j < n; j = j + 1) mtx[j] = 8'h00;     // odd counts would desync the parser if they counted
            spi_frame(n);
        end
    endtask

    reg [31:0] crc, got4;
    integer i, t0, n_r, n_fill;
    reg [7:0] rb [0:63];
    task check(input [255:0] what, input ok);
        begin
            if (!ok) begin
                if (errs < 5) $display("tb_link_arb: %0s", what);
                errs = errs + 1;
            end
        end
    endtask
    task check4(input [255:0] what, input [7:0] b0, input [7:0] b1, input [7:0] b2, input [7:0] b3, input [31:0] want);
        begin
            got4 = {b3, b2, b1, b0};
            if (got4 !== want) begin
                if (errs < 5) $display("tb_link_arb: %0s: reply %h, expected %h", what, got4, want);
                errs = errs + 1;
            end
        end
    endtask
    task check_status(input [255:0] what, input [7:0] st, input reply);
        begin
            if (st[7:6] !== 2'b01 || st[5] !== 1'b0 || st[1] !== 1'b0 || st[2] !== reply) begin
                if (errs < 5) $display("tb_link_arb: %0s: status %h, expected 01 0 x x %b 0 x", what, st, reply);
                errs = errs + 1;
            end
        end
    endtask

    initial begin
        for (i = 0; i < FBN; i = i + 1) begin fb0[i] = 8'd0; fb1[i] = 8'd0; end
        if ($test$plusargs("vcd")) begin $dumpfile("tb_link_arb.vcd"); $dumpvars(0, tb_link_arb); end
        repeat (10) @(negedge clk); rst = 1'b0; repeat (50) @(negedge clk);

        // 1. UART: CLEAR 07, PLOT (10,20)=5A, FB_CRC
        uart_byte(8'h02); uart_byte(8'h01); uart_byte(8'h07);
        uart_byte(8'h03); uart_byte(8'h05); uart_byte(8'd10); uart_byte(8'd0); uart_byte(8'd20); uart_byte(8'd0); uart_byte(8'h5A);
        uart_byte(8'h04); uart_byte(8'h00);
        #5_000_000;
        crc = back_crc(0);
        check("1: UART gave no 4-byte reply", n_ug == 4);
        check4("1: UART FB_CRC", ug[0], ug[1], ug[2], ug[3], crc);

        // 2. SPI: CLEAR 09, FB_CRC, wait, read
        spi_cmd(8'h02, 8'h01, 8'h09);
        spi_cmd(8'h04, 8'h00, 8'h00);
        #5_000_000;
        spi_read(8);
        crc = back_crc(0);
        check_status("2: read frame with a reply waiting", mrx[0], 1'b1);
        check("2: OVERFLOW set", mrx[0][4] === 1'b0);
        check("2: count byte is not 4", mrx[1] === 8'd4);
        check4("2: SPI FB_CRC", mrx[2], mrx[3], mrx[4], mrx[5], crc);
        check("2: bytes after reply not 00", mrx[6] === 8'h00 && mrx[7] === 8'h00);
        check("2: SPI reply came out of o_tx", n_ug == 4);
        spi_read(5);
        check_status("2: second read frame", mrx[0], 1'b0);
        check("2: reply still counted", mrx[1] === 8'd0 && mrx[2] === 8'h00 && mrx[3] === 8'h00);

        // 3. read frames don't reach the parser; 0xFF inside a command frame does
        mtx[0] = 8'hFF; mtx[1] = 8'h00; mtx[2] = 8'h02; mtx[3] = 8'h01; mtx[4] = 8'h77; spi_frame(5);   // not CLEAR 77
        wait_ready;
        mtx[0] = 8'h03; mtx[1] = 8'h05; mtx[2] = 8'd7; mtx[3] = 8'd0; mtx[4] = 8'd9; mtx[5] = 8'd0; mtx[6] = 8'hFF;
        spi_frame(7);                                                                    // PLOT (7,9) = FF
        spi_cmd(8'h04, 8'h00, 8'h00);
        #5_000_000;
        spi_read(6);
        crc = back_crc(0);
        check("3: read frame was drawn", (flip_ack ? fb0[0] : fb1[0]) === 8'h09);
        check("3: PLOT color FF not drawn", (flip_ack ? fb0[9*320+7] : fb1[9*320+7]) === 8'hFF);
        check4("3: after a read frame and a PLOT", mrx[2], mrx[3], mrx[4], mrx[5], crc);

        // 4. a UART byte during an SPI frame is dropped; the frame still works
        wait_ready;
        mtx[0] = 8'h02; mtx[1] = 8'h01; mtx[2] = 8'h33;                                 // CLEAR 33
        fork
            spi_frame(3);
            begin #800; uart_byte(8'h03); end                                            // while CS is low
        join
        spi_cmd(8'h04, 8'h00, 8'h00);
        #5_000_000;
        spi_read(6);
        crc = back_crc(0);
        check4("4: UART byte during SPI frame", mrx[2], mrx[3], mrx[4], mrx[5], crc);

        // 5. UART again: the reply comes out of the UART
        uart_byte(8'h02); uart_byte(8'h01); uart_byte(8'h44);
        uart_byte(8'h04); uart_byte(8'h00);
        #5_000_000;
        crc = back_crc(0);
        check("5: no UART reply after SPI", n_ug == 8);
        if (n_ug == 8) check4("5: UART after SPI", ug[4], ug[5], ug[6], ug[7], crc);

        // 6. command frames carry status and count, no reply bytes, and remove nothing
        spi_cmd(8'h04, 8'h00, 8'h00);
        #3_000_000;
        wait_ready;
        for (i = 0; i < 6; i = i + 1) mtx[i] = 8'h00;                                    // three NOPs
        spi_frame(6);
        check_status("6: command frame with a reply waiting", mrx[0], 1'b1);
        check("6: command frame: count not 4", mrx[1] === 8'd4);
        check("6: command frame gave replies", mrx[2] === 8'h00 && mrx[3] === 8'h00 && mrx[4] === 8'h00 && mrx[5] === 8'h00);
        spi_read(6);
        check("6: command frame took reply", mrx[1] === 8'd4);
        check4("6: reply after a command frame", mrx[2], mrx[3], mrx[4], mrx[5], crc);

        // 7. five unread replies, the host keeps sending: READY falls; read frames still work
        spi_cmd(8'h02, 8'h01, 8'h55);
        for (i = 0; i < 5; i = i + 1) spi_cmd(8'h04, 8'h00, 8'h00);
        #12_000_000;                                    // the engine now waits with the fifth reply
        n_fill = 0;                                     // NOPs, one byte per frame, while READY is high
        while (ready === 1'b1 && n_fill < 600) begin mtx[0] = 8'h00; spi_frame(1); n_fill = n_fill + 1; end
        if (n_fill % 2) begin mtx[0] = 8'h00; spi_frame(1); end              // keep the NOPs whole
        check("7: READY high with <257 free", n_fill <= 256);               // n_fill - 1 bytes were queued when READY was last high
        crc = back_crc(0);
        check("7: READY did not fall", ready === 1'b0);
        #2_000_000;
        check("7: READY rose, replies unread", ready === 1'b0);
        n_r = 0;
        spi_read(4);                                                                     // 2 reply bytes
        check("7: full queue: count not 16", mrx[1] === 8'd16);
        rb[n_r] = mrx[2]; rb[n_r+1] = mrx[3]; n_r = n_r + 2;
        t0 = $time;
        while (n_r < 20 && $time - t0 < 10_000_000) begin  // the rest, in frames of up to 16 reply bytes
            #200_000;
            spi_read(18);
            check("7: count byte above 16", mrx[1] <= 8'd16);
            for (i = 0; i < mrx[1] && i < 16; i = i + 1) if (n_r + i < 64) rb[n_r + i] = mrx[2 + i];
            n_r = n_r + mrx[1];
        end
        check("7: not exactly 20 reply bytes", n_r == 20);
        for (i = 0; i < 5; i = i + 1) check4("7: queued reply", rb[4*i], rb[4*i+1], rb[4*i+2], rb[4*i+3], crc);
        t0 = $time;
        while (ready !== 1'b1 && $time - t0 < 10_000_000) #1000;
        check("7: READY did not come back", ready === 1'b1);
        check("7: o_overflow rose", overflow === 1'b0);

        // 8. no READY, no mercy: 600 bytes behind five FB_CRCs overflow the FIFO
        for (i = 0; i < 5; i = i + 1) spi_cmd(8'h04, 8'h00, 8'h00);
        for (i = 0; i < 200; i = i + 1) mtx[i] = 8'h00;
        for (k = 0; k < 3; k = k + 1) spi_frame(200);
        spi_read(2);
        check("8: o_overflow did not rise", overflow === 1'b1);
        check("8: OVERFLOW bit not set", mrx[0][4] === 1'b1);

        if (x_we != 0)
            $display("FAIL: tb_link_arb (%0d X write enables)", x_we);
        else if (errs != 0)
            $display("FAIL: tb_link_arb (%0d checks failed, see above)", errs);
        else
            $display("PASS: tb_link_arb (UART session, SPI session and read frames, dropped UART byte, UART again, command frames, 5 unread replies, READY fell after %0d bytes, overflow flag)", n_fill);
        $finish;
    end

    initial begin #400_000_000; $display("FAIL: tb_link_arb watchdog"); $finish; end
endmodule
`default_nettype wire
