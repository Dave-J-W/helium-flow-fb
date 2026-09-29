#!/usr/bin/env python3
"""gen_screens.py: generate the four Phoebus displays of the 15LSS_sample_gas IOC, the alarm-server
configurations and the archiver PV list, from the PV table in sg_pvs.py.

Usage:
    python gen_screens.py            regenerate the committed files under ioc/screens/
    python gen_screens.py --check    regenerate into memory and exit 1 if any committed file differs

Outputs (spec docs/ioc/15LSS_sample_gas_IOC_spec.md section 13; layout authority: the approved
mockups docs/ioc/mockups/sampleGas_screens_mockup.html, ruling P5-R3):
    sampleGas_simple.bob        13.0 simple panel (everyday display)
    sampleGas_main.bob          13.1 full panel
    sampleGas_admin.bob         13.2 Admin (write switch with confirmation both ways)
    sampleGas_deep.bob          13.3 Deep admin (PV names, modes, nine groups)
    sampleGas_alarms.xml        13.4 alarm-server config, production (15IDC)
    sampleGas_alarms_bench.xml  13.4 alarm-server config, bench (two simulated stations)
    archive_pvs.txt             13.4 archiver PV list, production

Target: Phoebus 4.7 Display Builder (.bob, <display version="2.0.0">). Property names were checked
against the installed 4.7.4 jars (e.g. the action button's confirmation is `show_confirm_dialog`
+ `confirm_message`). Only macro P is used; the Alicat and the O2 analyzer are read only through
the Sts: mirrors (13.1). Long texts (lsi records, > 40 characters) are read as `<pv>.VAL$` with
format STRING, because a plain CA DBR_STRING stops at 40 characters.

Deterministic: the same sg_pvs.py always produces byte-identical output.
"""
import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sg_pvs as T  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
SCREENS = os.path.join(REPO_ROOT, "ioc", "screens")

P = "$(P)"

# ---------------------------------------------------------------------------
# Palette (from the mockup) and fonts
# ---------------------------------------------------------------------------
PANEL_BG = "#f4f4f4"
TEXT = "#222222"
LBL = "#555555"
UNIT = "#888888"
GREY_DEFAULT = "#999999"
RO_BG = "#111111"
RO_VAL = "#9fe870"
RO_LBL = "#bbbbbb"
RO_SUB = "#999999"
WHITE = "#ffffff"
BOX_LINE = "#cccccc"
GROUP_LINE = "#aaaaaa"

BANNER_UNKNOWN = ("#b4b2a9", "#000000")  # file colour: no value yet / IOC down
BANNER_OK = ("#c0dd97", "#173404")
BANNER_MINOR = ("#fac775", "#412402")
BANNER_MAJOR = ("#e24b4a", "#ffffff")
SHADOW_BADGE = ("#fac775", "#412402")
PURGE_BTN = ("#b5d4f4", "#042c53")
ZERO_BTN = ("#f7c1c1", "#501313")
LED_ON = "#639922"
LED_OFF_GREY = "#b4b2a9"
LED_OFF_RED = "#e24b4a"
LED_OFF_AMBER = "#e08a1e"
SWITCH_STRIP = ("#faeeda", "#633806", "#ba7517")  # background, text, border
LIVE_ON = "#c0dd97"

# The simulator's STATES colours, index = Sts:State value.
STATE_NAMES = ["IDLE", "PRECHECK", "PURGE", "HANDOFF", "REGULATE", "OPEN_LOOP", "FLOW_ZERO",
               "OPEN_STOP"]
STATE_COLOURS = ["#8a8a8a", "#8e7cc3", "#2f6fbf", "#4f9aa8", "#4c9a2a", "#e08a1e", "#555555",
                 "#c00000"]
STATE_IOC_DOWN = "#000000"  # the simulator's IOC_DOWN colour

SANS = "Liberation Sans"
MONO = "Liberation Mono"

# Format option indices (org.phoebus.ui.vtype.FormatOption, 4.7.4)
FMT_DEFAULT, FMT_DECIMAL, FMT_STRING = 0, 1, 6
# Group styles (GroupWidget.Style): 0 GROUP (titled box), 3 NONE
GROUP_BOX = 0

RESUME_TOOLTIP = "resume feedback from the current flow, without a purge"

# §13.2 confirmation texts. The live text is built by a rule (out_exp) from Cfg:Active:MFC (pv0)
# and Sts:SetpointRBV (pv1); the static text is the fallback shown before the PVs have values.
CONFIRM_LIVE_EXPR = (
    '"Enable writes? The Alicat (" + pvStr0 + ") stays at its current flow ("'
    ' + "%.2f" % pv1 + " SLPM). The controller goes to IDLE and changes nothing until you press'
    ' Purge, Flow Zero or Resume Flow."'
)
CONFIRM_LIVE_STATIC = (
    "Enable writes? The Alicat stays at its current flow. The controller goes to IDLE and "
    "changes nothing until you press Purge, Flow Zero or Resume Flow."
)
CONFIRM_SHADOW_EXPR = (
    '"Switch to shadow mode? The controller keeps running but stops writing; the Alicat stays'
    ' at its current flow (" + "%.2f" % pv1 + " SLPM) until someone changes it."'
)
CONFIRM_SHADOW_STATIC = (
    "Switch to shadow mode? The controller keeps running but stops writing; the Alicat stays "
    "at its current flow until someone changes it."
)
CONFIRM_APPLY = (
    "Apply the edited PV names? If the Alicat prefix (Cfg:MFC) changed, the helium totalizer "
    "is re-baselined (press New He cylinder if the cylinder differs); in the PC trial, writes "
    "are also disabled until you re-enable them on the Admin screen."
)
CONFIRM_NEW_CYL = (
    "Record a new, full helium cylinder? The litres left and the run-out forecast restart "
    "from a full cylinder."
)
CONFIRM_RELEASE = (
    "Release control? The controller goes to IDLE and stops regulating; the Alicat stays at "
    "its current flow."
)

# Admin parameter labels and order: the approved mockup (P5-R3). Must cover the A-level keys.
ADMIN_LABELS = [
    ("tol", "In-range tolerance"),
    ("delta", "Handoff margin Δ"),
    ("purgeFlow", "Purge flow"),
    ("purgeTimeoutMargin", "Timeout margin"),
    ("fbDelay", "Feedback delay"),
    ("settleTimeout", "Settle timeout"),
    ("flowMinorX", "Flow-high MINOR"),
    ("flowMajorX", "Flow-high MAJOR"),
    ("pinnedTime", "Pinned → MAJOR"),
    ("o2AbnormalOffset", "O2 abnormal by"),
    ("cylWarnH", "Cylinder warn"),
    ("cylAlarmH", "Cylinder alarm"),
    ("rampMaxPurge", "Ramp max (purge)"),       # user decision 2026-09-29: from Deep admin
    ("rampMinHandoff", "Ramp min (handoff)"),
]

PARAM = {p["key"]: p for p in T.PARAMS}

_a_keys = {p["key"] for p in T.PARAMS if p["level"] == "A" and p["key"] != "writeEnable"}
assert {k for k, _ in ADMIN_LABELS} == _a_keys, "ADMIN_LABELS out of step with sg_pvs A level"

