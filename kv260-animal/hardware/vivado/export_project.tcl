# Create a directly openable project with a local HLS IP repository.
# MC_KV260_BUILD: completed original build; MC_KV260_PROJECT: new output directory.
foreach var {MC_KV260_BUILD MC_KV260_PROJECT} {
    if {![info exists ::env($var)]} {error "Set $var"}
}
set build [file normalize $::env(MC_KV260_BUILD)]
set dest [file normalize $::env(MC_KV260_PROJECT)]
if {[file exists [file join $dest mc_kv260.xpr]]} {error "Use a new project output directory"}
set ip [file join $dest ip_repo/mc_kv260]
file mkdir $ip
set source_ip [file join $build hls/solution/impl/ip]
foreach name {component.xml mc_kv260_info.xml sysgen_info.xml constraints doc drivers example hdl misc subcore xgui} {
    if {[file exists [file join $source_ip $name]]} {
        file copy [file join $source_ip $name] [file join $ip $name]
    }
}
create_project mc_kv260 $dest -part xck26-sfvc784-2LV-c
set boards [get_board_parts -quiet xilinx.com:kv260_som:part0:1.4]
if {[llength $boards]} {set_property board_part [lindex $boards 0] [current_project]}
set_property ip_repo_paths [list [file join $dest ip_repo]] [current_project]
update_ip_catalog
set bd_source [file join $build vivado/mc_kv260.srcs/sources_1/bd/system/system.bd]
set bd_dest [file join $dest mc_kv260.srcs/sources_1/bd/system/system.bd]
file mkdir [file dirname $bd_dest]
file copy $bd_source $bd_dest
add_files -norecurse $bd_dest
set bd [get_files system.bd]
open_bd_design $bd
validate_bd_design
save_bd_design
generate_target all $bd
set wrapper [make_wrapper -files $bd -top]
add_files -norecurse $wrapper
set_property top system_wrapper [current_fileset]
update_compile_order -fileset sources_1
close_project
puts "PASS: standalone Vivado project created"
