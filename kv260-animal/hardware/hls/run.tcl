set root [file normalize [file join [file dirname [info script]] ../..]]
if {![info exists ::env(MC_KV260_BUILD)]} {error "Set MC_KV260_BUILD on large storage"}
set build [file normalize $::env(MC_KV260_BUILD)]
set action all
foreach arg $argv {if {$arg in {csim synth cosim all verify}} {set action $arg}}
set flags "-std=c++11 -I$root/shared/common -I$root/shared/kernel/src -I$root/hardware/hls"
file mkdir $build
cd $build
open_project hls
set_top mc_kv260
add_files [file join $root hardware/hls/mc_kv260.cpp] -cflags $flags
add_files -tb [file join $root tests/hls_test.cpp] -cflags $flags
add_files -tb [file join $root shared/common/software.cpp] -cflags $flags
if {$action eq "verify"} {open_solution solution} else {open_solution -reset solution -flow_target vivado}
set_part xck26-sfvc784-2LV-c
create_clock -period 5 -name default
config_interface -m_axi_addr64=true
if {$action in {csim all verify}} {csim_design}
if {$action in {synth cosim all}} {csynth_design}
if {$action in {cosim all verify}} {cosim_design -rtl verilog}
if {$action in {synth cosim all}} {export_design -format ip_catalog -vendor workbench.org -library hls -version 1.0}
exit