# The "key Diag: values" of the Admin screen (§13.2), grouped as in §7.6.
ADMIN_DIAG = [
    ("Diag:LidArmed", "lid armed"), ("Diag:AboveCount", "above count"),
    ("Diag:MaxRate", "max rate"), ("Diag:HoldSec", "hold s"),
    ("Diag:HoldAttempts", "hold attempts"), ("Diag:Settling", "settling"),
    ("Diag:SettleDir", "settle dir"), ("Diag:TowardRate", "toward rate"),
    ("Diag:O2Slope", "O2 slope"), ("Diag:StallSec", "stall s"),
    ("Diag:StallLatched", "stall latched"), ("Diag:FlowSteady", "flow steady"),
    ("Diag:InFine", "fine band"), ("Diag:PinnedSec", "pinned s"),
    ("Diag:PurgeElapsed", "purge elapsed"), ("Diag:PurgeTimeout", "purge timeout"),
    ("Diag:O2Start", "O2 at start"), ("Diag:LidOnsetT", "lid onset t"),
    ("Diag:LidRatio", "lid ratio"), ("Diag:LidCurv", "lid curvature"),
    ("Diag:LidResult", "lid result"), ("Diag:EstO2", "est. O2"),
    ("Diag:Blind", "blind"), ("Diag:HandoffPhase", "handoff phase"),
]

EPID_FIELDS = ["FBON", "VAL", "CVAL", "OVAL", "P", "I", "KP", "KI", "DRVL", "DRVH", "ODEL"]
FORECAST_WINDOWS = [("3d", "3 d"), ("2d", "2 d"), ("1d", "1 d"), ("12h", "12 h"), ("6h", "6 h")]

# Deep admin: layout of the nine groups (mockup): two rows of three columns; a column may stack
# two groups. Reading order equals sg_pvs.DEEP_GROUPS.
DEEP_LAYOUT = [
    [["Enclosure and purge"], ["Lid check (during purge)", "Lid detector (lid lifted)"], ["PID"]],
    [["Settling"], ["Alarms", "O2 reading checks"], ["MFC hold monitor", "Helium"]],
]
assert [g for row in DEEP_LAYOUT for col in row for g in col] == T.DEEP_GROUPS

LINKED_NAMES = [("MFC", "Alicat prefix"), ("O2", "O2 reading"), ("CYL", "Cylinder P"),
                ("STN", "Station label")]


# ---------------------------------------------------------------------------
# XML building blocks
# ---------------------------------------------------------------------------

def _rgb(hexcol):
    h = hexcol.lstrip("#")
    return str(int(h[0:2], 16)), str(int(h[2:4], 16)), str(int(h[4:6], 16))


def _color(parent, tag, hexcol):
    el = ET.SubElement(parent, tag)
    r, g, b = _rgb(hexcol)
    ET.SubElement(el, "color", red=r, green=g, blue=b)
    return el


def _font(parent, size, style="REGULAR", family=SANS):
    el = ET.SubElement(parent, "font")
    ET.SubElement(el, "font", family=family, style=style, size=f"{float(size):.1f}")
    return el


