"""Screenshots of the sampleGas Phoebus displays, for the documentation (docs/ioc/img/).

Runs its OWN Phoebus instances, one per display: each with its own user directory
(-Dphoebus.user), the same private settings file and its own server port (4994-4997), so none of
them ever talks to, restores or saves over the user's Phoebus. Each display is opened once, at
start, and never re-sent: re-sending a display to Phoebus reloads it, which restarts its trend.
Only the window of the instance being shot is captured (PrintWindow), so other windows on the
desktop never end up in a picture. The windows go to a portrait monitor if there is one.

Windows only. Usage (PowerShell, bench Python, PYTHONIOENCODING=utf-8):
    python doc_screens.py start [--prefix SIM:SampleGas:] [--ca "127.0.0.1:5076 127.0.0.1:5066"]
    python doc_screens.py shot <simple|main|admin|deep> <out.png> [--raw]
    python doc_screens.py stop
The working files (settings, user dirs, copies of the .bob files: Phoebus cannot open a path with
spaces) go to %LOCALAPPDATA%\\sgDocPhoebus.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
SCREENS = HERE.parent / "screens"
PHOEBUS_DIR = Path(r"C:\Program Files\phoebus-win")
JAVA = PHOEBUS_DIR / "jdk" / "bin" / "java.exe"
JAR = PHOEBUS_DIR / "phoebus-4.7.4-SNAPSHOT" / "product-4.7.4-SNAPSHOT.jar"
WORK = Path(os.environ["LOCALAPPDATA"]) / "sgDocPhoebus"
STATE = WORK / "state.json"
DISPLAYS = {"simple": "sampleGas_simple.bob", "main": "sampleGas_main.bob",
            "admin": "sampleGas_admin.bob", "deep": "sampleGas_deep.bob"}
# Not 4999: the PC-trial Phoebus (cmc-epics-ioc phoebus.md) uses that. A launch whose port is
# taken hands its display to whichever instance holds it, so start() refuses if a port answers.
PORTS = {"main": 4994, "simple": 4995, "admin": 4996, "deep": 4997}
BACKGROUND = (244, 244, 244)          # the displays' background colour (gen_screens.py)

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
ctypes.windll.shcore.SetProcessDpiAwareness(2)      # physical pixels for rects and captures


def _settings(prefix, ca):
    return "\n".join([
        "# doc_screens.py: separate Phoebus instances for documentation screenshots (bench PVs)",
        f"org.phoebus.pv.ca/addr_list={ca}",
        "org.phoebus.pv.ca/auto_addr_list=false",
        "org.phoebus.pv.ca/max_array_bytes=100000",
        "org.phoebus.pv/default=ca",
        f"org.csstudio.display.builder.model/macros=<P>{prefix}</P>",
        "org.phoebus.applications.update/current_version=",
        ""])


def _phoebus(name):
    url = (WORK / "displays" / DISPLAYS[name]).as_uri()      # file:///C:/... (no spaces)
    cmd = [str(JAVA), f"-Dphoebus.user={WORK / ('user-' + name)}", "-jar", str(JAR),
           "-settings", str(WORK / "settings.ini"), "-server", str(PORTS[name]), "-resource", url]
    return subprocess.Popen(cmd, cwd=str(JAR.parent), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def _windows(pid):
    """Visible top-level windows of process pid: [(hwnd, title, (l, t, r, b))]."""
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            out.append((hwnd, buf.value, (r.left, r.top, r.right, r.bottom)))
        return True

    user32.EnumWindows(cb, 0)
    return out


def _main_window(pid, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        ws = [w for w in _windows(pid) if (w[2][2] - w[2][0]) > 400 and w[1]]
        if ws:
            return max(ws, key=lambda w: (w[2][2] - w[2][0]) * (w[2][3] - w[2][1]))[0]
        time.sleep(1)
    raise SystemExit(f"doc_screens: no Phoebus window appeared for pid {pid}")


def _work_area():
    """Work area (l, t, r, b, physical pixels) of the monitor to use: a portrait (vertical)
    monitor if there is one, since the displays are tall; else the primary monitor."""
    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                    ("dwFlags", wt.DWORD)]
    mons = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)
    def cb(hmon, _hdc, _rc, _):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        w = mi.rcWork
        mons.append(((w.left, w.top, w.right, w.bottom), bool(mi.dwFlags & 1)))
        return True

    user32.EnumDisplayMonitors(0, 0, cb, 0)
    portrait = [r for r, _ in mons if (r[3] - r[1]) > (r[2] - r[0])]
    if portrait:
        return portrait[0]
    return next(r for r, primary in mons if primary)


def _display_size(name):
    root = ET.parse(SCREENS / DISPLAYS[name]).getroot()
    return int(root.findtext("width")), int(root.findtext("height"))


def _place(hwnd, name):
    """Move the window to the chosen monitor and size it for the display (logical size) plus
    Phoebus' frame, menu, toolbar, tab header and zoom bar (~190) and scrollbar and status bar
    (~50), within the work area; raise it without taking the focus. Returns the window's scale."""
    dw, dh = _display_size(name)
    l, t, r, b = _work_area()
    x, y = l + 20, t + 20
    user32.ShowWindow(hwnd, 4)                                   # SW_SHOWNOACTIVATE
    # Phoebus resizes itself after a DPI change on the new monitor: re-apply until it holds
    for _ in range(10):
        scale = user32.GetDpiForWindow(hwnd) / 96
        w = min(round((dw + 60) * scale), r - l - 40)
        h = min(round((dh + 270) * scale), b - t - 40)
        rc = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rc))
        if abs(rc.right - rc.left - w) <= 4 and abs(rc.bottom - rc.top - h) <= 4 and \
           abs(rc.left - x) <= 4 and abs(rc.top - y) <= 4:
            break
        user32.SetWindowPos(hwnd, 0, x, y, w, h, 0x0010)          # HWND_TOP, SWP_NOACTIVATE
        time.sleep(1.5)
    user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0010 | 0x0001 | 0x0002)   # raise, no move/size
    time.sleep(1.5)
    return scale


