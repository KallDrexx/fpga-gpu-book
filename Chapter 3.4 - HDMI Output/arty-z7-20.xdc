# Digilent Arty Z7-20 (xc7z020clg400-1) pin constraints for openXC7.
# Copy to boards/arty-z7-20/arty-z7-20.xdc. This is Chapter 3.1's file with
# the HDMI TX lines turned on (Chapter 3.4); nothing else changed.
#
# Pins follow Digilent's Arty-Z7-20-Master.xdc, and so do the port names, so
# Digilent's examples drop straight in, except the clock: Digilent's sysclk
# is clk here. Two changes for nextpnr-xilinx:
#
#   * No spaces inside the get_ports braces: write [get_ports {led[0]}],
#     not Digilent's [get_ports { led[0] }], and no trailing semicolons.
#     The openXC7 XDC parser crashes on the first and rejects the second.
#   * One line per pin, with every PL pin a later chapter needs. Lines whose
#     port is not in the design are skipped silently (nextpnr --verbose
#     lists them), so every project shares this one file.
#
# Every pin here is 3.3 V (LVCMOS33), except the four HDMI TMDS pairs, which
# are TMDS_33 and need an OBUFDS each.

## 125 MHz clock from the Ethernet PHY, IO_L13P_T2_MRCC_35 (a clock-capable pin)
set_property -dict {PACKAGE_PIN H16 IOSTANDARD LVCMOS33} [get_ports {clk}]
create_clock -period 8.000 [get_ports {clk}]

## Slide switches (high when up, toward the board edge)
set_property -dict {PACKAGE_PIN M20 IOSTANDARD LVCMOS33} [get_ports {sw[0]}]
set_property -dict {PACKAGE_PIN M19 IOSTANDARD LVCMOS33} [get_ports {sw[1]}]

## Push buttons BTN0-BTN3 (high while pressed)
set_property -dict {PACKAGE_PIN D19 IOSTANDARD LVCMOS33} [get_ports {btn[0]}]
set_property -dict {PACKAGE_PIN D20 IOSTANDARD LVCMOS33} [get_ports {btn[1]}]
set_property -dict {PACKAGE_PIN L20 IOSTANDARD LVCMOS33} [get_ports {btn[2]}]
set_property -dict {PACKAGE_PIN L19 IOSTANDARD LVCMOS33} [get_ports {btn[3]}]

## Green LEDs LD0-LD3 (active high)
set_property -dict {PACKAGE_PIN R14 IOSTANDARD LVCMOS33} [get_ports {led[0]}]
set_property -dict {PACKAGE_PIN P14 IOSTANDARD LVCMOS33} [get_ports {led[1]}]
set_property -dict {PACKAGE_PIN N16 IOSTANDARD LVCMOS33} [get_ports {led[2]}]
set_property -dict {PACKAGE_PIN M14 IOSTANDARD LVCMOS33} [get_ports {led[3]}]

## RGB LEDs LD4 and LD5 (active high, very bright at full drive)
set_property -dict {PACKAGE_PIN N15 IOSTANDARD LVCMOS33} [get_ports {led4_r}]
set_property -dict {PACKAGE_PIN G17 IOSTANDARD LVCMOS33} [get_ports {led4_g}]
set_property -dict {PACKAGE_PIN L15 IOSTANDARD LVCMOS33} [get_ports {led4_b}]
set_property -dict {PACKAGE_PIN M15 IOSTANDARD LVCMOS33} [get_ports {led5_r}]
set_property -dict {PACKAGE_PIN L14 IOSTANDARD LVCMOS33} [get_ports {led5_g}]
set_property -dict {PACKAGE_PIN G14 IOSTANDARD LVCMOS33} [get_ports {led5_b}]

## Pmod JA. ja[0..3] are Pmod pins 1-4 (top row), ja[4..7] are pins 7-10.
set_property -dict {PACKAGE_PIN Y18 IOSTANDARD LVCMOS33} [get_ports {ja[0]}]
set_property -dict {PACKAGE_PIN Y19 IOSTANDARD LVCMOS33} [get_ports {ja[1]}]
set_property -dict {PACKAGE_PIN Y16 IOSTANDARD LVCMOS33} [get_ports {ja[2]}]
set_property -dict {PACKAGE_PIN Y17 IOSTANDARD LVCMOS33} [get_ports {ja[3]}]
set_property -dict {PACKAGE_PIN U18 IOSTANDARD LVCMOS33} [get_ports {ja[4]}]
set_property -dict {PACKAGE_PIN U19 IOSTANDARD LVCMOS33} [get_ports {ja[5]}]
set_property -dict {PACKAGE_PIN W18 IOSTANDARD LVCMOS33} [get_ports {ja[6]}]
set_property -dict {PACKAGE_PIN W19 IOSTANDARD LVCMOS33} [get_ports {ja[7]}]

