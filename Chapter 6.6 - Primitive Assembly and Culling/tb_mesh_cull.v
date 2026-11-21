// tb_mesh_cull.v -- the face test of Chapter 6.6, on its own.
//
// mesh_cull decides, for one triangle, whether to REJECT it (an index that names no vertex, a NEAR or CLAMP vertex), CULL it
// (by the sign of area2 and the cull mode) or DRAW it. The testbench pushes about 23,000 cases through it, one at a time, and
// compares the verdict of every one with the Python model (sim/py/ref_mesh.py, host/mesh_model.py): ordinary
// screen positions, positions all over the int16 range (17-bit differences, 34-bit products), corner values, exactly collinear
// points (area2 = 0), area2 = +1 and -1, every flag on every corner, bad indices, all four cull modes.
//
// Contract checked: i_valid is a one-clock pulse; the inputs (i_bad, the three records, i_mode) are held from that clock until the
// clock of the answer and carry junk between requests; a new i_valid is sent only after the previous verdict has arrived; every
// i_valid is answered by exactly one one-clock o_valid, 1 to 16 clocks later and never without a request; o_verdict (read in the
// clock of o_valid) is 0 (DRAW), 1 (CULLED) or 2 (REJECTED). Any latency from 1 to 16 clocks is fine, and it may vary.
//
// Cases: sim/mesh_cull_cases.hex, 64-bit words (run `.venv/bin/python sim/py/ref_mesh.py --files sim`): word 0 = number of
// cases; then per case four words: record 0, record 1, record 2, and {mode[1:0], bad, expected verdict[1:0]} in bits 5:0
// (mode in bits 5:4, bad in bit 2, verdict in bits 1:0). A record is {intensity[7:0], flags[7:0], depth[15:0], sy[15:0], sx[15:0]}.
//
// Run from build/sim:   make test TESTS="tb_mesh_cull"      (a few seconds)
`timescale 1ns/1ps
`default_nettype none

module tb_mesh_cull;
    reg clk = 1'b0;
    always #10 clk = ~clk;

    reg         v = 1'b0, bad = 1'b0;
    reg  [63:0] r0 = 64'd0, r1 = 64'd0, r2 = 64'd0;
    reg   [1:0] mode = 2'd0;
    wire        ov;
    wire  [1:0] verdict;

    mesh_cull dut (.i_clk(clk), .i_valid(v), .i_bad(bad), .i_rec0(r0), .i_rec1(r1), .i_rec2(r2), .i_mode(mode),
                   .o_valid(ov), .o_verdict(verdict));

    reg [63:0] mem [0:262143];
    integer    ncases, k, i, gap, waited, sent = 0, got = 0, nbad = 0, maxlat = 0;
    integer    cnt_v [0:2];
    reg  [63:0] meta;
    reg   [1:0] want;
    reg         got_flag;

    // every o_valid must answer a request; check the verdict as it arrives
    always @(posedge clk) begin
        if (ov !== 1'b0 && ov !== 1'b1) begin
            if (nbad < 8) $display("tb_mesh_cull: o_valid is X at %0t", $time);
            nbad = nbad + 1;
        end else if (ov) begin
            if (got >= sent) begin
                if (nbad < 8) $display("tb_mesh_cull: o_valid without a request (case %0d) at %0t", got, $time);
                nbad = nbad + 1;
            end else begin
                meta = mem[1 + 4 * got + 3];
                want = meta[1:0];
                if (verdict !== want) begin
                    if (nbad < 8)
                        $display("tb_mesh_cull: case %0d (mode %0d, bad %0d): verdict %0d, expected %0d; records %h %h %h",
                                 got, meta[5:4], meta[2], verdict, want, mem[1 + 4 * got], mem[2 + 4 * got], mem[3 + 4 * got]);
                    nbad = nbad + 1;
                end
                cnt_v[want] = cnt_v[want] + 1;
                got = got + 1;
            end
        end
    end

    initial begin
        for (i = 0; i < 262144; i = i + 1) mem[i] = 64'hxxxxxxxxxxxxxxxx;
        $readmemh("../../sim/mesh_cull_cases.hex", mem);
        if (mem[0] === 64'hxxxxxxxxxxxxxxxx) begin
            $display("FAIL: tb_mesh_cull cannot read sim/mesh_cull_cases.hex (run `.venv/bin/python sim/py/ref_mesh.py --files sim`, and run from build/sim)");
            $finish;
        end
        ncases = mem[0];
        cnt_v[0] = 0; cnt_v[1] = 0; cnt_v[2] = 0;
        repeat (5) @(negedge clk);
        for (k = 0; k < ncases; k = k + 1) begin
            // junk on the inputs for a few clocks, then the case for one clock
            gap = $urandom_range(0, 3);
            repeat (gap) begin
                @(negedge clk);
                r0 = {$urandom, $urandom}; r1 = {$urandom, $urandom}; r2 = {$urandom, $urandom};
                mode = $urandom; bad = $urandom;
            end
            @(negedge clk);
            r0 = mem[1 + 4 * k]; r1 = mem[2 + 4 * k]; r2 = mem[3 + 4 * k];
            meta = mem[4 + 4 * k];
            mode = meta[5:4]; bad = meta[2]; v = 1'b1;
            sent = sent + 1;
            @(negedge clk);
            v = 1'b0;                        // the inputs stay as they are until the verdict arrives
            waited = 1;
            while (got < sent && waited <= 16) begin @(negedge clk); waited = waited + 1; end
            if (got < sent) begin
                $display("tb_mesh_cull: no verdict for case %0d within 16 clocks", k);
                nbad = nbad + 1;
                got = sent;        // resynchronise: count the case as answered
            end
            if (waited > maxlat) maxlat = waited;
            r0 = {$urandom, $urandom}; r1 = {$urandom, $urandom}; r2 = {$urandom, $urandom};   // junk again
            mode = $urandom; bad = $urandom;
        end
        repeat (40) @(negedge clk);          // an extra o_valid would be caught here
        if (nbad != 0) $display("FAIL: tb_mesh_cull (%0d problems, see above)", nbad);
        else $display("PASS: tb_mesh_cull (%0d cases: %0d DRAW, %0d CULLED, %0d REJECTED; slowest answer %0d clocks)",
                      ncases, cnt_v[0], cnt_v[1], cnt_v[2], maxlat - 1);
        $finish;
    end

    initial begin #(200_000_000); $display("FAIL: tb_mesh_cull watchdog timeout"); $finish; end
endmodule
`default_nettype wire
