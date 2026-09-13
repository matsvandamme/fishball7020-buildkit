% gen_fir_coe.m - generate a Xilinx FIR Compiler .coe file for this board's RX path.
%
% Produces a coefficient file in exactly the format/scaling the stock
% util_fir_dec / util_fir_int IP cores expect:
%   Radix 10, 16-bit SIGNED INTEGER coefficients (the IP is configured with
%   Quantization = Integer_Coefficients and Coefficient_Fractional_Bits = 0,
%   so the numbers in this file are used as literal integers).
%
% WHICH FILE DOES THE RX PATH ACTUALLY USE?  Not the obvious one.
% system_bd.tcl passes library/util_fir_int/coefile_int.coe to BOTH the RX
% decimator and the TX interpolator:
%   ad_add_decimation_filter    "rx_fir_decimator"   8 2 1 {61.44} {61.44} \
%                               ".../util_fir_int/coefile_int.coe"
%   ad_add_interpolation_filter "tx_fir_interpolator" 8 2 1 {61.44} {7.68} \
%                               ".../util_fir_int/coefile_int.coe"
% library/util_fir_dec/coefile_dec.coe is NOT used by this project at all
% (util_fir_dec has no component.xml - it is never packaged or instantiated).
% Because RX and TX share one file, this script writes a SEPARATE file and
% you repoint only the RX line - otherwise you would narrow your transmitter
% to the FM channel bandwidth as a side effect.
%
% SCALING - this is the part that silently breaks things if you get it wrong.
% The stock coefile_int.coe has sum(h) = 131074 (~2^17) and max|h| = 2^14.
% That DC gain is what the rest of the datapath is built around. Keep
% TARGET_DC_GAIN at 2^17 and your filter drops in at the same output level as
% the stock one. If you see clipping, halve it - do NOT rescale to
% "max coefficient = 32767", which changes the passband gain arbitrarily.
%
% Requires: Signal Processing Toolbox (firpm). See README for the workflow.

clear; close all;

%% ------------------------------------------------------------------
%% Configuration - edit this block
%% ------------------------------------------------------------------

% Default: FM broadcast channel (+/-100 kHz) captured through the stock
% 8x decimator, with the AD9361 at its minimum sample rate.
Fs_in   = 2083333;      % sample rate at the FILTER INPUT [Hz]
D       = 8;            % decimation rate of the IP this file feeds (1 = single rate)
Fpass   = 100e3;        % passband edge [Hz]
Astop   = 60;           % target stopband attenuation [dB] (informational)
Ntaps   = 129;          % must match the tap count the IP was generated with

COEF_WIDTH     = 16;    % CONFIG.Coefficient_Width
TARGET_DC_GAIN = 2^17;  % matches stock coefile_int.coe - see note above
OUTFILE        = 'coefile_fm.coe';   % new file; repoint only the RX line

%% ------------------------------------------------------------------
%% Stopband edge
%% ------------------------------------------------------------------
% For a decimating filter, the frequency that matters is NOT the output
% Nyquist - it is the lowest input frequency that folds ONTO the passband
% after decimation, which is (Fs_out - Fpass). Content between Fs_out/2 and
% that point aliases into (Fpass .. Fs_out/2), which is outside the band you
% care about. Using the output Nyquist instead would demand a far narrower
% transition and many more taps for no benefit.
Fs_out = Fs_in / D;
if D > 1
    Fstop = Fs_out - Fpass;
else
    Fstop = Fpass * 1.6;          % single-rate: pick your own transition
end

assert(Fstop > Fpass,  'Fstop must exceed Fpass - lower Fpass or reduce D.');
assert(Fstop < Fs_in/2, 'Fstop exceeds input Nyquist - reduce D or Fpass.');

fprintf('Fs_in  = %.0f Hz\n', Fs_in);
fprintf('Fs_out = %.0f Hz (decimate by %d)\n', Fs_out, D);
fprintf('Fpass  = %.1f kHz\n', Fpass/1e3);
fprintf('Fstop  = %.1f kHz  (first alias folding into passband)\n', Fstop/1e3);

%% ------------------------------------------------------------------
%% Design (Parks-McClellan, stopband weighted)
%% ------------------------------------------------------------------
N = Ntaps - 1;
h = firpm(N, [0 Fpass Fstop Fs_in/2]/(Fs_in/2), [1 1 0 0], [1 10]);

%% ------------------------------------------------------------------
%% Quantize to signed integers at the stock DC gain
%% ------------------------------------------------------------------
hq = round(h * TARGET_DC_GAIN / sum(h));

lim = 2^(COEF_WIDTH-1);
if max(abs(hq)) > lim-1
    error(['Coefficients overflow %d-bit signed (peak %d > %d). ' ...
           'Widen the transition band, or halve TARGET_DC_GAIN.'], ...
          COEF_WIDTH, max(abs(hq)), lim-1);
end

% Correct any rounding drift so the DC gain lands exactly on target: nudge
% the centre (largest) tap, where a +/-1 change is least significant.
[~, kmax] = max(abs(hq));
hq(kmax) = hq(kmax) + (TARGET_DC_GAIN - sum(hq));

fprintf('\ntaps    = %d\n', numel(hq));
fprintf('sum(h)  = %d  (target %d)\n', sum(hq), TARGET_DC_GAIN);
fprintf('max|h|  = %d  (limit %d)\n', max(abs(hq)), lim-1);

%% ------------------------------------------------------------------
%% Verify BEFORE spending an hour on a rebuild
%% ------------------------------------------------------------------
[H, f] = freqz(hq/TARGET_DC_GAIN, 1, 8192, Fs_in);
HdB = 20*log10(abs(H) + eps);
achieved = max(HdB(f >= Fstop));
fprintf('stopband (>= %.1f kHz) = %.1f dB  (target -%d dB)\n', ...
        Fstop/1e3, achieved, Astop);
if achieved > -Astop
    warning(['Stopband is only %.1f dB. Increase Ntaps (and regenerate the ' ...
             'IP to match), widen the transition, or accept it.'], achieved);
end

figure;
plot(f/1e3, HdB); grid on;
xline(Fpass/1e3, 'g--', 'Fpass'); xline(Fstop/1e3, 'r--', 'Fstop');
yline(-Astop, 'r:'); xlim([0 Fs_in/2/1e3]); ylim([-120 10]);
xlabel('Frequency [kHz]'); ylabel('Magnitude [dB]');
title(sprintf('%d taps, %.0f kHz passband @ %.3f MSPS, /%d', ...
      numel(hq), Fpass/1e3, Fs_in/1e6, D));

%% ------------------------------------------------------------------
%% Write the .coe
%% ------------------------------------------------------------------
fid = fopen(OUTFILE, 'w');
fprintf(fid, '; Xilinx FIR Compiler coefficient file\n');
fprintf(fid, '; Generated by gen_fir_coe.m on %s\n', datestr(now));
fprintf(fid, '; %d taps, %d-bit signed integer, sum(h) = %d\n', ...
        numel(hq), COEF_WIDTH, sum(hq));
fprintf(fid, '; Fs_in = %.0f Hz, decimate by %d, Fpass = %.0f Hz, Fstop = %.0f Hz\n', ...
        Fs_in, D, Fpass, Fstop);
fprintf(fid, 'Radix = 10;\n');
fprintf(fid, 'Coefficient_Width = %d;\n', COEF_WIDTH);
fprintf(fid, 'CoefData = %d', hq(1));
fprintf(fid, ',\n%d', hq(2:end));
fprintf(fid, ';\n');
fclose(fid);

fprintf('\nWrote %s\n', OUTFILE);