## Pmod JB, same layout as JA
set_property -dict {PACKAGE_PIN W14 IOSTANDARD LVCMOS33} [get_ports {jb[0]}]
set_property -dict {PACKAGE_PIN Y14 IOSTANDARD LVCMOS33} [get_ports {jb[1]}]
set_property -dict {PACKAGE_PIN T11 IOSTANDARD LVCMOS33} [get_ports {jb[2]}]
set_property -dict {PACKAGE_PIN T10 IOSTANDARD LVCMOS33} [get_ports {jb[3]}]
set_property -dict {PACKAGE_PIN V16 IOSTANDARD LVCMOS33} [get_ports {jb[4]}]
set_property -dict {PACKAGE_PIN W16 IOSTANDARD LVCMOS33} [get_ports {jb[5]}]
set_property -dict {PACKAGE_PIN V12 IOSTANDARD LVCMOS33} [get_ports {jb[6]}]
set_property -dict {PACKAGE_PIN W13 IOSTANDARD LVCMOS33} [get_ports {jb[7]}]

## HDMI TX (the "HDMI OUT" source port). The TMDS pairs need IOSTANDARD
## TMDS_33 and an OBUFDS per pair: _p on the OBUFDS O pin, _n on OB.
## d[0] is TMDS channel 0 (blue + syncs), d[1] green, d[2] red.
## hdmi_tx_hpdn is the hot-plug detect input (active low); cec, scl, sda
## (DDC, the monitor's EDID) are unused by this book's designs.
set_property -dict {PACKAGE_PIN L16 IOSTANDARD TMDS_33} [get_ports {hdmi_tx_clk_p}]
set_property -dict {PACKAGE_PIN L17 IOSTANDARD TMDS_33} [get_ports {hdmi_tx_clk_n}]
set_property -dict {PACKAGE_PIN K17 IOSTANDARD TMDS_33} [get_ports {hdmi_tx_d_p[0]}]
set_property -dict {PACKAGE_PIN K18 IOSTANDARD TMDS_33} [get_ports {hdmi_tx_d_n[0]}]
set_property -dict {PACKAGE_PIN K19 IOSTANDARD TMDS_33} [get_ports {hdmi_tx_d_p[1]}]
set_property -dict {PACKAGE_PIN J19 IOSTANDARD TMDS_33} [get_ports {hdmi_tx_d_n[1]}]
set_property -dict {PACKAGE_PIN J18 IOSTANDARD TMDS_33} [get_ports {hdmi_tx_d_p[2]}]
set_property -dict {PACKAGE_PIN H18 IOSTANDARD TMDS_33} [get_ports {hdmi_tx_d_n[2]}]
set_property -dict {PACKAGE_PIN R19 IOSTANDARD LVCMOS33} [get_ports {hdmi_tx_hpdn}]
set_property -dict {PACKAGE_PIN G15 IOSTANDARD LVCMOS33} [get_ports {hdmi_tx_cec}]
set_property -dict {PACKAGE_PIN M17 IOSTANDARD LVCMOS33} [get_ports {hdmi_tx_scl}]
set_property -dict {PACKAGE_PIN M18 IOSTANDARD LVCMOS33} [get_ports {hdmi_tx_sda}]

## Audio out
set_property -dict {PACKAGE_PIN R18 IOSTANDARD LVCMOS33} [get_ports {aud_pwm}]
set_property -dict {PACKAGE_PIN T17 IOSTANDARD LVCMOS33} [get_ports {aud_sd}]