def _text(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


# Widget versions of Phoebus 4.7.4 (getVersion() in app-display-model: Widget base 2.0.0,
# TextEntryWidget 3.0.0, ActionButtonWidget 3.0.0, GroupWidget 3.0.0, StripchartWidget 2.1.0).
# An older version in the file triggers the legacy configurators: a stripchart < 2.1 replaces
# `start` by `time_range` (default "1 minute"); a group < 3 copies foreground_color into
# line_color.
WIDGET_VERSIONS = {
    "label": "2.0.0", "textupdate": "2.0.0", "led": "2.0.0", "combo": "2.0.0",
    "rectangle": "2.0.0", "textentry": "3.0.0", "action_button": "3.0.0", "group": "3.0.0",
    "stripchart": "2.1.0",
}


class Container:
    """A display or a group: something widgets are added to."""

    def __init__(self, el, names):
        self.el = el
        self.names = names

    def widget(self, type_, name, x, y, w, h):
        assert name not in self.names, f"duplicate widget name {name!r}"
        self.names.add(name)
        el = ET.SubElement(self.el, "widget", type=type_, version=WIDGET_VERSIONS[type_])
        ET.SubElement(el, "name").text = name
        return Widget(el, x, y, w, h)


class Widget:
    def __init__(self, el, x, y, w, h):
        self.el = el
        self._geom = (x, y, w, h)
        self._geom_done = False

    def geom(self):
        if not self._geom_done:
            for tag, v in zip(("x", "y", "width", "height"), self._geom):
                self.set(tag, int(v))
            self._geom_done = True
        return self

    def set(self, tag, value):
        ET.SubElement(self.el, tag).text = _text(value)
        return self

    def color(self, tag, hexcol):
        _color(self.el, tag, hexcol)
        return self

    def font(self, size, style="REGULAR", family=SANS):
        _font(self.el, size, style, family)
        return self

    def rule(self, name, prop_id, pvs, exps, out_exp=False):
        rules = self.el.find("rules")
        if rules is None:
            rules = ET.SubElement(self.el, "rules")
        r = ET.SubElement(rules, "rule", name=name, prop_id=prop_id,
                          out_exp="true" if out_exp else "false")
        for bool_exp, val in exps:
            e = ET.SubElement(r, "exp", bool_exp=bool_exp)
            if out_exp:
                ET.SubElement(e, "expression").text = val
            elif prop_id.endswith("color"):
                _color(e, "value", val)
            else:
                ET.SubElement(e, "value").text = _text(val)
        for pv in pvs:
            ET.SubElement(r, "pv_name").text = pv
        return self


def new_display(name, width, height):
    root = ET.Element("display", version="2.0.0")
    ET.SubElement(root, "name").text = name
    ET.SubElement(root, "width").text = str(width)
    ET.SubElement(root, "height").text = str(height)
    _color(root, "background_color", PANEL_BG)
    return root, Container(root, set())


# --- widget factories (geometry first, then appearance) ---------------------

def label(c, name, text, x, y, w, h, size=12, style="REGULAR", fg=LBL, bg=None, halign=0,
          valign=1, family=SANS, wrap=False, tooltip=None, visible=None):
    wd = c.widget("label", name, x, y, w, h)
    wd.set("text", text).geom().font(size, style, family).color("foreground_color", fg)
    if bg is not None:
        wd.color("background_color", bg).set("transparent", False)
    wd.set("horizontal_alignment", halign).set("vertical_alignment", valign)
    if wrap:
        wd.set("wrap_words", True)
    if tooltip:
        wd.set("tooltip", tooltip)
    if visible is not None:
        wd.set("visible", visible)
    return wd


def textupdate(c, name, pv, x, y, w, h, size=12, style="REGULAR", fg=TEXT, bg=None, halign=0,
               valign=1, family=SANS, precision=None, fmt=None, units=False, border=True,
               wrap=False):
    wd = c.widget("textupdate", name, x, y, w, h)
    wd.set("pv_name", pv).geom().font(size, style, family).color("foreground_color", fg)
    if bg is None:
        wd.set("transparent", True)
    else:
        wd.color("background_color", bg).set("transparent", False)
    if fmt is not None:
        wd.set("format", fmt)
    if precision is not None:
        wd.set("precision", precision)
    wd.set("show_units", units)
    wd.set("horizontal_alignment", halign).set("vertical_alignment", valign)
    if wrap:
        wd.set("wrap_words", True)
    if not border:
        wd.set("border_alarm_sensitive", False)
    return wd


def longtext(c, name, pv, x, y, w, h, size=11, family=MONO, fg=TEXT, bg=WHITE, valign=0,
             wrap=True, suffix=".VAL$", border=False):
    """A text update for an lsi (read as <pv>.VAL$) or a CHAR waveform (suffix='')."""
    return textupdate(c, name, pv + suffix, x, y, w, h, size=size, family=family, fg=fg, bg=bg,
                      valign=valign, fmt=FMT_STRING, wrap=wrap, border=border)


def textentry(c, name, pv, x, y, w, h, tooltip=None, size=11, fmt=None, precision=None):
    wd = c.widget("textentry", name, x, y, w, h)
    wd.set("pv_name", pv).geom().font(size)
    if fmt is not None:
        wd.set("format", fmt)
    if precision is not None:
        wd.set("precision", precision)
    wd.set("show_units", False)
    if tooltip:
        wd.set("tooltip", tooltip)
    return wd


def led(c, name, pv, x, y, size=14, on=LED_ON, off=LED_OFF_GREY):
    wd = c.widget("led", name, x, y, size, size)
    wd.set("pv_name", pv).geom().color("off_color", off).color("on_color", on)
    wd.set("border_alarm_sensitive", False)
    return wd


def combo(c, name, pv, x, y, w, h, size=12):
    wd = c.widget("combo", name, x, y, w, h)
    wd.set("pv_name", pv).geom().font(size).set("items_from_pv", True)
    return wd


def rect(c, name, x, y, w, h, bg, line=None, line_width=0, corner=3):
    wd = c.widget("rectangle", name, x, y, w, h)
    wd.geom().set("line_width", line_width)
    wd.color("line_color", line or bg).color("background_color", bg)
    wd.set("corner_width", corner).set("corner_height", corner)
    return wd


def _button(c, name, text, x, y, w, h, colours, bold, size, tooltip, enabled):
    wd = c.widget("action_button", name, x, y, w, h)
    return wd, (text, colours, bold, size, tooltip, enabled)


def _finish_button(wd, opts):
    text, colours, bold, size, tooltip, enabled = opts
    wd.set("text", text).geom().font(size, "BOLD" if bold else "REGULAR")
    bg, fg = colours if colours else (WHITE, TEXT)
    wd.color("background_color", bg).color("foreground_color", fg)
    if tooltip:
        wd.set("tooltip", tooltip)
    if not enabled:
        wd.set("enabled", False)


def button_write(c, name, text, pv, value, x, y, w, h, colours=None, bold=False, size=12,
                 tooltip=None, confirm=None, enabled=True):
    wd, opts = _button(c, name, text, x, y, w, h, colours, bold, size, tooltip, enabled)
    acts = ET.SubElement(wd.el, "actions")
    a = ET.SubElement(acts, "action", type="write_pv")
    ET.SubElement(a, "pv_name").text = pv
    ET.SubElement(a, "value").text = str(value)
    ET.SubElement(a, "description").text = text
    _finish_button(wd, opts)
    if confirm is not None:
        wd.set("show_confirm_dialog", True).set("confirm_message", confirm)
    return wd


def button_open(c, name, text, bob, x, y, w, h, size=12):
    wd, opts = _button(c, name, text, x, y, w, h, None, False, size, None, True)
    acts = ET.SubElement(wd.el, "actions")
    a = ET.SubElement(acts, "action", type="open_display")
    ET.SubElement(a, "file").text = bob
    m = ET.SubElement(a, "macros")
    ET.SubElement(m, "P").text = P
    ET.SubElement(a, "target").text = "tab"
    ET.SubElement(a, "description").text = text
    _finish_button(wd, opts)
    return wd


def group(c, name, x, y, w, h, size=12):
    wd = c.widget("group", name, x, y, w, h)
    wd.geom().set("style", GROUP_BOX).font(size, "BOLD")
    wd.color("foreground_color", TEXT).color("background_color", WHITE)
    wd.color("line_color", GROUP_LINE).set("transparent", False)
    return Container(wd.el, c.names)


# --- composite pieces shared by several screens -------------------------------

def title(c, suffix=None, show_prefix=False):
    label(c, "title", "Sample gas", 10, 8, 92, 24, size=15, style="BOLD", fg=TEXT)
    textupdate(c, "title_station", P + "Cfg:Active:STN", 102, 8, 120, 24, size=15, style="BOLD",
               border=False)
    x = 226
    if suffix:
        label(c, "title_screen", suffix, x, 8, 160, 24, size=15, style="BOLD", fg=TEXT)
        x += 164
    if show_prefix:
        label(c, "title_prefix", P, x, 12, 200, 18, size=11, family=MONO)


def shadow_badge(c, text, x, y, w):
    bg, fg = SHADOW_BADGE
    wd = label(c, "shadow_badge", text, x, y, w, 22, size=11, style="BOLD", fg=fg, bg=bg,
               halign=1, tooltip="Par:writeEnable = 0: the controller computes but never "
               "writes the Alicat (spec 8.20). Switch on the Admin screen.")
    # Shown unless writes are known to be enabled: a disconnected mirror still shows the badge.
    wd.rule("hide_when_live", "visible", [P + "Sts:WriteEnable"], [("pv0 == 1", False)])
    return wd


def banner(c, x, y, w, h):
    # File colours are neutral: green only from an explicit Sts:WorstSevr == 0, so a dead IOC
    # (no value, rule not applied) never shows the OK green. Alarm-sensitive border on, so a
    # disconnect shows the Phoebus disconnect border.
    bg, fg = BANNER_UNKNOWN
    wd = longtext(c, "banner", P + "Sts:Banner", x, y, w, h, size=13, family=SANS, fg=fg, bg=bg,
                  valign=1, border=True)
    wd.rule("banner_bg", "background_color", [P + "Sts:WorstSevr"],
            [("pv0 == 2", BANNER_MAJOR[0]), ("pv0 == 1", BANNER_MINOR[0]),
             ("pv0 == 0", BANNER_OK[0])])
    wd.rule("banner_fg", "foreground_color", [P + "Sts:WorstSevr"],
            [("pv0 == 2", BANNER_MAJOR[1]), ("pv0 == 1", BANNER_MINOR[1]),
             ("pv0 == 0", BANNER_OK[1])])
    # A stalled controller leaves Sts:Banner as it was (possibly green): cover it while
    # Sts:TickAge is past its HIHI (spec 13.4). Hidden in the file, so a dead IOC shows the
    # banner's own disconnect state instead.
    bg, fg = BANNER_MAJOR
    st = label(c, "banner_stalled", "MAJOR: controller not ticking (IOC up, program stalled): "
               "call the beamline staff", x, y, w, h, size=13, style="BOLD", fg=fg, bg=bg,
               wrap=True, visible=False)
    st.rule("show_when_stalled", "visible", [P + "Sts:TickAge"], [("pv0 > 10", True)])
    return wd


def state_label(c, x, y, w, h):
    # Fallback (no value) is the simulator's IOC_DOWN colour, white text; border on.
    wd = textupdate(c, "state", P + "Sts:State", x, y, w, h, size=12, style="BOLD", family=MONO,
                    fg=WHITE, bg=STATE_IOC_DOWN, halign=1, border=True)
    wd.rule("state_colour", "background_color", [P + "Sts:State"],
            [(f"pv0 == {i}", col) for i, col in enumerate(STATE_COLOURS)])
    return wd


def command_buttons(c, x, y, w, h, gap):
    """Purge, Flow Zero, Resume Flow; returns the x after the third button."""
    button_write(c, "btn_purge", "Purge", P + "Cmd:Purge", 1, x, y, w, h, colours=PURGE_BTN,
                 bold=True, size=13)
    button_write(c, "btn_flow_zero", "Flow Zero", P + "Cmd:FlowZero", 1, x + (w + gap), y, w, h,
                 colours=ZERO_BTN, bold=True, size=13)
    wd = button_write(c, "btn_resume", "Resume Flow", P + "Cmd:ResumeFlow", 1,
                      x + 2 * (w + gap), y, w, h, size=13, tooltip=RESUME_TOOLTIP, enabled=False)
    # §13.1: enabled in OPEN_LOOP with Sts:O2Valid, or in IDLE with Sts:O2Valid and O2 below
    # Par:lidLevel (the IOC re-checks; §8.5).
    wd.rule("resume_enable", "enabled",
            [P + "Sts:State", P + "Sts:O2Valid", P + "Sts:O2", P + "Par:lidLevel"],
            [("(pv0 == 5 && pv1 == 1) || (pv0 == 0 && pv1 == 1 && pv2 < pv3)", True)])
    return x + 3 * (w + gap)


def param_tooltip(p):
    def num(v):
        return f"{v:g}"
    return (f"{p['key']}: {p['desc']}. Default {num(p['default'])}, limits "
            f"{num(p['min'])} to {num(p['max'])} {p['unit']}.")


def param_row(c, p, x, y, label_w, entry_w, unit_w, text=None, h=22):
    label(c, f"lbl_{p['key']}", text or p["label"], x, y, label_w, h, size=11, fg=LBL,
          tooltip=param_tooltip(p))
    textentry(c, f"par_{p['key']}", P + f"Par:{p['key']}", x + label_w + 2, y, entry_w, h,
              tooltip=param_tooltip(p))
    label(c, f"unit_{p['key']}", p["unit"], x + label_w + entry_w + 6, y, unit_w, h, size=11,
          fg=UNIT)


def readout_box(c, name, x, y, w, h):
    rect(c, name, x, y, w, h, RO_BG)


def serialize(root, header_comment):
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f"<!-- {header_comment} -->\n" + body + "\n")


