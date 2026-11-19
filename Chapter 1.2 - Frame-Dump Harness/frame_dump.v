// frame_dump.v -- simulation-only frame grabber (Chapter 1.2).
//
// Watches a video bus (vsync, de, color) and writes one complete frame to a
// binary PPM (P6) file. It is a testbench component, not synthesizable RTL:
// keep it in sim/ and add `-y sim` to the iverilog command line so any
// testbench can instantiate it by name.
//
// How a frame is found:
//   1. Wait for SKIP+1 vsync assertions (vsync is active low). SKIP=0 dumps
//      the first complete frame after power-up, SKIP=1 the one after, ...
//   2. The first i_de cycle after that starts the frame. Each run of
//      consecutive i_de cycles is one line; HEIGHT runs make the frame.
//   3. The PPM is written when the frame ends, and o_done goes high.
//
// Sampling: inputs are read on the rising edge of i_clk, exactly as a
// flip-flop in the monitor would read them. Drive it from registered or
// combinational DUT outputs; do not drive the inputs with blocking
// assignments on the same clock edge.
//
// Geometry checks: every line must have exactly WIDTH de cycles, the frame
// exactly HEIGHT lines before the next vsync, and no pixel may be X/Z.
// Violations are printed (prefixed "frame_dump:") and raise o_error. The
// file is always a valid WIDTH x HEIGHT image: extra pixels are dropped and
// missing ones are written as black, so compare.py can still point at the
// first bad pixel.
//
// Color: BITS bits per channel, written as-is with PPM maxval 2^BITS-1
// (BITS=3 on the Go Board gives maxval 7; BITS=8 later gives 255).
`timescale 1ns/1ps
`default_nettype none

module frame_dump #(
    parameter WIDTH  = 640,
    parameter HEIGHT = 480,
    parameter BITS   = 3,
    parameter SKIP   = 0,
    parameter FILE   = "build/frame.ppm"
) (
    input  wire            i_clk,
    input  wire            i_vsync,   // active low
    input  wire            i_de,
    input  wire [BITS-1:0] i_red,
    input  wire [BITS-1:0] i_grn,
    input  wire [BITS-1:0] i_blu,
    output reg             o_done  = 1'b0,
    output reg             o_error = 1'b0
);
    localparam MAXVAL = (1 << BITS) - 1;

    reg [3*BITS-1:0] r_pix [0:WIDTH*HEIGHT-1];

    integer i, fd, seen, row, col, nerr;
    reg     vs_prev;
    reg     frame_over;

    task report(input [8*120-1:0] msg);
        begin
            if (nerr < 3) $display("frame_dump: %0s: %0s (line %0d, pixel %0d)", FILE, msg, row, col);
            nerr    = nerr + 1;
            o_error = 1'b1;
        end
    endtask

    // True on the cycle vsync goes from high to low.
    function vsync_fell(input prev, input now);
        vsync_fell = (prev === 1'b1) && (now === 1'b0);
    endfunction

    initial begin
        nerr = 0; row = 0; col = 0;
        for (i = 0; i < WIDTH * HEIGHT; i = i + 1) r_pix[i] = 0;

        // 1. Skip to the start of the requested frame.
        seen = 0; vs_prev = 1'b1;
        while (seen < SKIP + 1) begin
            @(posedge i_clk);
            if (vsync_fell(vs_prev, i_vsync)) seen = seen + 1;
            vs_prev = i_vsync;
        end

        // 2. Capture lines until HEIGHT are done or the next vsync arrives.
        frame_over = 0;
        while (!frame_over) begin
            @(posedge i_clk);
            if (vsync_fell(vs_prev, i_vsync)) begin
                frame_over = 1;
                if (row < HEIGHT) report("frame ended early (too few de lines)");
            end else if (i_de === 1'bx || i_de === 1'bz) begin
                report("de is X/Z");
            end else if (i_de) begin
                if (row >= HEIGHT) begin
                    report("extra de line after the last line");
                    frame_over = 1;
                end else begin
                    if (col < WIDTH) begin
                        if ((^{i_red, i_grn, i_blu}) === 1'bx) report("pixel color is X/Z");
                        else r_pix[row * WIDTH + col] = {i_red, i_grn, i_blu};
                    end
                    col = col + 1;
                end
            end else if (col != 0) begin
                // de just fell: one line finished.
                if (col != WIDTH) report("line has the wrong number of de pixels");
                row = row + 1;
                col = 0;
            end
            vs_prev = i_vsync;
        end

        // 3. Write the image.
        fd = $fopen(FILE, "wb");
        if (fd == 0) begin
            $display("frame_dump: cannot open %0s for writing (does the directory exist?)", FILE);
            o_error = 1'b1;
        end else begin
            $fwrite(fd, "P6\n%0d %0d\n%0d\n", WIDTH, HEIGHT, MAXVAL);
            for (i = 0; i < WIDTH * HEIGHT; i = i + 1)
                $fwrite(fd, "%c%c%c", r_pix[i][3*BITS-1:2*BITS], r_pix[i][2*BITS-1:BITS], r_pix[i][BITS-1:0]);
            $fclose(fd);
        end
        o_done = 1'b1;
    end
endmodule
`default_nettype wire
