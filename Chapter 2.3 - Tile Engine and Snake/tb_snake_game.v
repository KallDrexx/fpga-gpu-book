// tb_snake_game.v -- self-checking testbench for snake_game (Chapter 2.3).
//
// The testbench drives i_tick itself instead of running video, and keeps its
// own copy of the 40x30 tile map by watching snake_game's map write port
// (o_wr_en, o_wr_addr, o_wr_data), exactly as the tile engine would see it.
// A behavioral model of the rules runs alongside. After power-up, after
// every tick, food load and restart, it waits for o_busy to fall and then
// compares every map cell, o_length and o_game_over with the model.
//
// Food is random, so the model learns where the food went from the map: it
// must be exactly one food tile, on a cell that is free in the model (inside
// the walls, not on the snake). The script then moves the food where it wants
// it with the i_food_load port, so the moves below are the same for every
// design, whatever its LFSR does.
//
// Scripted games (DUT start: START_X=12, START_Y=10, START_LEN=4):
//   1  eat three foods (length 4 -> 7), ignore a reversal, turn, then run
//      into the body: game over; further ticks change nothing
//   2  restart, then turn back into the cell the tail occupies: game over
//      (the tail has not moved away yet)
//   3  restart, run up into the top wall: game over
//   4  restart, eat 12 foods in a row and wind around the board
//   5  restart, zigzag across the board eating at every step until the
//      snake is 240 cells long (the default MAX_LEN of 256 is enough)
// It also checks that the map is written only while o_busy is high, that
// every map write is in range, and that the random food does not always
// land on the same cell.
//
// Run: iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/tb.vvp sim/tb_snake_game.v
//      vvp -n build/tb.vvp           (add +vcd for waveforms)
`timescale 1ns/1ps
`default_nettype none