GEN_NOTE = ("GENERATED by ioc/tools/gen_screens.py from ioc/tools/sg_pvs.py: do not edit; "
            "regenerate. Macro: P (controller prefix).")


# ---------------------------------------------------------------------------
# 13.0 Simple panel
# ---------------------------------------------------------------------------

def build_simple():
    W, H = 440, 366
    root, c = new_display("Sample gas", W, H)
    title(c)
    shadow_badge(c, "SHADOW MODE", 318, 9, 112)
    banner(c, 10, 38, 420, 32)

    # O2, large, with in-range LED and target
    readout_box(c, "o2_box", 10, 78, 420, 94)
    label(c, "o2_lbl", "Oxygen", 18, 82, 120, 16, size=11, fg=RO_LBL)
    textupdate(c, "o2", P + "Sts:O2", 18, 98, 170, 50, size=36, fg=RO_VAL, family=MONO,
               precision=2, fmt=FMT_DECIMAL)
    label(c, "o2_unit", "%", 190, 116, 30, 26, size=15, fg=RO_VAL, family=MONO)
    led(c, "in_range_led", P + "Sts:InRange", 296, 112, off=LED_OFF_AMBER)
    textupdate(c, "in_range", P + "Sts:InRange", 316, 106, 108, 26, size=12, fg=BANNER_OK[0],
               border=False)
    label(c, "target_lbl", "target", 18, 148, 42, 18, size=11, fg=RO_SUB)
    textupdate(c, "target", P + "Par:target", 60, 148, 56, 18, size=11, fg=RO_SUB, precision=2,
               fmt=FMT_DECIMAL, border=False)
    label(c, "target_unit", "%", 116, 148, 20, 18, size=11, fg=RO_SUB)

    # Helium flow and helium left
    readout_box(c, "flow_box", 10, 180, 207, 64)
    label(c, "flow_lbl", "Helium flow", 18, 184, 150, 16, size=11, fg=RO_LBL)
    textupdate(c, "flow", P + "Sts:Flow", 18, 200, 100, 30, size=20, fg=RO_VAL, family=MONO,
               precision=2, fmt=FMT_DECIMAL)
    label(c, "flow_unit", "SLPM", 120, 208, 60, 18, size=11, fg=RO_VAL)
    readout_box(c, "left_box", 223, 180, 207, 64)
    label(c, "left_lbl", "Helium left", 231, 184, 150, 16, size=11, fg=RO_LBL)
    days = textupdate(c, "left_days", "='" + P + "He:EmptyMedianH'/24", 231, 200, 80, 26,
                      size=20, fg=RO_VAL, family=MONO, precision=1, fmt=FMT_DECIMAL)
    days_unit = label(c, "left_days_unit", "days", 313, 206, 60, 18, size=11, fg=RO_VAL)
    # No forecast for the first 6 h of a cylinder (He:EmptyMedianH NaN): say so instead of
    # "NaN days". A disconnected IOC keeps the file values (number shown, with its border).
    median = [P + "He:EmptyMedianH"]
    days.rule("hide_without_forecast", "visible", median, [("pv0 != pv0", False)])
    days_unit.rule("hide_without_forecast", "visible", median, [("pv0 != pv0", False)])
    collecting = label(c, "left_collecting", "collecting data", 231, 204, 190, 20, size=13,
                       fg=RO_VAL, visible=False,
                       tooltip="The run-out forecast needs 6 h of use after a new cylinder")
    collecting.rule("show_without_forecast", "visible", median, [("pv0 != pv0", True)])
    textupdate(c, "left_litres", P + "He:LeftL", 231, 226, 60, 16, size=11, fg=RO_SUB,
               precision=0, fmt=FMT_DECIMAL, border=False)
    label(c, "left_litres_unit", "L", 293, 226, 20, 16, size=11, fg=RO_SUB)

    # State
    state_label(c, 10, 252, 116, 24)
    textupdate(c, "state_desc", P + "Sts:StateDesc.VAL$", 134, 252, 296, 24, size=12, fg=LBL,
               fmt=FMT_STRING, border=False)

    # Controls
    command_buttons(c, 10, 286, 134, 36, 9)
    label(c, "mode_lbl", "Mode", 10, 332, 40, 24, size=12, fg=LBL)
    textupdate(c, "mode", P + "Mode", 50, 332, 220, 24, size=12, fg=LBL, border=False)
    button_open(c, "btn_full", "Full panel…", "sampleGas_main.bob", 320, 331, 110, 26)
    return serialize(root, GEN_NOTE + " Spec 13.0 simple panel.")


# ---------------------------------------------------------------------------
# 13.1 Full panel
# ---------------------------------------------------------------------------

