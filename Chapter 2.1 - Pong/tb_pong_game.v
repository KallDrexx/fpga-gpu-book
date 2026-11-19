// tb_pong_game.v -- self-checking testbench for pong_game (Chapter 2.1).
//
// The testbench drives i_tick itself (one pulse every few clocks) instead of
// running video, so thousands of game frames take a fraction of a second.
//
// Two DUTs:
//   A  default parameters. Directed tests use i_load to place the ball and
//      paddles, give one tick, and check the outcome: paddle bounces (centre,
//      edges, one row of overlap, near misses), wall bounces, points, the
//      serve delay, paddle limits, winning and the game-over pause. Then a
//      long randomized game.
//   B  different parameters (fast ball that moves further than a paddle is
//      wide, short serve and game-over pauses, first to 3 wins), randomized
//      game only.
//
// For both DUTs a behavioral model of the rules in this file runs alongside
// and every output is compared with it on every clock, so a value that
// changes between ticks, a wrong step, a late bounce or a wrong score fails
// even when no directed test looks at it.
//
// Run: iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/tb.vvp sim/tb_pong_game.v
//      vvp -n build/tb.vvp           (add +vcd for waveforms)
`timescale 1ns/1ps
`default_nettype none

// ---------------------------------------------------------------------------
// Model of the Pong rules, compared with one DUT every clock.
// ---------------------------------------------------------------------------
module tb_pong_game_model #(
    parameter NAME = "?",
    parameter W = 320, parameter H = 240,
    parameter BALL = 4, parameter PAD_W = 4, parameter PAD_H = 32,
    parameter PAD_L_X = 8, parameter PAD_R_X = 308,
    parameter PAD_SPEED = 3, parameter BALL_DX = 3, parameter BALL_DY = 2,
    parameter SERVE_FRAMES = 60, parameter OVER_FRAMES = 180, parameter WIN_SCORE = 9
) (
    input wire       i_clk, i_tick,
    input wire       i_l_up, i_l_dn, i_r_up, i_r_dn,
    input wire       i_load,
    input wire [9:0] i_load_ball_x, i_load_ball_y,
    input wire       i_load_left, i_load_up,
    input wire [9:0] i_load_lpad_y, i_load_rpad_y,
    input wire [9:0] i_ball_x, i_ball_y,
    input wire       i_ball_left, i_ball_up,
    input wire [9:0] i_lpad_y, i_rpad_y,
    input wire [3:0] i_score_l, i_score_r,
    input wire [1:0] i_state
);
    localparam SERVE = 0, PLAY = 1, OVER = 2;
    localparam CX = (W - BALL) / 2, CY = (H - BALL) / 2, PY = (H - PAD_H) / 2;
    localparam L_FACE = PAD_L_X + PAD_W, PMAX = H - PAD_H;

    integer bx = CX, by = CY, left = 0, up = 0, lp = PY, rp = PY;
    integer sl = 0, sr = 0, state = SERVE, wait_n = SERVE_FRAMES;
    integer ticks = 0, l_hits = 0, r_hits = 0, walls = 0, points = 0, games = 0;
    integer errors = 0;
    integer nbx, nby, nleft, nup;

    function integer pad_step(input integer p, input integer u, input integer d);
        if (u && !d)      pad_step = (p <= PAD_SPEED) ? 0 : p - PAD_SPEED;
        else if (d && !u) pad_step = (p + PAD_SPEED >= PMAX) ? PMAX : p + PAD_SPEED;
        else              pad_step = p;
    endfunction

    task score(input integer for_left);
        begin
            points = points + 1;
            if (for_left) sl = sl + 1; else sr = sr + 1;
            bx = CX; by = CY; up = 0;
            left = for_left ? 0 : 1;  // serve toward the player who lost the point
            if ((for_left ? sl : sr) == WIN_SCORE) begin state = OVER; wait_n = OVER_FRAMES; end
            else begin state = SERVE; wait_n = SERVE_FRAMES; end
        end
    endtask

    always @(posedge i_clk) begin
        if (i_load) begin
            bx = i_load_ball_x; by = i_load_ball_y; left = i_load_left; up = i_load_up;
            lp = i_load_lpad_y; rp = i_load_rpad_y; state = PLAY;
        end else if (i_tick) begin
            ticks = ticks + 1;
            // Collision tests use the positions from before this tick: the
            // positions in the frame the players just saw.
            nbx = bx; nby = by; nleft = left; nup = up;
            if (state == SERVE) begin
                wait_n = wait_n - 1;
                if (wait_n == 0) state = PLAY;
            end else if (state == OVER) begin
                wait_n = wait_n - 1;
                if (wait_n == 0) begin
                    state = SERVE; wait_n = SERVE_FRAMES; sl = 0; sr = 0; games = games + 1;
                end
            end else begin
                if (up) begin
                    if (by <= BALL_DY) begin nby = 0; nup = 0; walls = walls + 1; end
                    else nby = by - BALL_DY;
                end else begin
                    if (by + BALL + BALL_DY >= H) begin nby = H - BALL; nup = 1; walls = walls + 1; end
                    else nby = by + BALL_DY;
                end
                if (left) begin
                    if (bx >= L_FACE && bx - BALL_DX <= L_FACE && by + BALL > lp && by < lp + PAD_H) begin
                        nbx = L_FACE; nleft = 0; l_hits = l_hits + 1;
                    end else if (bx <= BALL_DX) nbx = -1;  // point for the right player
                    else nbx = bx - BALL_DX;
                end else begin
                    if (bx + BALL <= PAD_R_X && bx + BALL + BALL_DX >= PAD_R_X && by + BALL > rp && by < rp + PAD_H) begin
                        nbx = PAD_R_X - BALL; nleft = 1; r_hits = r_hits + 1;
                    end else if (bx + BALL + BALL_DX >= W) nbx = -2;  // point for the left player
                    else nbx = bx + BALL_DX;
                end
                bx = nbx; by = nby; left = nleft; up = nup;
                if (nbx == -1) score(0);
                else if (nbx == -2) score(1);
            end
            lp = pad_step(lp, i_l_up, i_l_dn);
            rp = pad_step(rp, i_r_up, i_r_dn);
        end
    end

    // Compare between clock edges, when everything has settled.
    // A mismatch is counted (and printed) once per tick, not on every clock.
    integer cyc = 0, last_bad = -1;
    always @(negedge i_clk) begin
        cyc = cyc + 1;
        if ((^{i_ball_x, i_ball_y, i_ball_left, i_ball_up, i_lpad_y, i_rpad_y, i_score_l, i_score_r, i_state}) === 1'bx) begin
            if (errors < 3) $display("  %0s: X/Z on an output at clock %0d (give every register an initial value)", NAME, cyc);
            errors = errors + 1;
        end else if (ticks != last_bad && (i_ball_x != bx || i_ball_y != by || i_ball_left != left || i_ball_up != up ||
                     i_lpad_y != lp || i_rpad_y != rp || i_score_l != sl || i_score_r != sr || i_state != state)) begin
            last_bad = ticks;
            if (errors < 3)
                $display("  %0s: after tick %0d got ball (%0d,%0d) %0s/%0s pads %0d,%0d score %0d-%0d state %0d; expected ball (%0d,%0d) %0s/%0s pads %0d,%0d score %0d-%0d state %0d",
                         NAME, ticks, i_ball_x, i_ball_y, i_ball_left ? "left" : "right", i_ball_up ? "up" : "down",
                         i_lpad_y, i_rpad_y, i_score_l, i_score_r, i_state,
                         bx, by, left ? "left" : "right", up ? "up" : "down", lp, rp, sl, sr, state);
            errors = errors + 1;
        end
    end
endmodule

// ---------------------------------------------------------------------------
// One DUT with its stimulus registers, its model and the random player.
// ---------------------------------------------------------------------------
module tb_pong_game_unit #(
    parameter NAME = "?",
    parameter W = 320, parameter H = 240,
    parameter BALL = 4, parameter PAD_W = 4, parameter PAD_H = 32,
    parameter PAD_L_X = 8, parameter PAD_R_X = 308,
    parameter PAD_SPEED = 3, parameter BALL_DX = 3, parameter BALL_DY = 2,
    parameter SERVE_FRAMES = 60, parameter OVER_FRAMES = 180, parameter WIN_SCORE = 9
) (input wire i_clk);
    reg       tick = 0, l_up = 0, l_dn = 0, r_up = 0, r_dn = 0, load = 0;
    reg [9:0] ld_bx = 0, ld_by = 0, ld_lp = 0, ld_rp = 0;
    reg       ld_left = 0, ld_up = 0;
    wire [9:0] bx, by, lp, rp;
    wire       left, up;
    wire [3:0] sl, sr;
    wire [1:0] state;

    pong_game #(.W(W), .H(H), .BALL(BALL), .PAD_W(PAD_W), .PAD_H(PAD_H),
                .PAD_L_X(PAD_L_X), .PAD_R_X(PAD_R_X), .PAD_SPEED(PAD_SPEED),
                .BALL_DX(BALL_DX), .BALL_DY(BALL_DY), .SERVE_FRAMES(SERVE_FRAMES),
                .OVER_FRAMES(OVER_FRAMES), .WIN_SCORE(WIN_SCORE)) dut (
        .i_clk(i_clk), .i_tick(tick),
        .i_l_up(l_up), .i_l_dn(l_dn), .i_r_up(r_up), .i_r_dn(r_dn),
        .i_load(load), .i_load_ball_x(ld_bx), .i_load_ball_y(ld_by),
        .i_load_left(ld_left), .i_load_up(ld_up), .i_load_lpad_y(ld_lp), .i_load_rpad_y(ld_rp),
        .o_ball_x(bx), .o_ball_y(by), .o_ball_left(left), .o_ball_up(up),
        .o_lpad_y(lp), .o_rpad_y(rp), .o_score_l(sl), .o_score_r(sr), .o_state(state));

    tb_pong_game_model #(.NAME(NAME), .W(W), .H(H), .BALL(BALL), .PAD_W(PAD_W), .PAD_H(PAD_H),
                .PAD_L_X(PAD_L_X), .PAD_R_X(PAD_R_X), .PAD_SPEED(PAD_SPEED),
                .BALL_DX(BALL_DX), .BALL_DY(BALL_DY), .SERVE_FRAMES(SERVE_FRAMES),
                .OVER_FRAMES(OVER_FRAMES), .WIN_SCORE(WIN_SCORE)) model (
        .i_clk(i_clk), .i_tick(tick),
        .i_l_up(l_up), .i_l_dn(l_dn), .i_r_up(r_up), .i_r_dn(r_dn),
        .i_load(load), .i_load_ball_x(ld_bx), .i_load_ball_y(ld_by),
        .i_load_left(ld_left), .i_load_up(ld_up), .i_load_lpad_y(ld_lp), .i_load_rpad_y(ld_rp),
        .i_ball_x(bx), .i_ball_y(by), .i_ball_left(left), .i_ball_up(up),
        .i_lpad_y(lp), .i_rpad_y(rp), .i_score_l(sl), .i_score_r(sr), .i_state(state));

    // All stimulus changes on the falling edge, away from the DUT's sampling edge.
    task do_tick;
        begin
            @(negedge i_clk) tick = 1;
            @(negedge i_clk) tick = 0;
            repeat (2) @(negedge i_clk);  // a few idle clocks between ticks
        end
    endtask

    task do_load(input integer x, input integer y, input integer lft, input integer u,
                 input integer lpad, input integer rpad);
        begin
            @(negedge i_clk);
            ld_bx = x; ld_by = y; ld_left = lft; ld_up = u; ld_lp = lpad; ld_rp = rpad; load = 1;
            @(negedge i_clk) load = 0;
            @(negedge i_clk);
        end
    endtask

    task buttons(input integer lu, input integer ld, input integer ru, input integer rd);
        begin l_up = lu; l_dn = ld; r_up = ru; r_dn = rd; end
    endtask

    // A sloppy player for each side: follows the ball, sometimes wrongly.
    integer seed = 1, n, aim;
    task random_play(input integer ticks);
        begin
            for (n = 0; n < ticks; n = n + 1) begin
                aim = by + BALL / 2 - PAD_H / 2 + ($random(seed) % 24);
                l_up = (lp > aim + 2); l_dn = (lp + 2 < aim);
                aim = by + BALL / 2 - PAD_H / 2 + ($random(seed) % 24);
                r_up = (rp > aim + 2); r_dn = (rp + 2 < aim);
                if (($random(seed) & 31) == 0) begin l_up = $random(seed); l_dn = $random(seed); end
                if (($random(seed) & 31) == 0) begin r_up = $random(seed); r_dn = $random(seed); end
                // Now and then, a load mid-game with random values on screen. Never
                // during OVER: a load there would leave a score at WIN_SCORE in PLAY,
                // which the rules never allow.
                if (($random(seed) & 1023) == 0 && state != 2)
                    do_load({$random(seed)} % (W - BALL), {$random(seed)} % (H - BALL), $random(seed), $random(seed),
                            {$random(seed)} % (PMAX_ + 1), {$random(seed)} % (PMAX_ + 1));
                do_tick;
            end
            buttons(0, 0, 0, 0);
        end
    endtask
    localparam PMAX_ = H - PAD_H;
