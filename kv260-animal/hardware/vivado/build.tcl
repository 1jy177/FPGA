set root [file normalize [file join [file dirname [info script]] ../..]]
if {![info exists ::env(MC_KV260_BUILD)]} {error "Set MC_KV260_BUILD on large storage"}
set build [file normalize $::env(MC_KV260_BUILD)]
file mkdir $build
create_project mc_kv260 [file join $build vivado] -part xck26-sfvc784-2LV-c -force
set boards [get_board_parts -quiet xilinx.com:kv260_som:part0:*]
if {[llength $boards]==0} {error "Install official KV260 SOM board files"}
set_property board_part [lindex [lsort -dictionary $boards] end] [current_project]
set_property ip_repo_paths [list [file join $build hls/solution/impl/ip]] [current_project]
update_ip_catalog
create_bd_design system
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:3.4 ps]
apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e -config {apply_board_preset "1"} $ps
set_property -dict [list CONFIG.PSU__USE__M_AXI_GP0 {1} CONFIG.PSU__USE__M_AXI_GP1 {0} \
 CONFIG.PSU__USE__M_AXI_GP2 {0} CONFIG.PSU__USE__S_AXI_GP0 {0} CONFIG.PSU__USE__S_AXI_GP1 {0} \
 CONFIG.PSU__USE__S_AXI_GP2 {1} CONFIG.PSU__USE__S_AXI_GP3 {1} \
 CONFIG.PSU__SAXIGP2__DATA_WIDTH {128} CONFIG.PSU__SAXIGP3__DATA_WIDTH {128} \
 CONFIG.PSU__FPGA_PL0_ENABLE {1} CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {200} \
 CONFIG.PSU__USE__IRQ0 {0} CONFIG.PSU__USE__IRQ1 {0}] $ps
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 reset]
# Reset polarity is propagated from PS pl_resetn0 (read-only IP parameter).
connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins reset/slowest_sync_clk]
connect_bd_net [get_bd_pins ps/pl_resetn0] [get_bd_pins reset/ext_reset_in]
set ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 control]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {2}] $ctrl
connect_bd_intf_net [get_bd_intf_pins ps/M_AXI_HPM0_FPD] [get_bd_intf_pins control/S00_AXI]
connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins ps/maxihpm0_fpd_aclk] [get_bd_pins control/aclk]
connect_bd_net [get_bd_pins reset/peripheral_aresetn] [get_bd_pins control/aresetn]
for {set i 0} {$i<2} {incr i} {
    set core [create_bd_cell -type ip -vlnv workbench.org:hls:mc_kv260:1.0 mc_kv260_$i]
    set mem [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 memory_$i]
    set_property -dict [list CONFIG.NUM_SI {2} CONFIG.NUM_MI {1}] $mem
    connect_bd_intf_net [get_bd_intf_pins control/M0${i}_AXI] [get_bd_intf_pins mc_kv260_$i/s_axi_control]
    connect_bd_intf_net [get_bd_intf_pins mc_kv260_$i/m_axi_input_mem] [get_bd_intf_pins memory_$i/S00_AXI]
    connect_bd_intf_net [get_bd_intf_pins mc_kv260_$i/m_axi_output_mem] [get_bd_intf_pins memory_$i/S01_AXI]
    connect_bd_intf_net [get_bd_intf_pins memory_$i/M00_AXI] [get_bd_intf_pins ps/S_AXI_HP${i}_FPD]
    connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins mc_kv260_$i/ap_clk] [get_bd_pins memory_$i/aclk] [get_bd_pins ps/saxihp${i}_fpd_aclk]
    connect_bd_net [get_bd_pins reset/peripheral_aresetn] [get_bd_pins mc_kv260_$i/ap_rst_n] [get_bd_pins memory_$i/aresetn]
}
assign_bd_address
validate_bd_design
if {[get_property CONFIG.C_EXT_RESET_HIGH $rst] != 0} {error "PS reset polarity must be active-low"}
save_bd_design
set bd [get_files system.bd]
generate_target all $bd
make_wrapper -files $bd -top
add_files -norecurse [file join $build vivado/mc_kv260.gen/sources_1/bd/system/hdl/system_wrapper.v]
set_property top system_wrapper [current_fileset]
launch_runs synth_1 -jobs 2
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Synthesis failed"}
open_run synth_1
report_utilization -file [file join $build utilization_synth.rpt]
launch_runs impl_1 -to_step write_bitstream -jobs 2
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "Implementation failed"}
open_run impl_1
report_utilization -file [file join $build utilization_routed.rpt]
report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose -file [file join $build timing_routed.rpt]
report_drc -file [file join $build drc.rpt]
report_route_status -file [file join $build route_status.rpt]
report_methodology -file [file join $build methodology.rpt]
report_cdc -details -file [file join $build cdc.rpt]
report_clock_interaction -file [file join $build clock_interaction.rpt]
report_exceptions -summary -file [file join $build exceptions.rpt]
report_clocks -file [file join $build clocks.rpt]
report_property [get_runs impl_1] -file [file join $build implementation_properties.rpt]
foreach delay {max min} {
    set paths [get_timing_paths -delay_type $delay -max_paths 1]
    if {[llength $paths]==0 || [get_property SLACK $paths]<=0} {error "$delay timing failed"}
}
if {[llength [get_drc_violations -quiet -filter {SEVERITY == Error}]]} {error "DRC errors remain"}
file mkdir [file join $root overlays]
file copy -force [file join $build vivado/mc_kv260.runs/impl_1/system_wrapper.bit] [file join $root overlays/mc_kv260.bit]
file copy -force [file join $build vivado/mc_kv260.gen/sources_1/bd/system/hw_handoff/system.hwh] [file join $root overlays/mc_kv260.hwh]
exit