def build_main():
    W, H = 800, 658
    root, c = new_display("Sample gas: full panel", W, H)
    title(c, show_prefix=True)
    shadow_badge(c, "SHADOW MODE: no writes", 596, 9, 194)
    banner(c, 10, 38, 780, 32)

    # Four big readouts
    bw, gap, y0, bh = 191, 5, 78, 114
    xs = [10 + i * (bw + gap) for i in range(4)]

    def box(i, name, title_text, pv, precision):
        x = xs[i]
        readout_box(c, f"{name}_box", x, y0, bw, bh)
        label(c, f"{name}_lbl", title_text, x + 8, y0 + 4, bw - 12, 16, size=11, fg=RO_LBL)
        textupdate(c, name, pv, x + 8, y0 + 20, bw - 16, 32, size=22, fg=RO_VAL, family=MONO,
                   precision=precision, fmt=FMT_DECIMAL if precision is not None else None)
        return x

    x = box(0, "o2", "Oxygen (Sts:O2)", P + "Sts:O2", None)
    sy = y0 + 56
    label(c, "o2_sub", "% · target", x + 8, sy, 66, 18, size=11, fg=RO_SUB)
    textupdate(c, "o2_target", P + "Par:target", x + 74, sy, 44, 18, size=11, fg=RO_SUB,
               precision=2, fmt=FMT_DECIMAL, border=False)
    label(c, "o2_pm", "±", x + 118, sy, 12, 18, size=11, fg=RO_SUB)
    textupdate(c, "o2_tol", P + "Par:tol", x + 130, sy, 50, 18, size=11, fg=RO_SUB, precision=3,
               fmt=FMT_DECIMAL, border=False)

    x = box(1, "flow", "Helium flow (Sts:Flow)", P + "Sts:Flow", 2)
    label(c, "flow_sub", "SLPM · expected", x + 8, sy, 100, 18, size=11, fg=RO_SUB)
    textupdate(c, "flow_expected", P + "Sts:ExpectedFlow", x + 108, sy, 60, 18, size=11,
               fg=RO_SUB, precision=2, fmt=FMT_DECIMAL, border=False)
    label(c, "flow_mfc", "MFC", x + 8, sy + 18, 30, 18, size=11, fg=RO_SUB)
    textupdate(c, "flow_running", P + "Sts:MfcRunning", x + 38, sy + 18, 130, 18, size=11,
               fg=RO_SUB, border=False)

    x = box(2, "setpoint", "Flow setpoint (Sts:SetpointRBV)", P + "Sts:SetpointRBV", 2)
    label(c, "setpoint_sub", "SLPM · last command", x + 8, sy, 124, 18, size=11, fg=RO_SUB)
    textupdate(c, "setpoint_lastcmd", P + "Sts:LastCmd", x + 132, sy, 52, 18, size=11,
               fg=RO_SUB, precision=3, fmt=FMT_DECIMAL, border=False)

    x = box(3, "cyl", "Helium cylinder (He:LeftL)", P + "He:LeftL", 0)
    label(c, "cyl_unit", "L", x + 8, sy, 12, 18, size=11, fg=RO_SUB)
    longtext(c, "cyl_forecast", P + "He:ForecastText", x + 20, sy, bw - 26, 36, size=10,
             family=SANS, fg=RO_SUB, bg=RO_BG)
    # §7.1 Sts:CylPressure: INVALID ("n/a") until a pressure PV is configured in Cfg:CYL. With
    # no gauge configured (the usual case) the row is hidden, since "NaN psi" in an alarm border
    # reads as a fault; a configured gauge that fails still shows its alarm border.
    cyl_lbl = label(c, "cyl_pressure_lbl", "cylinder pressure:", x + 8, sy + 38, 100, 16,
                    size=10, fg=RO_SUB)
    cyl_p = textupdate(c, "cyl_pressure", P + "Sts:CylPressure", x + 108, sy + 38, bw - 116,
                       16, size=10, fg=RO_SUB, units=True)
    for wd in (cyl_lbl, cyl_p):
        wd.rule("hide_without_gauge", "visible", [P + "Cfg:Active:CYL"],
                [("pvStr0 == \"\"", False)])

    # State row and progress line
    y = 200
    state_label(c, 10, y, 116, 24)
    led(c, "in_range_led", P + "Sts:InRange", 134, y + 5, off=LED_OFF_AMBER)
    textupdate(c, "in_range", P + "Sts:InRange", 152, y, 104, 24, size=12, border=False)
    textupdate(c, "state_desc", P + "Sts:StateDesc.VAL$", 262, y, 528, 24, size=12, fg=LBL,
               fmt=FMT_STRING, border=False)
    rect(c, "progress_box", 10, 230, 780, 26, WHITE, line=BOX_LINE, line_width=1)
    longtext(c, "progress", P + "Sts:Progress", 14, 232, 772, 22, bg=WHITE, valign=1)

    # Mode and target
    y = 264
    label(c, "mode_lbl", "Enclosure mode (Mode)", 10, y, 385, 16, size=11)
    label(c, "target_lbl", "O2 target, % (Par:target)", 405, y, 385, 16, size=11)
    combo(c, "mode", P + "Mode", 10, y + 18, 385, 28)
    textentry(c, "par_target", P + "Par:target", 405, y + 18, 345, 28,
              tooltip=param_tooltip(PARAM["target"]), size=13)
    label(c, "unit_target", PARAM["target"]["unit"], 756, y + 18, 34, 28, size=12, fg=UNIT)

    # Buttons
    y = 320
    nx = command_buttons(c, 10, y, bw, 36, gap)
    button_open(c, "btn_admin", "Admin…", "sampleGas_admin.bob", nx, y, bw, 36, size=13)

    # Last action
    y = 364
    label(c, "last_action_lbl", "Last action:", 10, y, 80, 20, size=12)
    textupdate(c, "last_action", P + "Sts:LastAction.VAL$", 90, y, 700, 20, size=12, fg=LBL,
               fmt=FMT_STRING, border=False)

    # Trend: O2 and flow, log axes, target line (a trace of Par:target on the O2 axis; solid,
    # because the 4.7.4 stripchart trace has no line-style property, so it cannot be dashed)
    ch = c.widget("stripchart", "trend", 10, 392, 780, 256)
    ch.geom().set("show_toolbar", False).set("show_legend", True)
    ch.set("start", "30 minutes").set("end", "now")
    axes = ET.SubElement(ch.el, "y_axes")
    for ttl, lo, hi in (("O2 (%)", 0.1, 25.0), ("He flow (SLPM)", 0.01, 20.0)):
        a = ET.SubElement(axes, "y_axis")
        for tag, v in (("title", ttl), ("autoscale", False), ("log_scale", True),
                       ("minimum", lo), ("maximum", hi), ("show_grid", True), ("visible", True)):
            ET.SubElement(a, tag).text = _text(v)
    traces = ET.SubElement(ch.el, "traces")
    for name, pv, axis, colour, lw in (("O2 (Sts:O2)", "Sts:O2", 0, "#2f6fbf", 2),
                                       ("He flow (Sts:Flow)", "Sts:Flow", 1, "#e08a1e", 2),
                                       ("O2 target", "Par:target", 0, "#c00000", 1)):
        t = ET.SubElement(traces, "trace")
        ET.SubElement(t, "name").text = name
        ET.SubElement(t, "y_pv").text = P + pv
        ET.SubElement(t, "axis").text = str(axis)
        ET.SubElement(t, "trace_type").text = "2"
        _color(t, "color", colour)
        ET.SubElement(t, "line_width").text = str(lw)
        ET.SubElement(t, "point_type").text = "0"
        ET.SubElement(t, "point_size").text = "10"
        ET.SubElement(t, "visible").text = "true"
    return serialize(root, GEN_NOTE + " Spec 13.1 full panel.")


