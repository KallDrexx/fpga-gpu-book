// OSERDESE2 -- behavioral simulation model for Icarus Verilog (sim only).
//
// Supplied by the book (Chapter 3.4). Copy to sim/OSERDESE2.v. Do NOT add it
// to a project's SRCS: yosys knows the real primitive, and this file is only
// for testbenches (iverilog finds it through -y sim).
//
// What it models (7-series OSERDESE2, UG471 chapter 3):
//   * DATA_RATE_OQ = "DDR" with DATA_WIDTH 4, 6 or 8 (one OSERDESE2), or 10
//     or 14 (a MASTER and a SLAVE joined by SHIFTOUT1/2 -> SHIFTIN1/2).
//     Bit order as on silicon: D1 of the MASTER leaves first, then D2 ... D8,
//     then the SLAVE's D3, D4 (and D5 ... D8 for width 14). The SLAVE's D1
//     and D2 are unused.
//   * The parallel word is taken on the rising edge of CLKDIV and shifted
//     out on both edges of CLK, which must run at DATA_WIDTH/2 times CLKDIV
//     and be phase-aligned to it (both from one MMCM). Each word starts on
//     the first CLK rising edge after the CLKDIV edge that took it. Silicon
//     has a different (also fixed) latency; never depend on this one.
//   * RST drives OQ to SRVAL_OQ (0) at once, with or without clocks. Its
//     release is sampled on a CLKDIV rising edge, so release it
//     synchronously to CLKDIV (the pixel domain's reset). UG471 requires a
//     reset after CLK and CLKDIV are stable; the model's OQ and SHIFTOUT
//     are X until RST has been high at a CLKDIV edge with CLK running. A
//     MASTER and its SLAVE must share the same RST so they leave reset
//     together.
//   * Checks that report an error and drive OQ to X: wrong clock ratio
//     (CLK rising edges per CLKDIV period != DATA_WIDTH/2), OCE not 1 while
//     running, unsupported parameters. A SLAVE's OQ and a MASTER's SHIFTOUT
//     are X: on silicon they carry nothing useful.
//
// Not modeled: the 3-state path (TQ/TFB follow nothing, T1-T4, TCE are
// ignored), OFB (copies OQ), TBYTE*, IS_*_INVERTED, SDR mode, glitches,
// setup/hold.

`timescale 1ns/1ps
`default_nettype none

