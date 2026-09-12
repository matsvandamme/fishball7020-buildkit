open_project fmcomms2_zc706.xpr
upgrade_ip [get_ips] -log ip_upgrade_2022_2.log
reset_run synth_1
launch_runs synth_1 -jobs 8
wait_on_run synth_1
launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
open_run impl_1
write_hw_platform -fixed -include_bit -force -file system_top.xsa
report_utilization -file utilization.rpt
report_timing_summary -file timing.rpt
exit