## ChipKit outer digital header IO0-IO13
set_property -dict {PACKAGE_PIN T14 IOSTANDARD LVCMOS33} [get_ports {ck_io0}]
set_property -dict {PACKAGE_PIN U12 IOSTANDARD LVCMOS33} [get_ports {ck_io1}]
set_property -dict {PACKAGE_PIN U13 IOSTANDARD LVCMOS33} [get_ports {ck_io2}]
set_property -dict {PACKAGE_PIN V13 IOSTANDARD LVCMOS33} [get_ports {ck_io3}]
set_property -dict {PACKAGE_PIN V15 IOSTANDARD LVCMOS33} [get_ports {ck_io4}]
set_property -dict {PACKAGE_PIN T15 IOSTANDARD LVCMOS33} [get_ports {ck_io5}]
set_property -dict {PACKAGE_PIN R16 IOSTANDARD LVCMOS33} [get_ports {ck_io6}]
set_property -dict {PACKAGE_PIN U17 IOSTANDARD LVCMOS33} [get_ports {ck_io7}]
set_property -dict {PACKAGE_PIN V17 IOSTANDARD LVCMOS33} [get_ports {ck_io8}]
set_property -dict {PACKAGE_PIN V18 IOSTANDARD LVCMOS33} [get_ports {ck_io9}]
set_property -dict {PACKAGE_PIN T16 IOSTANDARD LVCMOS33} [get_ports {ck_io10}]
set_property -dict {PACKAGE_PIN R17 IOSTANDARD LVCMOS33} [get_ports {ck_io11}]
set_property -dict {PACKAGE_PIN P18 IOSTANDARD LVCMOS33} [get_ports {ck_io12}]
set_property -dict {PACKAGE_PIN N17 IOSTANDARD LVCMOS33} [get_ports {ck_io13}]

## ChipKit inner digital header IO26-IO41
set_property -dict {PACKAGE_PIN U5  IOSTANDARD LVCMOS33} [get_ports {ck_io26}]
set_property -dict {PACKAGE_PIN V5  IOSTANDARD LVCMOS33} [get_ports {ck_io27}]
set_property -dict {PACKAGE_PIN V6  IOSTANDARD LVCMOS33} [get_ports {ck_io28}]
set_property -dict {PACKAGE_PIN U7  IOSTANDARD LVCMOS33} [get_ports {ck_io29}]
set_property -dict {PACKAGE_PIN V7  IOSTANDARD LVCMOS33} [get_ports {ck_io30}]
set_property -dict {PACKAGE_PIN U8  IOSTANDARD LVCMOS33} [get_ports {ck_io31}]
set_property -dict {PACKAGE_PIN V8  IOSTANDARD LVCMOS33} [get_ports {ck_io32}]
set_property -dict {PACKAGE_PIN V10 IOSTANDARD LVCMOS33} [get_ports {ck_io33}]
set_property -dict {PACKAGE_PIN W10 IOSTANDARD LVCMOS33} [get_ports {ck_io34}]
set_property -dict {PACKAGE_PIN W6  IOSTANDARD LVCMOS33} [get_ports {ck_io35}]
set_property -dict {PACKAGE_PIN Y6  IOSTANDARD LVCMOS33} [get_ports {ck_io36}]
set_property -dict {PACKAGE_PIN Y7  IOSTANDARD LVCMOS33} [get_ports {ck_io37}]
set_property -dict {PACKAGE_PIN W8  IOSTANDARD LVCMOS33} [get_ports {ck_io38}]
set_property -dict {PACKAGE_PIN Y8  IOSTANDARD LVCMOS33} [get_ports {ck_io39}]
set_property -dict {PACKAGE_PIN W9  IOSTANDARD LVCMOS33} [get_ports {ck_io40}]
set_property -dict {PACKAGE_PIN Y9  IOSTANDARD LVCMOS33} [get_ports {ck_io41}]

## ChipKit SPI and I2C (also usable as plain digital I/O)
set_property -dict {PACKAGE_PIN W15 IOSTANDARD LVCMOS33} [get_ports {ck_miso}]
set_property -dict {PACKAGE_PIN T12 IOSTANDARD LVCMOS33} [get_ports {ck_mosi}]
set_property -dict {PACKAGE_PIN H15 IOSTANDARD LVCMOS33} [get_ports {ck_sck}]
set_property -dict {PACKAGE_PIN F16 IOSTANDARD LVCMOS33} [get_ports {ck_ss}]
set_property -dict {PACKAGE_PIN P16 IOSTANDARD LVCMOS33} [get_ports {ck_scl}]
set_property -dict {PACKAGE_PIN P15 IOSTANDARD LVCMOS33} [get_ports {ck_sda}]
