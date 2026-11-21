// tb_frame_mesh.v -- frame-dump testbench for primitive assembly and culling (Chapter 6.6).
//
// The same harness as Chapter 6.4's tb_frame_depth: a 640x480 vga_timing on a 25 MHz clk_pix, gpu_link and fb_scanout on a
// 50 MHz clk_sys, wired as in Chapter 5.7's tb_frame_display_lists (no new ports: the index buffer, the face test and the mesh
// loop live inside the command engine). The testbench is an SPI master playing sim/mesh_stream.hex (written by
// `sim/py/ref_mesh.py --files sim`) into the link and dumps six consecutive frames, build/frame_mesh_f0.ppm to f5.ppm: a cube
// with three faces towards the camera drawn with cull mode BACK, NONE and FRONT, the cube poking through the near plane,
// vertex-intensity mode, and a display list (SET_MATRIX, TRANSFORM, CLEAR_ALL, DRAW_MESH, FLIP) whose matrix the host patches
// with twelve WRITE_LIST_WORDs. sim/py/ref_mesh.py draws the expected frames and checks that they show the faces it expects.
//
// Stream entries (16 bits): 00NN byte | A000 open frame | A001 close frame | 3000 wait for the next
// frame start | 2YYY wait for line YYY | 4000 arm the dumpers | 1NNN fence: read NNN reply bytes | FFFF end.
//
// Checked here: pixel format and geometry (frame_dump), that o_hsync, o_vsync and o_de are the inputs
// delayed by the same number of clocks (any latency of 1 to 24 is fine), the MISO status byte, that the
// commands that must be sent in the middle of a frame really were, and the 30 reply bytes: GET_INFO (flags 00), then
// GET_MESH_STATS and GET_INFO (flags 06: five bad packets and a list holding commands a list may not hold), capabilities 3FFF.
// The pixels are NOT checked here: a wrong picture still prints PASS, and only the compare.py step
// of `make frame-mesh` catches it.
//
// Run from the repo root:   make frame-mesh
`timescale 1ns/1ps
`default_nettype none

