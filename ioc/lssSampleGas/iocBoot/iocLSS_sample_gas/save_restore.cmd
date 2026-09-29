# save_restore.cmd: autosave for one station (spec §10). Before iocInit; the caller sets TOP (envPaths)
# and SAVEDIR (the .sav directory, relative to this iocBoot directory). Both request files are
# restored in pass 0 (parameters and PV names before record init) and pass 1 (the He: arrays).
#
# ONE STATION PER .sav NAME. Autosave names each .sav after its request file, so a second station
# (15IDE, deferred) must not reuse sampleGas_settings.req / sampleGas_helium.req directly, or both
# stations write the same .sav. For a second station, add two wrapper request files in db/, e.g.
#   sampleGas_settings_15IDE.req:  file sampleGas_settings.req P=15IDE:SampleGas:
#   sampleGas_helium_15IDE.req:    file sampleGas_helium.req   P=15IDE:SampleGas:
# restore their .sav files here as well, and pass them to create_monitor_set in st.cmd.
save_restoreSet_Debug(0)
save_restoreSet_IncompleteSetsOk(1)
save_restoreSet_DatedBackupFiles(1)
save_restoreSet_NumSeqFiles(3)
save_restoreSet_SeqPeriodInSeconds(600)
set_requestfile_path("$(TOP)/db")
set_savefile_path("$(SAVEDIR)")
set_pass0_restoreFile("sampleGas_settings.sav")
set_pass1_restoreFile("sampleGas_settings.sav")
set_pass0_restoreFile("sampleGas_helium.sav")
set_pass1_restoreFile("sampleGas_helium.sav")