# ---------------------------------------------------------------------------
# 13.2 Admin
# ---------------------------------------------------------------------------

def section_header(c, name, text, y, x=10, w=800):
    label(c, name, text, x, y, w, 20, size=12, style="BOLD", fg=TEXT)


def build_admin():
    W = 820
    root, c = new_display("Sample gas: Admin", W, 100)
    title(c, suffix="· Admin", show_prefix=True)

    # Write switch (Par:writeEnable), two buttons, each with a confirmation (§13.2, §8.20)
    bg, fg, border = SWITCH_STRIP
    rect(c, "switch_strip", 10, 38, 800, 38, bg, line=border, line_width=1)
    label(c, "switch_lbl", "Writes to the Alicat", 18, 45, 150, 24, size=12, style="BOLD",
          fg=fg)
    we = P + "Par:writeEnable"
    sh = button_write(c, "btn_shadow", "Shadow (off)", we, 0, 172, 44, 112, 26,
                      confirm=CONFIRM_SHADOW_STATIC, enabled=False)
    lv = button_write(c, "btn_live", "Live (on)", we, 1, 290, 44, 100, 26,
                      confirm=CONFIRM_LIVE_STATIC, enabled=False)
    confirm_pvs = [P + "Cfg:Active:MFC", P + "Sts:SetpointRBV"]
    sh.rule("confirm_text", "confirm_message", confirm_pvs, [("true", CONFIRM_SHADOW_EXPR)],
            out_exp=True)
    lv.rule("confirm_text", "confirm_message", confirm_pvs, [("true", CONFIRM_LIVE_EXPR)],
            out_exp=True)
    # Only the button that changes something is enabled; the current setting is highlighted.
    sh.rule("enable_if_live", "enabled", [we], [("pv0 == 1", True)])
    sh.rule("highlight", "background_color", [we], [("pv0 == 0", SHADOW_BADGE[0])])
    lv.rule("enable_if_shadow", "enabled", [we], [("pv0 == 0", True)])
    lv.rule("highlight", "background_color", [we], [("pv0 == 1", LIVE_ON)])
    label(c, "switch_note", "Each switch asks for confirmation. The valve never moves at "
          "the switch.", 400, 40, 405, 34, size=11, fg=fg, wrap=True)

    # Commands
    y = 84
    button_write(c, "btn_new_cyl", "New He cylinder fitted", P + "Cmd:NewCylinder", 1, 10, y,
                 176, 26, confirm=CONFIRM_NEW_CYL)
    button_write(c, "btn_new_run", "Mark new user run", P + "Cmd:MarkNewRun", 1, 192, y, 150, 26)
    button_write(c, "btn_release", "Release control (→ IDLE)", P + "Cmd:ReleaseIdle", 1,
                 348, y, 190, 26, confirm=CONFIRM_RELEASE)
    button_open(c, "btn_deep", "Deep admin…", "sampleGas_deep.bob", 544, y, 120, 26)

    # A-level parameters, three columns (mockup order)
    y = 118
    section_header(c, "hdr_params", "Parameters", y)
    y += 22
    col_w = 268
    for i, (key, text) in enumerate(ADMIN_LABELS):
        r, col = divmod(i, 3)
        param_row(c, PARAM[key], 10 + col * col_w, y + r * 24, 140, 70, 50, text=text)
    y += ((len(ADMIN_LABELS) + 2) // 3) * 24 + 8

    # Per-mode PID gains and limits (§13.2: KP, KI, drvh, drvl for A-D)
    section_header(c, "hdr_gains", "PID gains and limits per enclosure mode", y)
    y += 22
    cols = [("KP", "KP (at 0.99 %)"), ("KI", "KI (1/s)"), ("drvh", "Max flow (SLPM)"),
            ("drvl", "Min flow (SLPM)")]
    label(c, "gains_h_mode", "Mode", 10, y, 170, 18, size=11)
    for j, (_, text) in enumerate(cols):
        label(c, f"gains_h_{j}", text, 190 + j * 130, y, 124, 18, size=11)
    y += 20
    mf = {m["field"]: m for m in T.MODE_FIELDS}
    for i, x in enumerate(T.MODE_SLOTS):
        ry = y + i * 24
        label(c, f"gains_slot_{x}", f"{x}:", 10, ry, 20, 22, size=11, fg=TEXT)
        textupdate(c, f"gains_name_{x}", P + f"Mode:{x}:name", 30, ry, 150, 22, size=11,
                   border=False)
        for j, (f, _) in enumerate(cols):
            m = mf[f]
            textentry(c, f"mode_{x}_{f}", P + f"Mode:{x}:{f}", 190 + j * 130, ry, 110, 22,
                      tooltip=f"Mode:{x}:{f}, limits {m['min']:g} to {m['max']:g} {m['unit']}")
    y += 4 * 24 + 8

    # Overrides
    label(c, "overrides_lbl", "Overrides: count", 10, y, 120, 22, size=12, style="BOLD",
          fg=TEXT)
    textupdate(c, "override_count", P + "Diag:OverrideCount", 130, y, 50, 22, size=12,
               border=False)
    button_write(c, "btn_reset_count", "Reset count", P + "Cmd:ResetOverrideCount", 1, 184,
                 y - 1, 110, 24, size=11)
    y += 26
    longtext(c, "override_log", P + "Diag:OverrideLog", 10, y, 800, 84)
    y += 92

    # Helium usage report (waveform CHAR, read as a string)
    section_header(c, "hdr_report", "Helium usage report (He:Rep:Text)", y)
    y += 22
    longtext(c, "usage_report", P + "He:Rep:Text", 10, y, 800, 100, suffix="")
    y += 108

    # Live internals: epid, forecast windows, key Diag values
    section_header(c, "hdr_internals", "Live internals", y)
    y += 22
    label(c, "epid_lbl", "epid", 10, y, 40, 20, size=11, style="BOLD", fg=TEXT)
    for i, f in enumerate(EPID_FIELDS):
        r, col = divmod(i, 6)
        x = 50 + col * 128
        label(c, f"epid_{f}_lbl", f, x, y + r * 20, 44, 20, size=11, family=MONO)
        textupdate(c, f"epid_{f}", P + f"PID.{f}", x + 44, y + r * 20, 80, 20, size=11,
                   family=MONO, border=False)
    y += 2 * 20 + 6

    label(c, "fc_lbl", "forecast", 10, y, 100, 20, size=11, style="BOLD", fg=TEXT)
    for j, (w_, text) in enumerate(FORECAST_WINDOWS + [("median", "median")]):
        label(c, f"fc_h_{w_}", text, 120 + j * 110, y, 100, 20, size=11, halign=2)
    y += 20
    label(c, "fc_est_lbl", "run-out (h)", 10, y, 100, 20, size=11)
    label(c, "fc_rate_lbl", "usage (L/day)", 10, y + 20, 100, 20, size=11)
    for j, (w_, _) in enumerate(FORECAST_WINDOWS):
        textupdate(c, f"fc_est_{w_}", P + f"He:Est{w_}H", 120 + j * 110, y, 100, 20, size=11,
                   family=MONO, halign=2, border=False)
        textupdate(c, f"fc_rate_{w_}", P + f"He:Rate{w_}", 120 + j * 110, y + 20, 100, 20,
                   size=11, family=MONO, halign=2, border=False)
    textupdate(c, "fc_est_median", P + "He:EmptyMedianH", 120 + 5 * 110, y, 100, 20, size=11,
               family=MONO, halign=2, border=False)
    y += 2 * 20 + 6

    label(c, "diag_lbl", "diagnostics", 10, y, 100, 20, size=11, style="BOLD", fg=TEXT)
    y += 20
    for i, (pv, text) in enumerate(ADMIN_DIAG):
        r, col = divmod(i, 4)
        x = 10 + col * 200
        label(c, f"diag_{i}_lbl", text, x, y + r * 20, 100, 20, size=11)
        textupdate(c, f"diag_{i}", P + pv, x + 100, y + r * 20, 96, 20, size=11, family=MONO,
                   units=True, border=False)
    y += ((len(ADMIN_DIAG) + 3) // 4) * 20 + 10
    root.find("height").text = str(y)
    return serialize(root, GEN_NOTE + " Spec 13.2 Admin.")


# ---------------------------------------------------------------------------
# 13.3 Deep admin
# ---------------------------------------------------------------------------

GROUP_ROW_H = 24
GROUP_TOP = 26      # title bar + border of a GROUP-style group (children start below it)
GROUP_BOTTOM = 10
GROUP_RIGHT_INSET = 12  # free space right of the unit column inside a group box (fix round 1)


def build_deep():
    W = 960
    root, c = new_display("Sample gas: Deep admin", W, 100)
    title(c, suffix="· Deep admin", show_prefix=True)

    # Linked PV names (§7.9, §13.3)
    y = 38
    section_header(c, "hdr_names", "Linked PV names", y, w=940)
    y += 22
    heads = [("", 10, 110), ("Edit (Cfg:*)", 125, 230), ("In use (Cfg:Active:*)", 365, 230),
             ("Conn.", 603, 44), ("Default (Cfg:Default:*)", 650, 230)]
    for j, (text, x, w) in enumerate(heads):
        if text:
            label(c, f"names_h_{j}", text, x, y, w, 18, size=11)
    y += 20
    for i, (f, text) in enumerate(LINKED_NAMES):
        ry = y + i * 26
        label(c, f"names_lbl_{f}", text, 10, ry, 110, 22, size=11, fg=TEXT)
        textentry(c, f"cfg_{f}", P + f"Cfg:{f}", 125, ry, 230, 22, size=11)
        textupdate(c, f"cfg_active_{f}", P + f"Cfg:Active:{f}", 365, ry, 230, 22, size=11,
                   family=MONO, border=False)
        if f != "STN":
            led(c, f"cfg_conn_{f}", P + f"Cfg:Conn:{f}", 618, ry + 4,
                off=LED_OFF_GREY if f == "CYL" else LED_OFF_RED)
        textupdate(c, f"cfg_default_{f}", P + f"Cfg:Default:{f}", 650, ry, 230, 22, size=11,
                   family=MONO, fg=GREY_DEFAULT, border=False)
    y += 4 * 26 + 4

    ap = button_write(c, "btn_apply", "Apply PV names", P + "Cfg:Apply", 1, 10, y, 140, 26,
                      confirm=CONFIRM_APPLY, enabled=False,
                      tooltip="Enabled only in IDLE (the controller does not own the flow)")
    ap.rule("idle_only", "enabled", [P + "Sts:State"], [("pv0 == 0", True)])
    button_write(c, "btn_restore", "Restore defaults", P + "Cfg:RestoreDefaults", 1, 156, y,
                 140, 26, tooltip="Copy the macro defaults into the fields (does not apply them)")
    pend = label(c, "pending", "unapplied edits", 302, y + 2, 112, 22, size=11, style="BOLD",
                 fg=SHADOW_BADGE[1], bg=SHADOW_BADGE[0], halign=1, visible=False)
    pend.rule("show_if_pending", "visible", [P + "Cfg:Pending"], [("pv0 == 1", True)])
    longtext(c, "cfg_status", P + "Cfg:Status", 420, y + 2, 530, 22, size=11, family=SANS,
             fg=LBL, bg=PANEL_BG, valign=1)
    y += 36

    # Enclosure modes: name, baseFlow, n (§13.3)
    section_header(c, "hdr_modes", "Enclosure modes", y, w=940)
    y += 22
    for j, (text, x) in enumerate((("Slot", 10), ("Name", 60), ("Base flow (SLPM)", 300),
                                   ("Exponent n", 430))):
        label(c, f"modes_h_{j}", text, x, y, 124 if j else 44, 18, size=11)
    y += 20
    mf = {m["field"]: m for m in T.MODE_FIELDS}
    for i, x in enumerate(T.MODE_SLOTS):
        ry = y + i * 24
        label(c, f"modes_slot_{x}", x, 10, ry, 40, 22, size=11, fg=TEXT)
        textentry(c, f"mode_{x}_name", P + f"Mode:{x}:name", 60, ry, 230, 22,
                  tooltip=f"Mode:{x}:name (the Mode menu text)")
        for f, fx in (("baseFlow", 300), ("n", 430)):
            m = mf[f]
            textentry(c, f"mode_{x}_{f}", P + f"Mode:{x}:{f}", fx, ry, 120, 22,
                      tooltip=f"Mode:{x}:{f}, limits {m['min']:g} to {m['max']:g} {m['unit']}")
    y += 4 * 24 + 8

    # Nine groups of D-level parameters (§13.3), in the spec order
    n_d = sum(1 for p in T.PARAMS if p["level"] == "D")
    section_header(c, "hdr_groups", f"Thresholds, timing and limits ({n_d} parameters in nine "
                   "groups)", y, w=940)
    y += 24
    col_w, gap = 308, 8
    lbl_w, ent_w, unit_w = 136, 78, 54
    # param_row's right edge (x 4 + label + entry + 6 + unit), plus the ~10 px left inset of a
    # GROUP box, must leave GROUP_RIGHT_INSET free inside the box.
    assert 4 + lbl_w + ent_w + 6 + unit_w + 10 <= col_w - GROUP_RIGHT_INSET
    for row in DEEP_LAYOUT:
        bottoms = []
        for ci, col in enumerate(row):
            gx, gy = 10 + ci * (col_w + gap), y
            for gname in col:
                keys = T._D_GROUP_KEYS[gname]
                gh = GROUP_TOP + len(keys) * GROUP_ROW_H + GROUP_BOTTOM
                g = group(c, gname, gx, gy, col_w, gh)     # Phoebus shows the name as title
                for k, key in enumerate(keys):
                    param_row(g, PARAM[key], 4, k * GROUP_ROW_H, lbl_w, ent_w, unit_w)
                gy += gh + 6
            bottoms.append(gy)
        y = max(bottoms) + 4
    label(c, "groups_note", "Each field: short label, entry box, unit (from "
          "ioc/tools/sg_pvs.py). Hover a field for its key, meaning, default and limits.",
          10, y, 940, 18, size=10, fg=UNIT)
    y += 26
    root.find("height").text = str(y)
    return serialize(root, GEN_NOTE + " Spec 13.3 Deep admin.")


# ---------------------------------------------------------------------------
# 13.4 Alarm server configuration and archiver list
# ---------------------------------------------------------------------------

# Operator-facing description per alarm (spec 7.5, 8.14 and the sections that raise them).
ALARM_DESCRIPTIONS = {
    "OpenStop": "Enclosure opened or not sealed: helium flow stopped (OPEN_STOP)",
    "PurgeIncomplete": "Purge timed out before the O2 reached target minus delta",
    "O2Bad": "O2 reading invalid or frozen",
    "OpenLoop": "O2 unavailable: running blind at fixed flow (OPEN_LOOP)",
    "Override": "Controller overrode an MFC setting (see the Admin override log)",
    "HoldStuck": "MFC on hold and cannot be resumed",
    "Mismatch": "Flow mismatch: cylinder empty or MFC fault?",
    "FlowHigh": "Helium flow above the expected flow: check the enclosure seal",
    "FlowLow": "PID demand below the expected flow: wrong enclosure mode selected?",
    "Pinned": "PID pinned at maximum flow: check the enclosure seal",
    "O2High": "O2 above the target range",
    "NotReached": "O2 target not reached within the settle timeout",
    "CylLow": "Helium cylinder forecast to run out soon",
    "Gas": "MFC gas table is not He (flow reading wrong)",
}
assert set(ALARM_DESCRIPTIONS) == {n for n, _ in T.ALARM_TABLE}

PROD_STATIONS = [("15IDC sample gas", "15IDC:SampleGas:")]
BENCH_STATIONS = [("Bench station 1 sample gas", "SIM:SampleGas:"),
                  ("Bench station 2 sample gas", "SIM2:SampleGas:")]

HEARTBEAT_NOTE = """
Heartbeat and IOC-down (spec 13.4). Every PV below alarms as INVALID/Disconnected when the IOC
is down; the Sts:Heartbeat entry is the one meant for that case (delay 10 s, so a short network
glitch is not annunciated).

Staleness (the IOC alive but Sts:Heartbeat unchanged for more than 10 s, e.g. the sequencer
stopped): the Phoebus alarm server has no "value unchanged for N s" check, so the IOC database
computes it. Sts:TickAge (calc, SCAN 1 second) counts the seconds since Sts:Heartbeat last
changed and is MAJOR above 10 s (HIHI); its entry below annunciates that.
"""


def _alarm_pv(parent, name, description, delay=None):
    pv = ET.SubElement(parent, "pv", name=name)
    ET.SubElement(pv, "description").text = description
    ET.SubElement(pv, "enabled").text = "true"
    ET.SubElement(pv, "latching").text = "false"
    if delay is not None:
        ET.SubElement(pv, "delay").text = str(delay)
    return pv


def build_alarms(config_name, stations, purpose):
    root = ET.Element("config", name=config_name)
    for comp_name, prefix in stations:
        comp = ET.SubElement(root, "component", name=comp_name)
        for n, key in T.ALARM_TABLE:
            _alarm_pv(comp, f"{prefix}Alm:{n}", ALARM_DESCRIPTIONS[n])
        _alarm_pv(comp, f"{prefix}Sts:Heartbeat",
                  "Sample gas IOC down: heartbeat disconnected", delay=10)
        _alarm_pv(comp, f"{prefix}Sts:TickAge",
                  "Sample gas controller not ticking (IOC up, program stalled > 10 s)")
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    assert "--" not in purpose + HEARTBEAT_NOTE  # not allowed inside an XML comment
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            "<!-- GENERATED by ioc/tools/gen_screens.py from ioc/tools/sg_pvs.py: do not edit;"
            " regenerate. -->\n"
            f"<!-- {purpose} -->\n"
            f"<!--{HEARTBEAT_NOTE}-->\n" + body + "\n")


def build_alarms_prod():
    return build_alarms(
        "SampleGas", PROD_STATIONS,
        "Phoebus alarm-server configuration, PRODUCTION (spec 13.4). Not loaded by anything in "
        "this repository; importing it into the beamline alarm server is the user's decision. "
        "15IDE is deferred: add a second component with its prefix when it exists.")


def build_alarms_bench():
    return build_alarms(
        "SampleGasBench", BENCH_STATIONS,
        "Phoebus alarm-server configuration for the BENCH stations (spec 2.1 prefixes). For a "
        "local alarm server only; never import into the beamline alarm server.")


ARCHIVE_SCAN_1S = ["Sts:O2", "Sts:State", "Sts:Flow", "Sts:SetpointRBV", "Sts:LastCmd"]


def build_archive():
    lines = [
        "# GENERATED by ioc/tools/gen_screens.py from ioc/tools/sg_pvs.py -- do not edit; "
        "regenerate.",
        "# Archiver PV list for the 15LSS_sample_gas IOC, production (spec 13.4). Configuring "
        "the archiver is up to the user.",
        "# Columns: PV name, sampling period in s, method (SCAN = one sample per period;",
        "# MONITOR = every change, the period is only the buffer-size estimate).",
        "# 15IDE is deferred: repeat the block with its prefix when it exists.",
    ]
    for station, prefix in PROD_STATIONS:
        lines.append("")
        lines.append(f"# {station} ({prefix}): process values, 1 s")
        for s in ARCHIVE_SCAN_1S:
            lines.append(f"{prefix}{s}  1  SCAN")
        lines.append(f"# {station}: alarms, on change")
        for n, _ in T.ALARM_TABLE:
            lines.append(f"{prefix}Alm:{n}  1  MONITOR")
        lines.append(f"# {station}: linked PV names in use, on change (which PVs the traces "
                     "came from)")
        for f in ("MFC", "O2", "CYL", "STN"):
            lines.append(f"{prefix}Cfg:Active:{f}  1  MONITOR")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

OUTPUTS = {
    os.path.join(SCREENS, "sampleGas_simple.bob"): build_simple,
    os.path.join(SCREENS, "sampleGas_main.bob"): build_main,
    os.path.join(SCREENS, "sampleGas_admin.bob"): build_admin,
    os.path.join(SCREENS, "sampleGas_deep.bob"): build_deep,
    os.path.join(SCREENS, "sampleGas_alarms.xml"): build_alarms_prod,
    os.path.join(SCREENS, "sampleGas_alarms_bench.xml"): build_alarms_bench,
    os.path.join(SCREENS, "archive_pvs.txt"): build_archive,
}


def main(argv):
    check = "--check" in argv
    mismatches = []
    for path, builder in OUTPUTS.items():
        text = builder()
        if check:
            existing = None
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8", newline="") as f:
                    existing = f.read()
            if existing != text:
                mismatches.append(path)
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
    if check:
        if mismatches:
            for p in mismatches:
                print(f"gen_screens.py --check: OUT OF DATE: {p}", file=sys.stderr)
            return 1
        print("gen_screens.py --check: all generated files up to date")
        return 0
    print("gen_screens.py: wrote", ", ".join(os.path.basename(p) for p in OUTPUTS))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