module tb_frame_mesh;
    localparam NFRAMES = 6;
    localparam NREPLY = 30;
    localparam MAXWORDS = 131072;

    // ---------------- clocks ----------------
    reg clk_pix = 1'b0, clk_sys = 1'b0;
    always #20 clk_pix = ~clk_pix;
    initial begin
        #3;
        forever #10 clk_sys = ~clk_sys;
    end

    // ---------------- beam ----------------
    wire       hs, vs, de;
    wire [9:0] x, y;
    vga_timing u_vga (.i_clk(clk_pix), .o_hsync(hs), .o_vsync(vs), .o_de(de), .o_x(x), .o_y(y));

    // ---------------- DUT: link + scanout with layers and sprites ----------------
    reg        rst = 1'b1;
    reg        sck = 1'b0, mosi = 1'b0, cs_n = 1'b1;
    wire       miso, ready, overflow, tx;
    wire       fb_we, pal_we, fb_buf, flip_req, flip_ack, vblank_pix;
    wire [16:0] fb_addr;
    wire [7:0]  fb_wdata, fb_rdata, pal_addr;
    wire [23:0] pal_wdata;
    wire        tm_we, tm_layer, tg_we, sc_we, sc_layer, en_we;
    wire [11:0] tm_addr;
    wire [15:0] tm_wdata;
    wire [13:0] tg_addr;
    wire [7:0]  tg_wdata;
    wire [8:0]  sc_x, sc_y;
    wire [1:0]  en;
    wire        sp_we, spc_we, spc_en, as_ok;               // sprite table writes, SPRITE_CTRL, asset lookup
    wire        cx_we;                                      // compositor settings
    wire [1:0]  cx_sel;
    wire [7:0]  cx_wdata;
    wire [8:0]  sp_addr;
    wire [7:0]  sp_wdata, ar_data;
    wire [14:0] ar_addr, as_base;
    wire [3:0]  as_id;
    wire vblank_sys;
    synchronizer u_sv (.i_clk(clk_sys), .i_async(vblank_pix), .o_sync(vblank_sys));

    gpu_link #(.CLK_HZ(50_000_000), .BAUD(2_500_000)) link (
        .i_clk(clk_sys), .i_rst(rst), .i_rx(1'b1), .o_tx(tx),
        .i_sck(sck), .i_mosi(mosi), .i_cs_n(cs_n), .o_miso(miso), .i_vblank(vblank_sys),
        .o_ready(ready), .o_overflow(overflow),
        .o_fb_we(fb_we), .o_fb_addr(fb_addr), .o_fb_wdata(fb_wdata), .i_fb_rdata(fb_rdata),
        .o_pal_we(pal_we), .o_pal_addr(pal_addr), .o_pal_wdata(pal_wdata),
        .o_fb_buf(fb_buf), .o_flip_req(flip_req), .i_flip_ack(flip_ack),
        .o_tm_we(tm_we), .o_tm_layer(tm_layer), .o_tm_addr(tm_addr), .o_tm_wdata(tm_wdata),
        .o_tg_we(tg_we), .o_tg_addr(tg_addr), .o_tg_wdata(tg_wdata),
        .o_sc_we(sc_we), .o_sc_layer(sc_layer), .o_sc_x(sc_x), .o_sc_y(sc_y),
        .o_en_we(en_we), .o_en(en),
        .o_sp_we(sp_we), .o_sp_addr(sp_addr), .o_sp_wdata(sp_wdata), .o_spc_we(spc_we), .o_spc_en(spc_en),
        .o_cx_we(cx_we), .o_cx_sel(cx_sel), .o_cx_wdata(cx_wdata),
        .i_ar_addr(ar_addr), .o_ar_data(ar_data), .i_as_id(as_id), .o_as_base(as_base), .o_as_ok(as_ok));

    wire       o_hs, o_vs, o_de;
    wire [7:0] o_r, o_g, o_b;
    fb_scanout #(.FB_FILE(""), .PAL_FILE("")) scan (
        .i_clk_sys(clk_sys), .i_fb_buf(fb_buf),
        .i_fb_we(fb_we), .i_fb_addr(fb_addr), .i_fb_wdata(fb_wdata), .o_fb_rdata(fb_rdata),
        .i_pal_we(pal_we), .i_pal_addr(pal_addr), .i_pal_wdata(pal_wdata),
        .i_flip_req(flip_req), .o_flip_ack(flip_ack),
        .i_clk_pix(clk_pix), .i_x(x), .i_y(y), .i_de(de), .i_hsync(hs), .i_vsync(vs),
        .o_hsync(o_hs), .o_vsync(o_vs), .o_de(o_de), .o_red(o_r), .o_grn(o_g), .o_blu(o_b),
        .o_vblank(vblank_pix),
        .i_tm_we(tm_we), .i_tm_layer(tm_layer), .i_tm_addr(tm_addr), .i_tm_wdata(tm_wdata),
        .i_tg_we(tg_we), .i_tg_addr(tg_addr), .i_tg_wdata(tg_wdata),
        .i_sc_we(sc_we), .i_sc_layer(sc_layer), .i_sc_x(sc_x), .i_sc_y(sc_y),
        .i_en_we(en_we), .i_en(en),
        .i_sp_we(sp_we), .i_sp_addr(sp_addr), .i_sp_wdata(sp_wdata), .i_spc_we(spc_we), .i_spc_en(spc_en),
        .i_cx_we(cx_we), .i_cx_sel(cx_sel), .i_cx_wdata(cx_wdata),
        .o_ar_addr(ar_addr), .i_ar_data(ar_data), .o_as_id(as_id), .i_as_base(as_base), .i_as_ok(as_ok));

    // ---------------- dumpers: four consecutive frames ----------------
    reg  armed = 1'b0;
    wire dump_vs = o_vs | ~armed;
    wire [NFRAMES-1:0] done, err;
    genvar gi;
    generate
        for (gi = 0; gi < NFRAMES; gi = gi + 1) begin : dump
            localparam [7:0] DIGIT = 8'd48 + gi;
            frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(8), .SKIP(gi),
                         .FILE({"build/frame_mesh_f", DIGIT, ".ppm"})) u_dump (
                .i_clk(clk_pix), .i_vsync(dump_vs), .i_de(o_de), .i_red(o_r), .i_grn(o_g), .i_blu(o_b),
                .o_done(done[gi]), .o_error(err[gi]));
        end
    endgenerate

    // ---------------- latency and sync alignment (as in tb_frame_scanout) ----------------
    reg [31:0] h_hs = {32{1'b1}}, h_vs = {32{1'b1}}, h_de = 32'd0;
    integer    lat = 0, since_rise = -1;
    reg        sync_err = 1'b0;
    always @(posedge clk_pix) begin
        if (lat == 0) begin
            if (since_rise >= 0) since_rise = since_rise + 1;
            if (since_rise < 0 && de && !h_de[0] && y == 10'd4) since_rise = 0;
            if (since_rise > 0 && o_de === 1'b1 && since_rise <= 24) lat = since_rise;
            if (since_rise > 24) begin
                if (!sync_err) $display("tb_frame_mesh: o_de did not rise within 24 clocks of i_de");
                sync_err = 1'b1;
                since_rise = -1;
            end
        end else if (o_hs !== h_hs[lat-1] || o_vs !== h_vs[lat-1] || o_de !== h_de[lat-1]) begin
            if (!sync_err)
                $display("tb_frame_mesh: syncs/de not all delayed by %0d clocks (beam x=%0d y=%0d)", lat, x, y);
            sync_err = 1'b1;
        end
        h_hs <= {h_hs[30:0], hs};
        h_vs <= {h_vs[30:0], vs};
        h_de <= {h_de[30:0], de};
    end

    // ---------------- the SPI master (mode 0, 5 MHz) ----------------
    localparam real HALF = 100.0;
    reg [7:0] m_rx;
    integer   spi_err = 0, n_frames = 0, n_bytes = 0;
    task xfer(input [7:0] tx_b);
        integer i;
        begin
            for (i = 7; i >= 0; i = i - 1) begin
                #5 mosi = tx_b[i];
                #(HALF - 15.0) m_rx[i] = miso;
                #10 sck = 1'b1;
                #(HALF) sck = 1'b0;
            end
        end
    endtask

    reg [7:0] got [0:63];
    integer   n_got = 0, n_late = 0, n_hang = 0;
    reg [7:0] st0;
    task open_frame;
        time t0;
        begin
            t0 = $time;
            while (ready !== 1'b1 && n_hang == 0) begin
                #1000;
                if ($time - t0 > 60_000_000) begin
                    $display("tb_frame_mesh: READY stayed low for 60 ms");
                    n_hang = 1;
                end
            end
            #50 cs_n = 1'b0;
            #150;
            n_frames = n_frames + 1;
        end
    endtask
    task close_frame;
        begin
            #100 cs_n = 1'b1;
            #200;
        end
    endtask
    integer first;
    task send_byte(input [7:0] b);
        begin
            xfer(b);
            if (first) begin
                st0 = m_rx; first = 0;
            end
            n_bytes = n_bytes + 1;
        end
    endtask

    // read frames until 'want' more reply bytes have arrived
    task fence(input integer want);
        integer goal, cnt, k, tries;
        begin
            goal = n_got + want; tries = 0;
            while (n_got < goal && tries < 400) begin
                open_frame;
                xfer(8'hFF); st0 = m_rx;
                xfer(8'h00); cnt = m_rx;
                for (k = 0; k < 12; k = k + 1) begin
                    xfer(8'h00);
                    if (k < cnt && n_got < 64 && n_got < goal) begin got[n_got] = m_rx; n_got = n_got + 1; end
                end
                close_frame;
                tries = tries + 1;
                #(20_000);
            end
            if (n_got < goal) begin
                $display("tb_frame_mesh: reply never arrived (%0d of %0d bytes)", n_got, goal);
                n_hang = 1;
            end
        end
    endtask

    reg [15:0] stream [0:MAXWORDS-1];
    reg [7:0]  expect_reply [0:63];
    integer    pc = 0, i, mid_frames = 0;
    reg        stream_done = 1'b0, in_frame = 1'b0;
    reg [15:0] w;
    integer    y_want = -1;

    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("build/tb_frame_mesh.vcd");
            $dumpvars(0, tb_frame_mesh);
        end
        for (i = 0; i < MAXWORDS; i = i + 1) stream[i] = 16'hFFFF;
        $readmemh("sim/mesh_stream.hex", stream);
        $readmemh("sim/mesh_replies.hex", expect_reply);
        if (stream[0] === 16'hxxxx || expect_reply[0] === 8'hxx) begin
            $display("FAIL: tb_frame_mesh cannot read sim/mesh_stream.hex (run `.venv/bin/python sim/py/ref_mesh.py --files sim`, and run from the repo root)");
            $finish;
        end
        repeat (10) @(posedge clk_sys);
        rst = 1'b0;
        repeat (10) @(posedge clk_sys);
        w = stream[0];
        while (w != 16'hFFFF && n_hang == 0) begin
            casez (w)
                16'h00??: begin if (in_frame) send_byte(w[7:0]); else $display("tb_frame_mesh: byte outside a frame"); end
                16'hA000: begin open_frame; first = 1; in_frame = 1'b1; end
                16'hA001: begin close_frame; in_frame = 1'b0;
                          if (st0[7:6] !== 2'b01) begin
                              if (spi_err == 0) $display("tb_frame_mesh: status byte %h does not start with 01", st0);
                              spi_err = spi_err + 1;
                          end end
                16'h3000: begin
                    y_want = -1;
                    @(posedge clk_pix); while (!(x == 10'd0 && y == 10'd0)) @(posedge clk_pix);
                end
                16'h4000: armed = 1'b1;
                16'h1???: fence(w[11:0]);
                16'h2???: begin
                    y_want = w[11:0];
                    @(posedge clk_pix); while (!(x == 10'd0 && y == y_want)) @(posedge clk_pix);
                end
                default: $display("tb_frame_mesh: bad stream entry %h at %0d", w, pc);
            endcase
            // commands for the middle of a frame must be sent before blanking, those for blanking inside it
            if (w == 16'hA001) begin
                if (y_want >= 0 && y_want < 480 && y >= 480) n_late = n_late + 1;
                if (y_want >= 480 && y < 480) n_late = n_late + 1;
            end
            pc = pc + 1;
            w = stream[pc];
        end
        stream_done = 1'b1;
    end

    // ---------------- watchdog and verdict ----------------
    initial begin : watchdog
        #(120 * 800 * 525 * 40);
        $display("FAIL: tb_frame_mesh watchdog timeout (frames not complete: is o_vsync toggling?)");
        $finish;
    end

    integer bad = 0;
    initial begin
        wait (&done && stream_done);
        #1;
        for (i = 0; i < NREPLY; i = i + 1) if (got[i] !== expect_reply[i]) bad = bad + 1;
        if (|err)
            $display("FAIL: tb_frame_mesh (frame errors, see frame_dump lines above)");
        else if (sync_err)
            $display("FAIL: tb_frame_mesh (o_hsync/o_vsync/o_de not aligned, see above)");
        else if (n_hang != 0)
            $display("FAIL: tb_frame_mesh (the link stopped answering)");
        else if (spi_err != 0)
            $display("FAIL: tb_frame_mesh (bad MISO status byte)");
        else if (overflow !== 1'b0)
            $display("FAIL: tb_frame_mesh (FIFO overflow)");
        else if (n_late != 0)
            $display("FAIL: tb_frame_mesh (%0d command frames ran past the time the story needs: SPI too slow or READY low too long)", n_late);
        else if (bad != 0)
            $display("FAIL: tb_frame_mesh (%0d of the 30 reply bytes are wrong: GET_INFO %h %h %h %h %h %h %h %h %h, GET_MESH_STATS %h %h %h %h %h %h %h %h %h %h %h %h, GET_INFO %h %h %h %h %h %h %h %h %h; expected 47 01 FF 3F 40 01 F0 00 00, see sim/mesh_replies.hex, and 47 01 FF 3F 40 01 F0 00 06)",
                     bad, got[0], got[1], got[2], got[3], got[4], got[5], got[6], got[7], got[8],
                     got[9], got[10], got[11], got[12], got[13], got[14], got[15], got[16], got[17], got[18], got[19], got[20],
                     got[21], got[22], got[23], got[24], got[25], got[26], got[27], got[28], got[29]);
        else
            $display("PASS: tb_frame_mesh (latency %0d clocks, %0d SPI frames, %0d bytes; build/frame_mesh_f0 to f5 .ppm written)",
                     lat, n_frames, n_bytes);
        $finish;
    end
endmodule
`default_nettype wire