module OSERDESE2 #(
    parameter         DATA_RATE_OQ   = "DDR",
    parameter         DATA_RATE_TQ   = "DDR",
    parameter integer DATA_WIDTH     = 4,
    parameter [0:0]   INIT_OQ        = 1'b0,
    parameter [0:0]   INIT_TQ        = 1'b0,
    parameter         SERDES_MODE    = "MASTER",
    parameter [0:0]   SRVAL_OQ       = 1'b0,
    parameter [0:0]   SRVAL_TQ       = 1'b0,
    parameter         TBYTE_CTL      = "FALSE",
    parameter         TBYTE_SRC      = "FALSE",
    parameter integer TRISTATE_WIDTH = 4,
    parameter [0:0]   IS_CLKDIV_INVERTED = 1'b0,
    parameter [0:0]   IS_CLK_INVERTED    = 1'b0,
    parameter [0:0]   IS_D1_INVERTED = 1'b0, IS_D2_INVERTED = 1'b0,
    parameter [0:0]   IS_D3_INVERTED = 1'b0, IS_D4_INVERTED = 1'b0,
    parameter [0:0]   IS_D5_INVERTED = 1'b0, IS_D6_INVERTED = 1'b0,
    parameter [0:0]   IS_D7_INVERTED = 1'b0, IS_D8_INVERTED = 1'b0,
    parameter [0:0]   IS_T1_INVERTED = 1'b0, IS_T2_INVERTED = 1'b0,
    parameter [0:0]   IS_T3_INVERTED = 1'b0, IS_T4_INVERTED = 1'b0
) (
    output wire OFB,
    output reg  OQ = 1'bx,
    output wire SHIFTOUT1,
    output wire SHIFTOUT2,
    output wire TBYTEOUT,
    output wire TFB,
    output wire TQ,
    input  wire CLK,
    input  wire CLKDIV,
    input  wire D1, D2, D3, D4, D5, D6, D7, D8,
    input  wire OCE,
    input  wire RST,
    input  wire SHIFTIN1,
    input  wire SHIFTIN2,
    input  wire T1, T2, T3, T4,
    input  wire TBYTEIN,
    input  wire TCE
);
    localparam IS_MASTER = (SERDES_MODE == "MASTER");
    localparam IS_SLAVE  = (SERDES_MODE == "SLAVE");
    localparam integer CYC = DATA_WIDTH / 2;        // CLK cycles per word

    // ------------------------------------------------ parameter checks
    reg cfg_ok = 1'b1;
    initial begin
        #0;
        if (DATA_RATE_OQ != "DDR") begin
            $display("OSERDESE2 ERROR (%m): this model supports DATA_RATE_OQ = \"DDR\" only (got \"%0s\")", DATA_RATE_OQ);
            cfg_ok = 1'b0;
        end
        if (!(DATA_WIDTH == 4 || DATA_WIDTH == 6 || DATA_WIDTH == 8 ||
              DATA_WIDTH == 10 || DATA_WIDTH == 14)) begin
            $display("OSERDESE2 ERROR (%m): DATA_WIDTH = %0d; DDR allows 4, 6, 8, 10 or 14", DATA_WIDTH);
            cfg_ok = 1'b0;
        end
        if (!IS_MASTER && !IS_SLAVE) begin
            $display("OSERDESE2 ERROR (%m): SERDES_MODE must be \"MASTER\" or \"SLAVE\" (got \"%0s\")", SERDES_MODE);
            cfg_ok = 1'b0;
        end
        if (IS_SLAVE && DATA_WIDTH <= 8)
            $display("OSERDESE2 warning (%m): a SLAVE is only needed for DATA_WIDTH 10 or 14");
        if (DATA_WIDTH > 8 && TRISTATE_WIDTH != 1)
            $display("OSERDESE2 warning (%m): DATA_WIDTH = %0d needs TRISTATE_WIDTH = 1 (UG471)", DATA_WIDTH);
    end

    // ------------------------------------------------ CLKDIV side
    // Take the word and sample RST on each CLKDIV rising edge.
    reg [7:0] r_par    = 8'h00;
    reg       r_tog    = 1'b0;     // toggles once per CLKDIV edge
    reg       r_rst    = 1'b1;     // RST as sampled by CLKDIV
    reg       r_seen   = 1'b0;     // a reset has been seen with CLK running
    reg       r_clk_on = 1'b0;     // CLK has toggled

    always @(posedge CLK) r_clk_on <= 1'b1;

    always @(posedge CLKDIV) begin
        r_par <= {D8, D7, D6, D5, D4, D3, D2, D1};
        r_tog <= ~r_tog;
        r_rst <= RST;
        if (RST === 1'b1 && r_clk_on) r_seen <= 1'b1;
    end

    // ------------------------------------------------ CLK side
    // r_sr[1:0] are the two bits for this CLK cycle (r_sr[0] on the rising
    // edge, r_sr[1] on the falling edge). Each cycle the register moves two
    // places down, and a MASTER fills the top from SHIFTIN2/SHIFTIN1.
    reg [7:0] r_sr     = 8'hxx;
    reg       r_tog_q  = 1'b0;
    reg       r_run    = 1'b0;     // out of reset, shifting
    reg       r_bad    = 1'b0;
    integer   n_cyc    = 0;        // CLK cycles since the last load
    integer   n_err    = 0;
    reg       oce_warned = 1'b0;

    always @(posedge CLK) begin
        r_tog_q <= r_tog;
        if (!cfg_ok || !r_seen) begin
            r_run <= 1'b0;
            r_sr  <= 8'hxx;
        end else if (r_rst !== 1'b0) begin
            r_run <= 1'b0;
            r_bad <= 1'b0;
            r_sr  <= {8{SRVAL_OQ}};
        end else if (r_tog !== r_tog_q) begin
            // first CLK rising edge after a CLKDIV edge: load a new word
            if (r_run && n_cyc != CYC && !r_bad) begin
                if (n_err < 3)
                    $display("OSERDESE2 ERROR (%m) at %0t: %0d CLK cycles per CLKDIV period; DATA_WIDTH = %0d in DDR needs CLK = %0d x CLKDIV, phase-aligned",
                             $time, n_cyc, DATA_WIDTH, CYC);
                n_err = n_err + 1;
                r_bad <= 1'b1;
            end
            r_run <= 1'b1;
            n_cyc  = 1;
            r_sr  <= r_par;
        end else begin
            n_cyc  = n_cyc + 1;
            r_sr  <= {IS_MASTER ? SHIFTIN2 : 1'b0, IS_MASTER ? SHIFTIN1 : 1'b0, r_sr[7:2]};
        end
        if (r_run && OCE !== 1'b1 && !oce_warned) begin
            $display("OSERDESE2 ERROR (%m) at %0t: OCE is %b; tie it to 1", $time, OCE);
            oce_warned = 1'b1;
        end
    end

    wire w_valid = cfg_ok && r_seen && !r_bad &&
                   (r_rst !== 1'b0 || OCE === 1'b1);

    // OQ: r_sr[0] after the rising edge, r_sr[1] after the falling edge.
    always @(posedge CLK or negedge CLK or posedge RST) begin
        #0.001;                                       // after r_sr updates
        if (!IS_MASTER || !w_valid)        OQ = 1'bx;
        else if (r_rst !== 1'b0 || RST === 1'b1) OQ = SRVAL_OQ;
        else if (!r_run)                   OQ = SRVAL_OQ;
        else if (CLK)                      OQ = r_sr[0];
        else                               OQ = r_sr[1];
    end

    // A SLAVE passes its next two bits to the MASTER, which takes them on the
    // following CLK rising edge: the slave's D3/D4 reach the MASTER's
    // register top on the cycle after the load and leave after the MASTER's
    // own D7/D8.
    assign SHIFTOUT1 = (IS_SLAVE && w_valid) ? r_sr[2] : 1'bx;
    assign SHIFTOUT2 = (IS_SLAVE && w_valid) ? r_sr[3] : 1'bx;

    assign OFB      = OQ;
    assign TQ       = 1'b0;
    assign TFB      = 1'b0;
    assign TBYTEOUT = 1'b0;
endmodule

`default_nettype wire
