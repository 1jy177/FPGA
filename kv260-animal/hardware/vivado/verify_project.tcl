# Run on an extracted distribution, using Vivado 2022.2.2.
set root [file normalize [file join [file dirname [info script]] ../..]]
set project [file join $root hardware/project/mc_kv260.xpr]
open_project $project
update_ip_catalog
foreach repo [get_property ip_repo_paths [current_project]] {
    if {[string first "$root/" [file normalize $repo]]!=0} {error "External IP repository: $repo"}
}
set bd [get_files system.bd]
open_bd_design $bd
validate_bd_design
foreach ip [get_ips] {
    if {[get_property IS_LOCKED $ip]} {error "Locked IP: $ip"}
}
foreach f [get_files -all] {
    set name [get_property NAME $f]
    if {![file exists $name]} {error "Missing project source: $name"}
    if {[string first "$root/" [file normalize $name]]!=0} {error "External project source: $name"}
}
launch_runs synth_1 -jobs 6
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Relocated project synthesis failed"}
puts "PASS: relocated project, Block Design, local IP and synthesis"
close_project
