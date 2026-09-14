// Self-checking testbench for ad_fs4_ddc.
//
// Verifies the module against a golden model of what it is supposed to be:
// multiplication by exp(-j*pi*n/2). Every check is exact integer arithmetic -
// the Fs/4 shift is a swap and a sign flip, so there is no rounding anywhere
// and no tolerance to argue about. A mismatch is a bug, full stop.
//
// The test that earns its keep is GAPPED VALID. The phase counter must
// advance once per SAMPLE, not once per clock, and on this board valid is
// genuinely intermittent: in 2R2T mode the AD9361 asserts adc_valid every
// second clock. Advancing on the clock instead looks correct in a
// back-to-back simulation and puts the channel at the wrong frequency on
// hardware, which costs a 20 minute rebuild and a flash to discover.
//
//   run from firmware/:  ./sim/run_sim.sh

`timescale 1ns/100ps

module tb_ad_fs4_ddc;

  localparam integer W = 16;

  reg                  clk = 1'b0;
  reg                  valid_in = 1'b0;
  reg  signed [W-1:0]  i_in = 0, q_in = 0;
  wire                 valid_out;
  wire signed [W-1:0]  i_out, q_out;

  integer errors  = 0;
  integer checks  = 0;

  always #5 clk = ~clk;

  ad_fs4_ddc #(.DATA_WIDTH(W)) dut (
    .clk(clk), .valid_in(valid_in), .i_in(i_in), .q_in(q_in),
    .valid_out(valid_out), .i_out(i_out), .q_out(q_out));

  // -- golden model -------------------------------------------------------
  // Mirrors the specification, not the implementation: an independent phase
  // counter, advanced only when a sample is accepted.
  reg [1:0] gold_phase = 2'd0;
  reg signed [W-1:0] gold_i, gold_q;

  task automatic golden(input signed [W-1:0] i, input signed [W-1:0] q);
    begin
      case (gold_phase)
        2'd0: begin gold_i =  i; gold_q =  q; end   // x  1
        2'd1: begin gold_i =  q; gold_q = -i; end   // x -j
        2'd2: begin gold_i = -i; gold_q = -q; end   // x -1
        2'd3: begin gold_i = -q; gold_q =  i; end   // x +j
      endcase
      gold_phase = gold_phase + 2'd1;
    end
  endtask

  task automatic check(input [255:0] what);
    begin
      checks = checks + 1;
      if (i_out !== gold_i || q_out !== gold_q) begin
        errors = errors + 1;
        $display("  FAIL %0s: got (%0d,%0d) expected (%0d,%0d) at t=%0t", what, i_out, q_out, gold_i, gold_q, $time);
      end
    end
  endtask

  // Drive one sample, then check the output it produced.
  task automatic feed(input signed [W-1:0] i, input signed [W-1:0] q,
                      input [255:0] what);
    begin
      @(negedge clk);
      i_in = i; q_in = q; valid_in = 1'b1;
      golden(i, q);
      @(negedge clk);
      valid_in = 1'b0;
      if (valid_out !== 1'b1)
        begin
          errors = errors + 1;
          $display("  FAIL %0s: valid_out low one clock after valid_in", what);
        end
      check(what);
    end
  endtask

  task automatic idle(input integer n);
    integer k;
    begin
      for (k = 0; k < n; k = k + 1) begin
        @(negedge clk);
        valid_in = 1'b0;
      end
    end
  endtask

  integer n, gap, seed = 32'hf1_5b_a1_17;
  reg signed [W-1:0] hold_i, hold_q;
  reg signed [W-1:0] ti, tq;

  initial begin
    $display("tb_ad_fs4_ddc");

    // -- 1. back-to-back samples, random data ----------------------------
    $display("  [1] 200 random samples, valid every clock");
    for (n = 0; n < 200; n = n + 1)
      feed($dist_uniform(seed, -2048, 2047), $dist_uniform(seed, -2048, 2047),
           "random back-to-back");

    // -- 2. gapped valid -------------------------------------------------
    // The phase must track SAMPLES, not clocks. This is the check that
    // catches a counter moved outside the `if (valid_in)`.
    $display("  [2] 200 random samples with random 0-3 clock gaps");
    for (n = 0; n < 200; n = n + 1) begin
      gap = $dist_uniform(seed, 0, 3);
      idle(gap);
      feed($dist_uniform(seed, -2048, 2047), $dist_uniform(seed, -2048, 2047),
           "random with gaps");
    end

    // -- 3. what the module is FOR ---------------------------------------
    // A tone at exactly +Fs/4 must come out as DC. Integer-exact: the four
    // samples of that tone are (A,0), (0,A), (-A,0), (0,-A), and each phase
    // maps its own sample onto (A,0).
    $display("  [3] a tone at +Fs/4 becomes DC (with gaps, so it is a real test)");
    gold_phase = dut.phase;                       // align to the DUT's phase
    begin : tone_at_fs4
      integer amp; amp = 1500;
      for (n = 0; n < 64; n = n + 1) begin
        case ((n + 0) % 4)
          0: begin ti =  amp; tq =     0; end
          1: begin ti =     0; tq =  amp; end
          2: begin ti = -amp; tq =     0; end
          3: begin ti =     0; tq = -amp; end
        endcase
        idle($dist_uniform(seed, 0, 2));
        // Only meaningful if the DUT's phase matches the tone's sample index.
        if (dut.phase !== (n % 4)) begin
          errors = errors + 1;
          $display("  FAIL tone: phase %0d does not track sample %0d", dut.phase, n % 4);
        end
        feed(ti, tq, "tone at +Fs/4");
        if (i_out !== amp || q_out !== 0) begin
          errors = errors + 1;
          $display("  FAIL tone: +Fs/4 did not land at DC: got (%0d,%0d) expected (%0d,0)", i_out, q_out, amp);
        end
      end
    end

    // -- 4. the converse: DC in must rotate ------------------------------
    $display("  [4] DC in comes out rotating through the four quadrants");
    begin : dc_rotates
      integer seen_i [0:3];
      integer seen_q [0:3];
      integer k;
      for (n = 0; n < 4; n = n + 1) begin
        feed(1000, 0, "dc rotation");
        seen_i[n] = i_out; seen_q[n] = q_out;
      end
      // Over one full cycle the four outputs must be the four rotations of
      // (1000,0): their sum is zero if and only if all four are present.
      k = 0;
      for (n = 0; n < 4; n = n + 1) k = k + seen_i[n] + seen_q[n];
      if (k !== 0) begin
        errors = errors + 1;
        $display("  FAIL dc: four outputs do not sum to zero (got %0d), so the phase is not cycling through all four states", k);
      end
    end

    // -- 5. data holds between samples -----------------------------------
    $display("  [5] outputs hold their value while valid_in is low");
    feed(777, -321, "before hold");
    hold_i = i_out; hold_q = q_out;
    idle(5);
    if (i_out !== hold_i || q_out !== hold_q) begin
      errors = errors + 1;
      $display("  FAIL hold: outputs moved with no valid input");
    end
    if (valid_out !== 1'b0) begin
      errors = errors + 1;
      $display("  FAIL hold: valid_out still high with valid_in low");
    end

    // -- 6. the documented input range, including its endpoints ----------
    // ad_datafmt.v sign-extends 12-bit converter data into 16 bits, so the
    // range is [-2048, +2047]. Negation of -2048 is representable in 16 bits;
    // the comment in the module explains why no clamp is needed. Prove it.
    $display("  [6] the endpoints of the documented input range");
    feed(-2048, -2048, "min endpoint");
    feed( 2047,  2047, "max endpoint");
    feed(-2048,  2047, "mixed endpoints");
    feed( 2047, -2048, "mixed endpoints");

    $display("");
    if (errors == 0)
      $display("  PASS  %0d checks, no mismatches against the golden model", checks);
    else
      $display("  FAIL  %0d of %0d checks failed", errors, checks);
    $display("");
    $finish;
  end

endmodule