module tb_snake_game;
    localparam COLS = 40, ROWS = 30, CELLS = COLS * ROWS;
    localparam START_X = 12, START_Y = 10, START_LEN = 4;
    localparam EMPTY = 0, WALL = 1, BODY = 2, HEAD = 3, FOOD = 4;
    localparam NONE = 0, UP = 1, DOWN = 2, LEFT = 3, RIGHT = 4;
    localparam MOVE_CLOCKS = 5000, START_CLOCKS = 20000;

    reg clk = 0;
    always #20 clk = ~clk;  // 25 MHz

    reg        tick = 0, restart = 0, food_load = 0;
    reg        up = 0, down = 0, left = 0, right = 0;
    reg [5:0]  food_x = 0;
    reg [4:0]  food_y = 0;
    wire        wr_en, busy, game_over;
    wire [10:0] wr_addr, length;
    wire [7:0]  wr_data;

    snake_game #(.START_X(START_X), .START_Y(START_Y), .START_LEN(START_LEN),
                 .SEED(16'h5A3C)) dut (
        .i_clk(clk), .i_tick(tick),
        .i_up(up), .i_down(down), .i_left(left), .i_right(right),
        .i_restart(restart),
        .i_food_load(food_load), .i_food_x(food_x), .i_food_y(food_y),
        .o_wr_en(wr_en), .o_wr_addr(wr_addr), .o_wr_data(wr_data),
        .o_busy(busy), .o_game_over(game_over), .o_length(length));

    integer errors = 0;
    integer step = 0;          // operations so far, for messages
    reg [8*24-1:0] what = "power-up";

    task fail_now(input [8*80-1:0] msg);
        begin
            $display("FAIL: tb_snake_game (%0s)", msg);
            $finish;
        end
    endtask

    // ------------------------------------------------- the map, as written
    reg [7:0] map [0:CELLS-1];
    integer i;
    initial for (i = 0; i < CELLS; i = i + 1) map[i] = 8'hxx;

    reg wr_err = 0;
    always @(posedge clk) begin
        if (wr_en !== 1'b0 && wr_en !== 1'b1 && !wr_err) begin
            $display("  o_wr_en is X at time %0t", $time);
            wr_err = 1;
        end else if (wr_en === 1'b1) begin
            if (!(wr_addr < CELLS)) begin
                if (!wr_err) $display("  map write to address %0d (op %0d, %0s): valid addresses are 0-%0d",
                                      wr_addr, step, what, CELLS-1);
                wr_err = 1;
            end else begin
                map[wr_addr] = wr_data;
            end
            if (busy !== 1'b1 && !tick && !restart && !food_load && !wr_err) begin
                $display("  map written while o_busy was low (op %0d, %0s, address %0d)", step, what, wr_addr);
                wr_err = 1;
            end
        end
    end

    // ----------------------------------------------------------- the model
    // The snake is a queue of cells sx/sy[tail..head]; it only grows, so no
    // wrap-around is needed for these short games.
    integer sx [0:4095];
    integer sy [0:4095];
    reg     occ [0:CELLS-1];    // 1 where the snake is
    integer qh, qt;             // head and tail index
    integer dir;                // RIGHT, DOWN, LEFT, UP
    integer fx, fy;             // food, -1 when the model expects new food
    integer over;
    integer placements = 0, first_place = -1, distinct_place = 0;

    function integer on_snake(input integer x, input integer y);
        on_snake = (x >= 0 && x < COLS && y >= 0 && y < ROWS) ? occ[y * COLS + x] : 0;
    endfunction

    function integer is_wall(input integer x, input integer y);
        is_wall = (x <= 0 || x >= COLS-1 || y <= 0 || y >= ROWS-1);
    endfunction

    function integer model_tile(input integer x, input integer y);
        begin
            if (is_wall(x, y))                         model_tile = WALL;
            else if (sx[qh] == x && sy[qh] == y)       model_tile = HEAD;
            else if (on_snake(x, y))                   model_tile = BODY;
            else if (fx == x && fy == y)               model_tile = FOOD;
            else                                       model_tile = EMPTY;
        end
    endfunction

    task model_reset;
        integer j;
        begin
            qt = 0; qh = START_LEN - 1;
            for (j = 0; j < CELLS; j = j + 1) occ[j] = 0;
            for (j = 0; j < START_LEN; j = j + 1) begin
                sx[j] = START_X - START_LEN + 1 + j; sy[j] = START_Y;
                occ[START_Y * COLS + sx[j]] = 1;
            end
            dir = RIGHT; fx = -1; fy = -1; over = 0;
        end
    endtask

    // One tick with button b held. Returns through the model state.
    task model_tick(input integer b);
        integer nd, nx, ny;
        begin
            if (!over) begin
                nd = dir;
                if (b != NONE &&
                    !((b == LEFT && dir == RIGHT) || (b == RIGHT && dir == LEFT) ||
                      (b == UP && dir == DOWN) || (b == DOWN && dir == UP)))
                    nd = b;
                dir = nd;
                nx = sx[qh] + (nd == RIGHT) - (nd == LEFT);
                ny = sy[qh] + (nd == DOWN) - (nd == UP);
                if (is_wall(nx, ny) || on_snake(nx, ny)) begin
                    over = 1;                      // checked before the tail moves
                end else begin
                    qh = qh + 1; sx[qh] = nx; sy[qh] = ny;
                    if (nx == fx && ny == fy) begin
                        fx = -1; fy = -1;          // eaten: the snake keeps its tail
                    end else begin
                        occ[sy[qt] * COLS + sx[qt]] = 0;
                        qt = qt + 1;
                    end
                    occ[ny * COLS + nx] = 1;
                end
            end
        end
    endtask

    // ------------------------------------------------------------- checking
    // Find the food on the written map, validate it against the model, then
    // compare every cell. new_food = 1 when the design was due to place new
    // food at random (power-up, restart, or after eating).
    task check(input integer new_food);
        integer x, y, n, gx, gy, cnt, exp, bad;
        begin
            if (wr_err) fail_now("bad map write, see above");
            cnt = 0; gx = -1; gy = -1;
            for (n = 0; n < CELLS; n = n + 1)
                if (map[n] === FOOD) begin
                    cnt = cnt + 1; gx = n % COLS; gy = n / COLS;
                end
            if (cnt != 1) begin
                $display("  op %0d (%0s): the map has %0d food tiles, expected exactly 1", step, what, cnt);
                fail_now("food count");
            end
            if (new_food) begin
                if (is_wall(gx, gy) || on_snake(gx, gy)) begin
                    $display("  op %0d (%0s): new food placed at (%0d,%0d), which is not a free cell",
                             step, what, gx, gy);
                    fail_now("food placed on the snake or the wall");
                end
                fx = gx; fy = gy;
                placements = placements + 1;
                if (first_place < 0) first_place = gy * COLS + gx;
                else if (gy * COLS + gx != first_place) distinct_place = 1;
            end
            bad = 0;
            for (y = 0; y < ROWS && !bad; y = y + 1)
                for (x = 0; x < COLS && !bad; x = x + 1) begin
                    exp = model_tile(x, y);
                    if (map[y * COLS + x] !== exp) begin
                        $display("  op %0d (%0s): map cell (%0d,%0d) = %0d, expected %0d (0 empty, 1 wall, 2 body, 3 head, 4 food)",
                                 step, what, x, y, map[y * COLS + x], exp);
                        bad = 1;
                    end
                end
            if (bad) fail_now("map differs from the model, see above");
            if (length !== qh - qt + 1) begin
                $display("  op %0d (%0s): o_length = %0d, expected %0d", step, what, length, qh - qt + 1);
                fail_now("o_length");
            end
            if (game_over !== over[0]) begin
                $display("  op %0d (%0s): o_game_over = %b, expected %0d", step, what, game_over, over);
                fail_now("o_game_over");
            end
        end
    endtask

    // Wait (on falling edges) until o_busy is low, then two more clocks so
    // that a write arriving after o_busy fell is caught as an error.
    task wait_idle(input integer limit);
        integer n;
        begin
            n = 0;
            while (busy !== 1'b0) begin
                @(negedge clk);
                n = n + 1;
                if (n > limit) begin
                    $display("  op %0d (%0s): o_busy still high after %0d clocks", step, what, limit);
                    fail_now("timeout waiting for o_busy to fall");
                end
            end
            repeat (2) @(negedge clk);
        end
    endtask

    task press(input integer b);
        begin
            up = (b == UP); down = (b == DOWN); left = (b == LEFT); right = (b == RIGHT);
        end
    endtask

    // One game tick with button b held from 2 clocks before the tick until
    // the move is done.
    task do_tick(input integer b, input integer expect_over);
        integer ate;
        begin
            step = step + 1; what = "tick";
            @(negedge clk); press(b);
            repeat (2) @(negedge clk);
            tick = 1;
            @(negedge clk); tick = 0;
            wait_idle(MOVE_CLOCKS);
            @(negedge clk); press(NONE);
            ate = (fx >= 0);
            model_tick(b);
            ate = ate && (fx < 0);
            if (over != expect_over) begin
                $display("  op %0d: testbench script error, model game over = %0d", step, over);
                fail_now("testbench script error");
            end
            check(ate);
        end
    endtask

    // Move the food to (x, y), a free cell. Skipped when it is already there.
    task do_food(input integer x, input integer y);
        begin
            if (fx == x && fy == y) disable do_food;
            step = step + 1; what = "food load";
            @(negedge clk);
            food_x = x; food_y = y; food_load = 1;
            @(negedge clk); food_load = 0;
            wait_idle(MOVE_CLOCKS);
            fx = x; fy = y;
            check(0);
        end
    endtask

    task do_restart;
        begin
            step = step + 1; what = "restart";
            for (i = 0; i < CELLS; i = i + 1) map[i] = 8'hxx;  // every cell must be rewritten
            @(negedge clk); restart = 1;
            @(negedge clk); restart = 0;
            wait_idle(START_CLOCKS);
            model_reset;
            check(1);
        end
    endtask

    integer k, b, nd, going_down, rowdir;
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("build/tb_snake_game.vcd");
            $dumpvars(0, tb_snake_game);
        end
    end

    initial begin : watchdog
        #(40 * 2000000);
        $display("FAIL: tb_snake_game watchdog timeout");
        $finish;
    end

    initial begin
        // ---- power-up: the game draws the whole screen by itself
        model_reset;
        #1;
        if (busy !== 1'b1) fail_now("o_busy must start high while the first screen is drawn");
        wait_idle(START_CLOCKS);
        check(1);

        // ---- game 1: eat, ignore a reversal, turn, bite the body
        do_food(15, 10);
        do_tick(NONE, 0); do_tick(NONE, 0);
        do_tick(NONE, 0);                    // eats at (15,10): length 5
        do_food(16, 13);
        do_tick(LEFT, 0);                    // reversal ignored: (16,10)
        do_tick(DOWN, 0); do_tick(NONE, 0);  // (16,11) (16,12)
        do_tick(NONE, 0);                    // eats at (16,13): length 6
        do_food(15, 13);
        do_tick(LEFT, 0);                    // eats at (15,13): length 7
        do_food(30, 25);
        do_tick(UP, 0);                      // (15,12)
        do_tick(RIGHT, 1);                   // (16,12) is body: game over
        do_tick(DOWN, 1); do_tick(NONE, 1);  // no effect after game over

        // ---- game 2: the cell the tail is leaving still counts
        do_restart;
        do_food(30, 25);
        do_tick(DOWN, 0);                    // (12,11)
        do_tick(LEFT, 0);                    // (11,11)
        do_tick(UP, 1);                      // (11,10) is the tail: game over

        // ---- game 3: the wall
        do_restart;
        do_food(30, 25);
        for (k = 0; k < START_Y - 1; k = k + 1) do_tick(UP, 0);   // up to y = 1
        do_tick(NONE, 1);                    // y = 0 is wall: game over

        // ---- game 4: a long snake, eating with every step for a while
        do_restart;
        for (k = 0; k < 6; k = k + 1) begin  // right along row 10: length 10
            do_food(13 + k, 10);
            do_tick(NONE, 0);
        end
        do_food(30, 25);
        do_tick(DOWN, 0);                    // (18,11)
        for (k = 0; k < 6; k = k + 1) begin  // down column 18: length 16
            do_food(18, 12 + k);
            do_tick(NONE, 0);
        end
        do_food(2, 2);
        do_tick(LEFT, 0); do_tick(NONE, 0); do_tick(UP, 0);       // (17,17) (16,17) (16,16)
        for (k = 0; k < 6; k = k + 1) do_tick(NONE, 0);           // up to (16,10)
        do_tick(LEFT, 0); do_tick(DOWN, 0);                       // (15,10) (15,11)
        do_tick(RIGHT, 1);                                        // (16,11): game over

        // ---- game 5: fill rows 10 and down in a zigzag, eating at every
        // step, until the snake is 240 long. Most of the free cells are snake
        // by the end, so food placed without checking the map would soon land
        // on it.
        do_restart;
        rowdir = RIGHT; going_down = 0;
        while (qh - qt + 1 < 240) begin
            if (going_down) begin
                b = rowdir; going_down = 0;
            end else if ((rowdir == RIGHT && sx[qh] == COLS-2) || (rowdir == LEFT && sx[qh] == 1)) begin
                b = DOWN; going_down = 1; rowdir = (rowdir == RIGHT) ? LEFT : RIGHT;
            end else begin
                b = NONE;
            end
            nd = (b == NONE) ? dir : b;
            do_food(sx[qh] + (nd == RIGHT) - (nd == LEFT), sy[qh] + (nd == DOWN) - (nd == UP));
            do_tick(b, 0);
        end
        do_food(1, 1);
        do_tick(NONE, 0);
        do_tick(UP, 1);                      // into the row above: game over

        if (!distinct_place) begin
            $display("  all %0d random food placements landed on the same cell", placements);
            fail_now("food placement is not random");
        end
        $display("PASS: tb_snake_game (%0d operations, %0d random food placements checked)", step, placements);
        $finish;
    end
endmodule
`default_nettype wire
