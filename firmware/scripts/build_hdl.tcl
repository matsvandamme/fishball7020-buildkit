# Creates (or recreates) and fully builds the pluto HDL project from source,
# then exports the hardware platform (with bitstream) needed for the FSBL.
# Run from hdl/projects/pluto/ (build_all.sh cd's there first).

# Use every core the machine has rather than a hardcoded 8.
set njobs 8
if {![catch {exec nproc} n]} { set njobs $n }
puts "build_hdl: using $njobs parallel jobs"

set proj_exists [file exists pluto.xpr]

if {$proj_exists} {
    open_project pluto.xpr
    upgrade_ip [get_ips] -log ip_upgrade.log
    reset_run synth_1
    launch_runs synth_1 -jobs $njobs
    wait_on_run synth_1
    launch_runs impl_1 -to_step write_bitstream -jobs $njobs
    wait_on_run impl_1
    open_run impl_1
} else {
    source system_project.tcl
    open_run impl_1
}

write_hw_platform -fixed -include_bit -force -file system_top.xsa
report_utilization -file utilization.rpt
report_timing_summary -file timing.rpt
exit
