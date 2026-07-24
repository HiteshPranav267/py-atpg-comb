# Load Libraries & Design
read_libs /home/cmos/capst_seq/saed90nm_typ.lib
read_hdl /home/cmos/capst_seq/s27.v

# Elaborate the Top Level Module
elaborate  

# Define Constraints (Clock is mandatory for DFT)
# Assuming the clock port is named 'CK'. Check your Verilog to confirm.
#create_clock -name CK -period 10.0 [get_ports CK]  ; # [NEW]

# DFT Setup
set_db dft_scan_style muxed
set_db dft_prefix dft_

# Define Scan Signals & Chain
# Define Shift Enable (SE)
define_shift_enable -name SE -active high -create_port SE

# Define the Scan Chain intent (1 chain, using specific ports)
define_scan_chain -name scan_1 -sdi scan_in -sdo scan_out -shift_enable SE -create_ports

# 6. Pre-Synthesis DFT Check
check_dft_rules

# 7. Synthesis (Maps DFFs to Scan-DFFs)
syn_generic
syn_map
syn_opt

# 8. Post-Synthesis DFT Check (Verifies Scan-DFFs are ready)
check_dft_rules -advanced

# 9. Connect the Scan Chain
# [FIXED] Removed '-auto_create_chains' to force using your defined 'scan_1'
connect_scan_chains -chains scan_1

# 10. Reporting & Export
report_scan_chains > /home/cmos/capst_seq/rpt_of_scan_s27_syn.rpt

# Save the Netlist (This is your Design with Scan Chains inserted)
write_hdl > /home/cmos/capst_seq/s27_syn_mod.v

# 11. Generate ATPG Setup Files
# This creates the .tcl and .loc files needed by Modus/TetraMAX
write_dft_atpg s27 -library /home/cmos/capst_seq/saed90nm.v

quit
