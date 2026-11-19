// tb_spi_slave.v -- self-checking testbench for spi_slave (Chapter 5.1).
//
// The testbench is an SPI master (mode 0) with its own, unrelated clock: every
// frame starts at a random phase against the 50 MHz clock and every SCK edge
// gets random jitter, so the slave's synchronizers see edges at all alignments.
// It checks, for SCK rates from 0.5 MHz up to 6.25 MHz (half period 80 ns):
//
//   * every complete byte sent arrives, in order, exactly once (o_rx_*), before
//     the next byte's first bit and before the end of the 100 ns CS-high gap;
//     a frame cut off after 1 to 7 extra bits delivers nothing for them and
//     does not disturb the next frame (CS resets the bit counter);
//   * MISO carries the bytes the testbench offers on i_tx_data. While CS is
//     high, i_tx_data holds a value that is new for each frame; it is byte 0.
//     One clock after each o_tx_take the testbench changes i_tx_data to the
//     next byte, so byte k is what i_tx_data showed at the end of byte k-1;
//   * the master samples MISO 10 ns before every SCK rising edge, as a master
//     with a 10 ns input setup time would;
//   * MISO never changes while SCK is high (it changes on falling edges only);
//   * o_tx_take pulses exactly once per byte whose first bit was clocked;
//   * o_frame is high by the first SCK edge and low by the end of the CS gap;
//   * CS may rise right after the last SCK edge (even in the same instant).
`timescale 1ns/1ps
`default_nettype none

