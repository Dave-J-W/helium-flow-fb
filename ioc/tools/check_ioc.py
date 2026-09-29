"""Read-only check of a local sampleGas IOC: status PVs, heartbeat and the newest log lines. No writes.

Usage (PowerShell, PYTHONIOENCODING=utf-8):
    python check_ioc.py [PREFIX] [CA_ADDR_LIST]
    defaults: LSSPC:SampleGas: (the PC IOC) and 127.0.0.1; bench IOC: SIM:SampleGas: "127.0.0.1:5076"
"""
import os, sys, time
os.environ["EPICS_CA_ADDR_LIST"] = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
os.environ["EPICS_CA_AUTO_ADDR_LIST"] = "NO"
os.environ["EPICS_CA_MAX_ARRAY_BYTES"] = "100000"
import epics  # noqa: E402

P = sys.argv[1] if len(sys.argv) > 1 else "LSSPC:SampleGas:"
NAMES = ["Sts:Heartbeat", "Sts:State", "Sts:StateDesc.VAL$", "Sts:O2", "Sts:O2Valid", "Sts:InRange",
         "Sts:Flow", "Sts:SetpointRBV", "Sts:MfcRunning", "Sts:MfcStatus", "Sts:ExpectedFlow",
         "Sts:LastCmd", "Sts:WriteEnable", "Sts:WorstSevr", "Sts:Banner.VAL$", "Sts:LastAction.VAL$",
         "Sts:Progress.VAL$", "He:LeftL", "He:ForecastText.VAL$", "He:CumL", "Par:writeEnable",
         "Par:target", "Mode", "Cfg:Active:MFC", "Cfg:Active:O2", "Cfg:Conn:MFC", "Cfg:Conn:O2",
         "Cfg:Status.VAL$"]


def main():
    pvs = {n: epics.PV(P + n) for n in NAMES}
    for pv in pvs.values():
        pv.wait_for_connection(5)
    hb0 = pvs["Sts:Heartbeat"].get()
    time.sleep(3)
    for n, pv in pvs.items():
        if not pv.connected:
            print(f"{n:24s} NOT CONNECTED")
            continue
        v = pv.get(as_string=True)
        print(f"{n:24s} {v!s:40.40s} sevr {pv.severity}")
    hb1 = pvs["Sts:Heartbeat"].get()
    print(f"heartbeat advanced {hb1 - hb0 if None not in (hb0, hb1) else '?'} in ~3 s")
    log = epics.PV(P + "Log:Text")
    if log.wait_for_connection(5):
        raw = log.get()   # CHAR waveform holding UTF-8 text (as_string would decode it as latin-1)
        txt = bytes(bytearray(int(b) & 0xFF for b in raw)).split(b"\0")[0].decode("utf-8", "replace") \
            if raw is not None else ""
        print("--- Log:Text (newest first, 25 lines)")
        print("\n".join(txt.splitlines()[:25]))


if __name__ == "__main__":
    main()