def _capture(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = user32.GetWindowDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mdc, bmp)
    ok = user32.PrintWindow(hwnd, mdc, 2)                    # PW_RENDERFULLCONTENT
    bmi = (ctypes.c_uint32 * 10)(40, w, (-h) & 0xFFFFFFFF, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mdc, bmp, 0, h, buf, bmi, 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(hwnd, hdc)
    if not ok:
        raise SystemExit("doc_screens: PrintWindow failed")
    return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")


def _crop(img, name, scale):
    """The display area, found from the top-left: the first pixel where runs of display
    background start both rightwards and downwards, a tenth of the display long."""
    W, H = (round(v * scale) for v in _display_size(name))
    px = img.load()
    run = max(20, W // 10)
    for y in range(img.height - run):
        for x in range(0, img.width - run):
            if all(px[x + i, y] == BACKGROUND for i in range(0, run, 4)) and \
               all(px[x, y + i] == BACKGROUND for i in range(0, run, 4)):
                return img.crop((x, y, min(x + W, img.width), min(y + H, img.height)))
    return img


def start(prefix, ca):
    if STATE.exists():
        raise SystemExit(f"doc_screens: already started ({STATE}); run stop first")
    for name, port in PORTS.items():
        with socket.socket() as s:
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                raise SystemExit(f"doc_screens: something already listens on port {port} "
                                 f"({name}); a Phoebus there would receive the display")
    (WORK / "displays").mkdir(parents=True, exist_ok=True)
    for f in DISPLAYS.values():
        shutil.copy2(SCREENS / f, WORK / "displays" / f)
    (WORK / "settings.ini").write_text(_settings(prefix, ca), encoding="utf-8")
    pids = {}
    try:
        for name in PORTS:
            (WORK / ("user-" + name)).mkdir(exist_ok=True)
            p = _phoebus(name)
            pids[name] = p.pid
            STATE.write_text(json.dumps(pids))
            _place(_main_window(p.pid), name)
            print(f"doc_screens: {name}: Phoebus pid {p.pid}")
        # A slow display can make Phoebus reset its window geometry after the first placement:
        # place every window again once all are up, so they all stay on the one monitor.
        time.sleep(10)
        for name, pid in pids.items():
            _place(_main_window(pid), name)
    except BaseException:
        stop()
        raise


def shot(name, out, raw=False):
    pid = json.loads(STATE.read_text())[name]
    hwnd = _main_window(pid)
    scale = _place(hwnd, name)
    img = _capture(hwnd)
    if not raw:
        full = img.size
        img = _crop(img, name, scale)
        W, H = (round(v * scale) for v in _display_size(name))
        # A partly hidden display would put Phoebus' own frame in the picture, and its status
        # bar shows the account name: never save one.
        if img.size == full or img.width < W - 2 or img.height < H - 2:
            raise SystemExit(f"doc_screens: {name}: display not fully visible "
                             f"({img.width}x{img.height} of {W}x{H}); nothing saved")
        img = img.resize((round(img.width / scale * 1.25), round(img.height / scale * 1.25)),
                         Image.LANCZOS)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print(f"doc_screens: {name} -> {out} ({img.width}x{img.height})")


def stop():
    """Stop the instances this tool started (by the PIDs it recorded, never by image name)."""
    if not STATE.exists():
        return
    for name, pid in json.loads(STATE.read_text()).items():
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        print(f"doc_screens: stopped {name} (pid {pid})")
    STATE.unlink()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--prefix", default="SIM:SampleGas:")
    s.add_argument("--ca", default="127.0.0.1:5076 127.0.0.1:5066")
    s = sub.add_parser("shot")
    s.add_argument("display", choices=sorted(DISPLAYS))
    s.add_argument("out")
    s.add_argument("--raw", action="store_true", help="whole window, no crop or resize")
    sub.add_parser("stop")
    a = ap.parse_args(argv)
    if a.cmd == "start":
        start(a.prefix, a.ca)
    elif a.cmd == "shot":
        shot(a.display, a.out, a.raw)
    else:
        stop()


if __name__ == "__main__":
    sys.exit(main())