module tb_spi_slave;
    reg clk = 1'b0;
    always #10 clk = ~clk;

    reg  sck = 1'b0, mosi = 1'b0, cs_n = 1'b1;
    wire miso, rx_valid, frame, take;
    wire [7:0] rx_data;

    // what the slave is asked to send: `idle` (new for every frame) until the
    // frame's first o_tx_take, then f(k) for byte k
    integer nt = 0;
    reg [7:0] idle = 8'h5A;
    function [7:0] f(input integer k);
        f = (k * 37 + 11) & 8'hFF;
    endfunction
    wire [7:0] tx_data = (nt == 0) ? idle : f(nt);
    always @(posedge clk) begin
        if (cs_n) nt <= 0;
        else if (take) nt <= nt + 1;
    end

    spi_slave dut (.i_clk(clk), .i_sck(sck), .i_mosi(mosi), .i_cs_n(cs_n), .o_miso(miso),
        .o_rx_valid(rx_valid), .o_rx_data(rx_data), .o_frame(frame),
        .i_tx_data(tx_data), .o_tx_take(take));

    // ---- monitors ----
    reg [7:0] got [0:65535];
    integer n_got = 0, n_take = 0, early_err = 0, frame_err = 0;
    integer n_sent = 0;
    always @(posedge clk) begin
        if (rx_valid) begin got[n_got % 65536] = rx_data; n_got = n_got + 1; end
        if (take) begin
            n_take = n_take + 1;
            if (n_got != n_sent) begin                   // the previous byte must be out by now
                if (early_err == 0)
                    $display("tb_spi_slave: o_tx_take at %0t, but only %0d of the %0d complete bytes were delivered (o_rx_valid too late?)", $time, n_got, n_sent);
                early_err = early_err + 1;
            end
        end
    end
    integer miso_bad = 0;
    always @(miso)
        if (sck === 1'b1) begin
            if (miso_bad == 0) $display("tb_spi_slave: MISO changed while SCK was high at %0t", $time);
            miso_bad = miso_bad + 1;
        end

    // ---- master ----
    localparam real MOSI_DLY = 5.0, MISO_SETUP = 10.0;
    real half;
    integer miso_err = 0;
    reg [7:0] sent [0:65535];
    integer exp_take = 0;

    function real jit(input integer maxns);
        jit = $urandom_range(0, maxns * 10) / 10.0;
    endfunction

    // one byte; k is its index in the frame (for the expected MISO byte)
    task xfer(input [7:0] tx, input integer k, input integer nbits);
        integer i;
        reg [7:0] exp_m, rxm;
        reg s;
        begin
            exp_m = (k == 0) ? idle : f(k);
            for (i = 7; i > 7 - nbits; i = i - 1) begin
                #(MOSI_DLY) mosi = tx[i];
                #(half - MOSI_DLY - MISO_SETUP + jit(20)) s = miso;
                rxm[i] = s;
                #(MISO_SETUP) sck = 1'b1;
                #(half + jit(20)) sck = 1'b0;
            end
            if (nbits == 8 && rxm !== exp_m) begin
                if (miso_err == 0)
                    $display("tb_spi_slave: MISO byte %0d of a frame was %h, expected %h (half period %0.0f ns)", k, rxm, exp_m, half);
                miso_err = miso_err + 1;
            end
        end
    endtask

    integer rates [0:6];
    integer tails [0:3];
    integer fr, nb, extra, b, tail, late_err = 0;
    initial begin
        rates[0] = 80;  rates[1] = 100; rates[2] = 125; rates[3] = 167;
        rates[4] = 250; rates[5] = 500; rates[6] = 1000;
        tails[0] = 0; tails[1] = 2; tails[2] = 20; tails[3] = 100;
        if ($test$plusargs("vcd")) begin $dumpfile("tb_spi_slave.vcd"); $dumpvars(0, tb_spi_slave); end
        #(1000 + jit(20));
        for (fr = 0; fr < 4000; fr = fr + 1) begin
            half  = rates[$urandom_range(0, 6)];
            nb    = $urandom_range(1, 6);
            extra = ($urandom_range(0, 3) == 0) ? $urandom_range(1, 7) : 0;
            tail  = tails[$urandom_range(0, 3)];
            cs_n = 1'b0;
            #(100 + jit(200));                           // CS to first SCK: at least 100 ns
            if (frame !== 1'b1) begin
                if (frame_err == 0) $display("tb_spi_slave: o_frame is not high 100 ns after CS fell (frame %0d)", fr);
                frame_err = frame_err + 1;
            end
            for (b = 0; b < nb; b = b + 1) begin
                sent[n_sent % 65536] = $urandom;
                xfer(sent[n_sent % 65536], b, 8);
                n_sent = n_sent + 1;
            end
            if (extra != 0) xfer($urandom, nb, extra);   // a partial byte, thrown away
            #(tail) cs_n = 1'b1;
            idle = $urandom;                             // byte 0 of the next frame
            #(100 + $urandom_range(0, 1500));            // CS high at least 100 ns
            if (frame !== 1'b0) begin
                if (frame_err == 0) $display("tb_spi_slave: o_frame is still high 100 ns after CS rose (frame %0d)", fr);
                frame_err = frame_err + 1;
            end
            exp_take = exp_take + nb + (extra != 0);
            if (n_got != n_sent) begin                   // every byte is out by the end of the CS-high gap
                if (late_err == 0)
                    $display("tb_spi_slave: frame %0d: %0d bytes sent so far, only %0d delivered by the end of the CS gap (a byte held back or lost at CS rise?)", fr, n_sent, n_got);
                late_err = late_err + 1;
            end
        end
        #2000;
        begin : final_check
            integer i, bad;
            bad = 0;
            for (i = 0; i < n_got && i < n_sent; i = i + 1)
                if (got[i % 65536] !== sent[i % 65536]) begin
                    if (bad == 0) $display("tb_spi_slave: received byte %0d is %h, sent %h", i, got[i % 65536], sent[i % 65536]);
                    bad = bad + 1;
                end
            if (n_got != n_sent || late_err != 0 || early_err != 0)
                $display("FAIL: tb_spi_slave (sent %0d bytes, received %0d; %0d frames ended with a byte missing, %0d bytes delivered after the next byte began)", n_sent, n_got, late_err, early_err);
            else if (frame_err != 0)
                $display("FAIL: tb_spi_slave (o_frame wrong %0d times)", frame_err);
            else if (bad != 0)
                $display("FAIL: tb_spi_slave (%0d received bytes wrong)", bad);
            else if (miso_err != 0 || miso_bad != 0)
                $display("FAIL: tb_spi_slave (MISO: %0d wrong bytes, %0d changes while SCK high)", miso_err, miso_bad);
            else if (n_take != exp_take)
                $display("FAIL: tb_spi_slave (o_tx_take pulsed %0d times, expected %0d)", n_take, exp_take);
            else
                $display("PASS: tb_spi_slave (4000 frames, %0d bytes, SCK 0.5 to 6.25 MHz with jitter, MISO, take and frame checks)", n_sent);
        end
        $finish;
    end
    initial begin : watchdog
        #400_000_000;
        $display("FAIL: tb_spi_slave watchdog");
        $finish;
    end
endmodule
`default_nettype wire
