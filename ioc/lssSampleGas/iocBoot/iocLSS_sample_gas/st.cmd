# st.cmd: the BENCH start-up (spec §5.5): one station against the plant simulator (ioc/test/plant_sim.py).
# Start it with ioc/tools/run_ioc.sh, which confines Channel Access to this PC first.
# READONLY=1 in the environment starts it read-only (Plan 2 task 6); default writes possible.
< envPaths
epicsEnvSet("P", "SIM:SampleGas:")
epicsEnvSet("SAVEDIR", "autosave")

dbLoadDatabase("$(TOP)/dbd/lssSampleGas.dbd")
lssSampleGas_registerRecordDeviceDriver(pdbbase)

< save_restore.cmd
dbLoadRecords("$(TOP)/db/sampleGas.db", "P=$(P),MFC=SIM:Alicat1:,O2=SIM:O2,CYL=,STN=SIM")

iocInit

create_monitor_set("sampleGas_settings.req", 30, "P=$(P)")
create_monitor_set("sampleGas_helium.req", 300, "P=$(P)")
seq sampleGas, "P=$(P),LOGDIR=logs,READONLY=$(READONLY=0)"