endmodule

// ---------------------------------------------------------------------------
// Top level
// ---------------------------------------------------------------------------
module tb_pong_game;
    reg clk = 0;
    always #20 clk = ~clk;  // 25 MHz

    // A: defaults. B: a fast ball (BALL_DX > PAD_W) and short pauses.
    tb_pong_game_unit #(.NAME("A")) a (.i_clk(clk));
    tb_pong_game_unit #(.NAME("B"), .BALL(3), .PAD_W(2), .PAD_H(20), .PAD_L_X(5), .PAD_R_X(313),
                        .PAD_SPEED(4), .BALL_DX(7), .BALL_DY(3),
                        .SERVE_FRAMES(2), .OVER_FRAMES(5), .WIN_SCORE(3)) b (.i_clk(clk));

    // Constants for A (the defaults).
    localparam CX = 158, CY = 118, PY = 104, LF = 12, RX = 308;

    integer fails = 0, i;

    task check_ball(input [8*60-1:0] what, input integer bx, input integer by, input integer lft, input integer u);
        begin
            if (a.bx !== bx || a.by !== by || a.left !== lft || a.up !== u) begin
                if (fails < 6)
                    $display("  A: %0s: ball (%0d,%0d) moving %0s/%0s, expected (%0d,%0d) %0s/%0s", what,
                             a.bx, a.by, a.left ? "left" : "right", a.up ? "up" : "down",
                             bx, by, lft ? "left" : "right", u ? "up" : "down");
                fails = fails + 1;
            end
        end
    endtask

    task check_score(input [8*60-1:0] what, input integer l, input integer r, input integer st);
        begin
            if (a.sl !== l || a.sr !== r || a.state !== st) begin
                if (fails < 6)
                    $display("  A: %0s: score %0d-%0d state %0d, expected %0d-%0d state %0d", what,
                             a.sl, a.sr, a.state, l, r, st);
                fails = fails + 1;
            end
        end
    endtask

    task check_pads(input [8*60-1:0] what, input integer l, input integer r);
        begin
            if (a.lp !== l || a.rp !== r) begin
                if (fails < 6)
                    $display("  A: %0s: paddles at %0d,%0d, expected %0d,%0d", what, a.lp, a.rp, l, r);
                fails = fails + 1;
            end
        end
    endtask

    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("build/tb_pong_game.vcd");
            $dumpvars(0, tb_pong_game);
        end
    end

    initial begin : watchdog
        #(40 * 2000000);
        $display("FAIL: tb_pong_game watchdog timeout");
        $finish;
    end

    initial begin
        repeat (3) @(negedge clk);

        // ---- power-up and serve ----
        check_ball("power-up", CX, CY, 0, 0);
        check_score("power-up", 0, 0, 0);
        check_pads("power-up", PY, PY);
        for (i = 0; i < 60; i = i + 1) a.do_tick;
        check_ball("ball must wait 60 ticks before the serve", CX, CY, 0, 0);
        check_score("last serve tick enters PLAY", 0, 0, 1);
        a.do_tick;
        check_ball("first move after the serve", CX + 3, CY + 2, 0, 0);

        // ---- left paddle ----
        a.do_load(20, 100, 1, 0, 90, PY); a.do_tick;
        check_ball("ball in open space moving left", 17, 102, 1, 0);
        a.do_load(14, 100, 1, 0, 90, PY); a.do_tick;
        check_ball("left paddle bounce (step crosses the face)", LF, 102, 0, 0);
        a.do_load(LF + 3, 100, 1, 1, 90, PY); a.do_tick;
        check_ball("left paddle bounce (step lands on the face)", LF, 98, 0, 1);
        a.do_load(LF, 100, 1, 0, 90, PY); a.do_tick;
        check_ball("left paddle bounce (ball already touching)", LF, 102, 0, 0);
        a.do_load(LF + 4, 100, 1, 0, 90, PY); a.do_tick;
        check_ball("one step short of the left paddle", LF + 1, 102, 1, 0);
        a.do_load(14, 90 - 3, 1, 0, 90, PY); a.do_tick;
        check_ball("ball's bottom row overlaps paddle's top row: hit", LF, 89, 0, 0);
        a.do_load(14, 90 - 4, 1, 0, 90, PY); a.do_tick;
        check_ball("ball just above the left paddle: miss", 11, 88, 1, 0);
        a.do_load(14, 90 + 31, 1, 1, 90, PY); a.do_tick;
        check_ball("ball's top row overlaps paddle's bottom row: hit", LF, 119, 0, 1);
        a.do_load(14, 90 + 32, 1, 1, 90, PY); a.do_tick;
        check_ball("ball just below the left paddle: miss", 11, 120, 1, 1);
        a.do_load(11, 100, 1, 0, 90, PY); a.do_tick;
        check_ball("ball behind the left paddle's face is not hit", 8, 102, 1, 0);

        // ---- right paddle ----
        a.do_load(RX - 4 - 2, 100, 0, 0, PY, 90); a.do_tick;
        check_ball("right paddle bounce (step crosses the face)", RX - 4, 102, 1, 0);
        a.do_load(RX - 4 - 3, 100, 0, 0, PY, 90); a.do_tick;
        check_ball("right paddle bounce (step lands on the face)", RX - 4, 102, 1, 0);
        a.do_load(RX - 4 - 4, 100, 0, 0, PY, 90); a.do_tick;
        check_ball("one step short of the right paddle", RX - 5, 102, 0, 0);
        a.do_load(RX - 6, 90 - 3, 0, 0, PY, 90); a.do_tick;
        check_ball("right paddle top-row hit", RX - 4, 89, 1, 0);
        a.do_load(RX - 6, 90 + 32, 0, 1, PY, 90); a.do_tick;
        check_ball("ball just below the right paddle: miss", RX - 3, 120, 0, 1);

        // ---- walls ----
        a.do_load(100, 3, 0, 1, PY, PY); a.do_tick;
        check_ball("moving up, not yet at the top wall", 103, 1, 0, 1);
        a.do_tick;
        check_ball("top wall bounce", 106, 0, 0, 0);
        a.do_load(100, 2, 1, 1, PY, PY); a.do_tick;
        check_ball("top wall bounce (lands on the edge)", 97, 0, 1, 0);
        a.do_load(100, 233, 0, 0, PY, PY); a.do_tick;
        check_ball("moving down, not yet at the bottom wall", 103, 235, 0, 0);
        a.do_tick;
        check_ball("bottom wall bounce", 106, 236, 0, 1);
        a.do_load(LF + 2, 234, 1, 0, 208, PY); a.do_tick;
        check_ball("paddle and wall bounce together", LF, 236, 0, 1);
        a.do_load(100, 0, 0, 0, PY, PY);  // leave the top in the right direction
        a.do_tick;
        check_ball("ball at the top moving down", 103, 2, 0, 0);

        // ---- points ----
        a.do_load(5, 50, 1, 1, 150, PY); a.do_tick;
        check_ball("left miss, still on screen", 2, 48, 1, 1);
        check_score("no point yet", 0, 0, 1);
        a.do_tick;
        check_ball("after a point the ball goes to the centre, toward the loser, down", CX, CY, 1, 0);
        check_score("right player scores", 0, 1, 0);
        for (i = 0; i < 60; i = i + 1) a.do_tick;
        check_ball("serve wait after a point", CX, CY, 1, 0);
        a.do_tick;
        check_ball("serve toward the left player", CX - 3, CY + 2, 1, 0);
        a.do_load(RX - 4 + 2, 50, 0, 0, PY, 150); a.do_tick;  // behind the right paddle face
        check_ball("right miss, still on screen", RX + 1, 52, 0, 0);
        a.do_load(316 - 3, 50, 0, 0, PY, 150); a.do_tick;
        check_ball("ball reaching the right edge scores", CX, CY, 0, 0);
        check_score("left player scores", 1, 1, 0);
        a.do_load(316 - 4, 50, 0, 0, PY, 150); a.do_tick;
        check_ball("one step from the right edge: no point yet", 315, 52, 0, 0);
        check_score("one step from the right edge", 1, 1, 1);
        a.do_load(3, 50, 1, 0, 150, PY); a.do_tick;
        check_ball("ball reaching the left edge scores", CX, CY, 1, 0);
        check_score("right scores again", 1, 2, 0);
        a.do_load(4, 50, 1, 0, 150, PY); a.do_tick;
        check_ball("one step from the left edge: no point yet", 1, 52, 1, 0);

        // ---- paddles ----
        a.do_load(CX, CY, 0, 0, 7, 201);
        a.buttons(1, 0, 0, 1); a.do_tick; check_pads("paddles move by PAD_SPEED", 4, 204);
        a.do_tick; check_pads("paddles move by PAD_SPEED", 1, 207);
        a.do_tick; check_pads("paddles stop at the screen edges", 0, 208);
        a.do_tick; check_pads("paddles stay at the screen edges", 0, 208);
        a.buttons(0, 1, 1, 0); a.do_tick; check_pads("paddles move back", 3, 205);
        a.buttons(1, 1, 1, 1); a.do_tick; check_pads("both buttons held: no move", 3, 205);
        a.buttons(0, 0, 0, 0);

        // ---- winning: left player to 9, game over, restart ----
        for (i = 0; i < 20 && a.sl != 8; i = i + 1) begin a.do_load(316 - 3, 50, 0, 0, PY, 0); a.do_tick; end
        check_score("left on 8", 8, 2, 0);
        a.do_load(316 - 3, 50, 0, 0, PY, 0); a.do_tick;
        check_score("ninth point ends the game", 9, 2, 2);
        check_ball("game over: ball in the centre", CX, CY, 0, 0);
        for (i = 0; i < 179; i = i + 1) a.do_tick;
        check_score("game over lasts OVER_FRAMES ticks", 9, 2, 2);
        a.do_tick;
        check_score("after game over: scores cleared, serving", 0, 0, 0);
        for (i = 0; i < 60; i = i + 1) a.do_tick;
        a.do_tick;
        check_ball("new game serves", CX + 3, CY + 2, 0, 0);

        // ---- long randomized games on both DUTs (checked by the models) ----
        fork
            a.random_play(30000);
            b.random_play(30000);
        join

        repeat (4) @(negedge clk);
        if (a.model.errors + b.model.errors != 0)
            $display("FAIL: tb_pong_game (%0d ticks disagree with the model, first ones above; %0d directed failures)",
                     a.model.errors + b.model.errors, fails);
        else if (fails != 0)
            $display("FAIL: tb_pong_game (%0d directed checks failed, first ones above)", fails);
        else if (a.model.l_hits < 20 || a.model.r_hits < 20 || b.model.l_hits < 20 || b.model.r_hits < 20 ||
                 a.model.games < 2 || b.model.games < 5)
            $display("FAIL: tb_pong_game (random play too short: hits %0d/%0d/%0d/%0d, games %0d/%0d)",
                     a.model.l_hits, a.model.r_hits, b.model.l_hits, b.model.r_hits, a.model.games, b.model.games);
        else
            $display("PASS: tb_pong_game (directed tests; random play: %0d paddle hits, %0d wall bounces, %0d points, %0d games)",
                     a.model.l_hits + a.model.r_hits + b.model.l_hits + b.model.r_hits,
                     a.model.walls + b.model.walls, a.model.points + b.model.points, a.model.games + b.model.games);
        $finish;
    end
endmodule
`default_nettype wire
