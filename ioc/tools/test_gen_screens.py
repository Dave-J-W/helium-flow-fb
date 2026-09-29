"""test_gen_screens.py: verify the generated Phoebus screens, alarm config and archive list.

Run: python -m unittest -v test_gen_screens   (from ioc/tools; stdlib unittest only; use the
project's venv interpreter, not a bare "python" on PATH).

What is checked (plan 2026-09-25-ioc-plan5-screens, Tasks 1, 2 and 4 steps 1-2):
- every .bob parses as XML, is a <display version="2.0.0"> and uses only macro P;
- every Par:*, Mode* and Cfg:* PV of sg_pvs.py is editable on exactly the screen its level says
  (U: full panel; A: Admin; D: Deep admin), and is referenced read-only elsewhere only where the
  layout needs it (ALLOWED_READ_ONLY below, each entry justified);
- the nine Deep admin groups appear in the spec order with their keys in the spec order;
- the Admin write switch: two buttons, each with a confirmation whose live text evaluates to the
  spec §13.2 wording; the Deep admin Apply PV names confirmation and IDLE-only enabling;
- the simple panel shows exactly the §13.0 PVs; the full panel the §13.1 PVs; one Resume Flow
  button whose enabling rule has the §13.1 truth table; the SHADOW MODE badge rule; the banner and
  state colour rules; the trend;
- the alarm configs list every Alm:* PV plus the heartbeat per station; the archive list has the
  §13.4 PVs at the §13.4 rates;
- gen_screens.py --check exits 0 (generated files are up to date).
"""
import os
import re
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
SCREENS = os.path.join(REPO_ROOT, "ioc", "screens")
SPEC_PATH = os.path.join(REPO_ROOT, "docs", "ioc", "15LSS_sample_gas_IOC_spec.md")
GEN = os.path.join(HERE, "gen_screens.py")

sys.path.insert(0, HERE)
import sg_pvs  # noqa: E402

BOB = {
    "simple": os.path.join(SCREENS, "sampleGas_simple.bob"),
    "main": os.path.join(SCREENS, "sampleGas_main.bob"),
    "admin": os.path.join(SCREENS, "sampleGas_admin.bob"),
    "deep": os.path.join(SCREENS, "sampleGas_deep.bob"),
}
ALARMS_PROD = os.path.join(SCREENS, "sampleGas_alarms.xml")
ALARMS_BENCH = os.path.join(SCREENS, "sampleGas_alarms_bench.xml")
ARCHIVE = os.path.join(SCREENS, "archive_pvs.txt")

# The simulator's STATES colours (docs/simulator; given in the task brief).
STATE_COLOURS = {
    0: "#8a8a8a",  # IDLE
    1: "#8e7cc3",  # PRECHECK
    2: "#2f6fbf",  # PURGE
    3: "#4f9aa8",  # HANDOFF
    4: "#4c9a2a",  # REGULATE
    5: "#e08a1e",  # OPEN_LOOP
    6: "#555555",  # FLOW_ZERO
    7: "#c00000",  # OPEN_STOP
}

with open(SPEC_PATH, encoding="utf-8") as _f:
    SPEC_LINES = _f.read().splitlines()


def _section(tag):
    start = next(i for i, l in enumerate(SPEC_LINES) if l.startswith(f"### {tag} "))
    end = len(SPEC_LINES)
    for i in range(start + 1, len(SPEC_LINES)):
        if SPEC_LINES[i].startswith("### ") or SPEC_LINES[i].startswith("## "):
            end = i
            break
    return SPEC_LINES[start:end]


def _ws(s):
    return " ".join(s.split())


# ---------------------------------------------------------------------------
# .bob helpers
# ---------------------------------------------------------------------------

_ROOTS = {}


def root(screen):
    if screen not in _ROOTS:
        _ROOTS[screen] = ET.parse(BOB[screen]).getroot()
    return _ROOTS[screen]


def norm_pv(name):
    """'$(P)Sts:Banner.VAL$' -> 'Sts:Banner'; formula PVs are handled by pv_names()."""
    name = name.strip()
    if name.startswith("$(P)"):
        name = name[len("$(P)"):]
    if name.endswith(".VAL$"):
        name = name[:-len(".VAL$")]
    return name


def pv_names(raw):
    raw = raw.strip()
    if not raw:
        return []
    if raw.startswith("="):  # formula: PV names in single quotes
        return [norm_pv(n) for n in re.findall(r"'([^']+)'", raw)]
    return [norm_pv(raw)]


def refs(screen):
    """List of (kind, pv, widget) for every PV reference on a screen.

    kind: 'widget' (the widget's own pv_name), 'rule', 'write' (write_pv action), 'trace'.
    """
    out = []
    for w in root(screen).iter("widget"):
        pn = w.find("pv_name")
        if pn is not None and pn.text:
            for n in pv_names(pn.text):
                out.append(("widget", n, w))
        for r in w.findall("rules/rule"):
            for p in r.findall("pv_name"):
                for n in pv_names(p.text or ""):
                    out.append(("rule", n, w))
        for a in w.findall("actions/action"):
            if a.get("type") == "write_pv":
                for n in pv_names(a.findtext("pv_name", "")):
                    out.append(("write", n, w))
        for t in w.findall("traces/trace"):
            for n in pv_names(t.findtext("y_pv", "")):
                out.append(("trace", n, w))
    return out


def all_pvs(screen):
    return {pv for _, pv, _ in refs(screen)}


def writable_pvs(screen):
    s = set()
    for kind, pv, w in refs(screen):
        if kind == "write" or (kind == "widget" and w.get("type") in ("textentry", "combo")):
            s.add(pv)
    return s


def buttons_writing(screen, pv):
    out = []
    for w in root(screen).iter("widget"):
        if w.get("type") != "action_button":
            continue
        for a in w.findall("actions/action"):
            if a.get("type") == "write_pv" and norm_pv(a.findtext("pv_name", "")) == pv:
                out.append((w, a.findtext("value", "")))
    return out


def open_display_targets(screen):
    out = []
    for w in root(screen).iter("widget"):
        for a in w.findall("actions/action"):
            if a.get("type") == "open_display":
                out.append((a.findtext("file", ""), w.findtext("text", "")))
    return out


def rules_of(w, prop_id):
    return [r for r in w.findall("rules/rule") if r.get("prop_id") == prop_id]


def rule_pvs(r):
    return [norm_pv(p.text or "") for p in r.findall("pv_name")]


def js_to_py(exp):
    """The translation Phoebus 4.7 applies to rule expressions (RuleToScript)."""
    exp = exp.replace("&&", " and ").replace("||", " or ")
    exp = re.sub(r"!(?!=)", " not ", exp)
    exp = re.sub(r"\btrue\b", "True", exp)
    exp = re.sub(r"\bfalse\b", "False", exp)
    return exp


def eval_rule(r, values):
    """Evaluate a rule the way the generated Jython does: the first true bool_exp wins; returns
    that expression's value text (or its evaluated expression if out_exp), else None (the
    widget keeps its file value)."""
    env = {}
    for i, v in enumerate(values):
        if isinstance(v, str):
            env[f"pvStr{i}"] = v
        else:
            env[f"pv{i}"] = float(v)
            env[f"pvInt{i}"] = int(v)
            env[f"pvStr{i}"] = str(v)
    for e in r.findall("exp"):
        if eval(js_to_py(e.get("bool_exp")), {}, env):
            if r.get("out_exp") == "true":
                return eval(js_to_py(e.findtext("expression")), {}, env)
            val = e.find("value")
            col = val.find("color")
            if col is not None:
                return "#%02x%02x%02x" % tuple(int(col.get(c)) for c in ("red", "green", "blue"))
            return (val.text or "").strip()
    return None


def widget_text(w):
    return w.findtext("text", "")


def file_colour(w, tag):
    col = w.find(f"{tag}/color")
    return "#%02x%02x%02x" % tuple(int(col.get(c)) for c in ("red", "green", "blue"))


DB_PATH = os.path.join(REPO_ROOT, "ioc", "lssSampleGas", "lssSampleGasApp", "Db", "sampleGas.db")
_DB = {}


def db_records():
    """{record name without $(P): record type} parsed from the generated sampleGas.db."""
    if not _DB:
        with open(DB_PATH, encoding="utf-8") as f:
            for rtyp, name in re.findall(r'^record\((\w+),\s*"\$\(P\)([^"]+)"\)', f.read(),
                                         re.M):
                _DB[name] = rtyp
    return _DB


# ---------------------------------------------------------------------------
# Level of each parameter-like PV (spec §9: U main panel, A Admin, D Deep admin; §7.4 and §13.2/
# §13.3 for the mode-slot fields; §13.3 for Cfg:*).
# ---------------------------------------------------------------------------

SCREEN_OF_LEVEL = {"U": "main", "A": "admin", "D": "deep"}
ADMIN_MODE_FIELDS = {"KP", "KI", "drvh", "drvl"}          # §13.2
DEEP_MODE_FIELDS = {"name", "baseFlow", "n"}              # §13.3


def level_pvs():
    """{pv: (screen, editable)} for every Par:*, Mode* and Cfg:* PV of the table."""
    out = {}
    for p in sg_pvs.PARAMS:
        out[f"Par:{p['key']}"] = (SCREEN_OF_LEVEL[p["level"]], True)
    for x in sg_pvs.MODE_SLOTS:
        for f in ["name"] + [m["field"] for m in sg_pvs.MODE_FIELDS]:
            scr = "admin" if f in ADMIN_MODE_FIELDS else "deep"
            out[f"Mode:{x}:{f}"] = (scr, True)
    for pv in sg_pvs.PVS:
        s = pv["suffix"]
        if s == "Mode":
            out[s] = ("main", True)
        elif s.startswith("Cfg:"):
            editable = pv["rtyp"] in ("stringout", "bo")
            out[s] = ("deep", editable)
    return out


# Read-only uses of parameter-like PVs outside their level's screen, each needed by the layout.
ALLOWED_READ_ONLY = {
    "simple": {
        "Par:target",      # §13.0 "O2 large with the target" (shown, not editable)
        "Mode",            # §13.0 "The mode is shown, not editable"
        "Par:lidLevel",    # Resume Flow enabling rule (§13.0 = §13.1)
        "Cfg:Active:STN",  # mockup title "Sample gas <station>"
    },
    "main": {
        "Par:tol",         # §13.1 "Sts:O2, with target ± tol"
        "Par:lidLevel",    # Resume Flow enabling rule (§13.1)
        "Cfg:Active:STN",  # mockup title
        "Cfg:Active:CYL",  # hide the cylinder-pressure row when no gauge is configured
    },
    "admin": {
        "Mode:A:name", "Mode:B:name", "Mode:C:name", "Mode:D:name",  # row labels of the gains table
        "Cfg:Active:MFC",  # §13.2 confirmation text "The Alicat (<Cfg:Active:MFC>) ..."
        "Cfg:Active:STN",  # title
    },
    "deep": set(),
}


class TestFilesParse(unittest.TestCase):
    def test_bob_files_parse_as_display_2(self):
        for scr, path in BOB.items():
            with self.subTest(screen=scr):
                r = ET.parse(path).getroot()
                self.assertEqual(r.tag, "display")
                self.assertEqual(r.get("version"), "2.0.0")
                self.assertTrue(len(list(r.iter("widget"))) > 5)

    def test_only_macro_p(self):
        for scr, path in BOB.items():
            with self.subTest(screen=scr):
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                macros = set(re.findall(r"\$\((\w+)\)", text))
                self.assertEqual(macros - {"P"}, set(), f"{scr}: macros other than P")

    def test_no_direct_alicat_or_o2_pvs(self):
        # §13.1: screens read the Alicat through the Sts: mirrors only.
        for scr in BOB:
            for pv in all_pvs(scr):
                with self.subTest(screen=scr, pv=pv):
                    self.assertFalse(pv.startswith("15ID") or pv.startswith("SIM:"), pv)
                    self.assertFalse("$(MFC)" in pv or "$(O2)" in pv, pv)

    def test_every_pv_exists_in_table_and_db(self):
        known = {p["suffix"] for p in sg_pvs.PVS} | {f"Par:{p['key']}" for p in sg_pvs.PARAMS}
        for x in sg_pvs.MODE_SLOTS:
            for f in ["name"] + [m["field"] for m in sg_pvs.MODE_FIELDS]:
                known.add(f"Mode:{x}:{f}")
        db = db_records()
        self.assertTrue(len(db) > 200)
        for scr in BOB:
            for pv in all_pvs(scr):
                with self.subTest(screen=scr, pv=pv):
                    record = pv.split(".")[0]  # PID.FBON -> PID
                    self.assertIn(record, known)
                    self.assertIn(record, db, "not a record of sampleGas.db")

    def test_val_dollar_only_on_long_strings(self):
        # <pv>.VAL$ (CA long-string access) is used exactly on the lsi/lso records: never on
        # another type, and never missing on an lsi (a plain CA string stops at 40 characters).
        db = db_records()
        seen = set()
        for scr, path in BOB.items():
            with open(path, encoding="utf-8") as f:
                text = f.read()
            for rec, dollar in re.findall(r"\$\(P\)([A-Za-z0-9:_]+)(\.VAL\$)?(?=[<'])", text):
                with self.subTest(screen=scr, record=rec):
                    long_str = db.get(rec) in ("lsi", "lso")
                    self.assertEqual(bool(dollar), long_str, f"{rec} ({db.get(rec)})")
                    if dollar:
                        seen.add(rec)
        self.assertEqual(seen, {"Sts:Banner", "Sts:StateDesc", "Sts:Progress", "Sts:LastAction",
                                "He:ForecastText", "Diag:OverrideLog", "Cfg:Status"})

    def test_widget_versions_are_the_4_7_4_ones(self):
        # getVersion() of Phoebus 4.7.4 (app-display-model jar): Widget base 2.0.0; TextEntry,
        # ActionButton and Group 3.0.0; Stripchart 2.1.0. Older versions trigger legacy
        # configurators (stripchart < 2.1 replaces `start` by time_range / "1 minute"; group < 3
        # copies foreground_color into line_color).
        want = {"label": "2.0.0", "textupdate": "2.0.0", "led": "2.0.0", "combo": "2.0.0",
                "rectangle": "2.0.0", "textentry": "3.0.0", "action_button": "3.0.0",
                "group": "3.0.0", "stripchart": "2.1.0"}
        for scr in BOB:
            for w in root(scr).iter("widget"):
                with self.subTest(screen=scr, widget=w.findtext("name")):
                    self.assertIn(w.get("type"), want)
                    self.assertEqual(w.get("version"), want[w.get("type")])

    def test_check_mode_exits_zero(self):
        r = subprocess.run([sys.executable, GEN, "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


# ---------------------------------------------------------------------------
# Task 1: levels, Admin, Deep admin
# ---------------------------------------------------------------------------

class TestLevels(unittest.TestCase):
    def test_each_parameter_on_its_screen(self):
        for pv, (scr, editable) in level_pvs().items():
            with self.subTest(pv=pv):
                if editable:
                    self.assertIn(pv, writable_pvs(scr), f"{pv} not editable on {scr}")
                else:
                    self.assertIn(pv, all_pvs(scr), f"{pv} not shown on {scr}")

    def test_parameters_editable_nowhere_else(self):
        lv = level_pvs()
        for scr in BOB:
            for pv in writable_pvs(scr):
                if pv in lv:
                    with self.subTest(screen=scr, pv=pv):
                        self.assertEqual(lv[pv][0], scr, f"{pv} editable on {scr}")

    def test_read_only_cross_references_are_justified(self):
        lv = level_pvs()
        for scr in BOB:
            foreign = {pv for pv in all_pvs(scr) if pv in lv and lv[pv][0] != scr}
            with self.subTest(screen=scr):
                self.assertEqual(foreign - ALLOWED_READ_ONLY[scr], set())

    def test_parameter_fields_have_unit_and_limits(self):
        # Every Par:* entry carries its unit (a label named unit_<key>) and a tooltip with limits.
        for p in sg_pvs.PARAMS:
            if p["key"] == "writeEnable":
                continue
            scr = SCREEN_OF_LEVEL[p["level"]]
            with self.subTest(key=p["key"]):
                entries = [w for k, pv, w in refs(scr)
                           if k == "widget" and pv == f"Par:{p['key']}"
                           and w.get("type") == "textentry"]
                self.assertEqual(len(entries), 1)
                tip = entries[0].findtext("tooltip", "")
                self.assertIn(p["key"], tip)
                self.assertIn("limits", tip)
                units = [w for w in root(scr).iter("widget") if w.findtext("name") ==
                         f"unit_{p['key']}"]
                self.assertEqual(len(units), 1)
                self.assertEqual(widget_text(units[0]), p["unit"])


class TestDeepAdmin(unittest.TestCase):
    def test_nine_groups_in_order_with_keys(self):
        # the group's name is its visible title in Phoebus
        groups = [w for w in root("deep").iter("widget") if w.get("type") == "group"
                  and w.findtext("name", "") in sg_pvs.DEEP_GROUPS]
        self.assertEqual([g.findtext("name") for g in groups], sg_pvs.DEEP_GROUPS)
        for g in groups:
            title = g.findtext("name")
            with self.subTest(group=title):
                keys = []
                for w in g.iter("widget"):
                    if w.get("type") == "textentry":
                        keys.append(norm_pv(w.findtext("pv_name"))[len("Par:"):])
                self.assertEqual(keys, sg_pvs._D_GROUP_KEYS[title])
                self.assertEqual(sum(1 for w in g.iter("widget") if w.get("type") == "label"
                                     and widget_text(w) == title), 0,
                                 "group title is the group widget's name, not a label")

    def test_group_rows_leave_a_right_inset(self):
        # Children of a GROUP box start ~10 px in from its left border; the rightmost child must
        # still leave >= 12 px free inside the box so units such as "SLPM/s" are not clipped.
        for g in root("deep").iter("widget"):
            if g.get("type") != "group":
                continue
            width = int(g.findtext("width"))
            right = max(int(w.findtext("x")) + int(w.findtext("width"))
                        for w in g.findall("widget"))
            with self.subTest(group=g.findtext("name")):
                self.assertLessEqual(right + 10, width - 12)

    def test_49_d_level_fields(self):
        n = sum(1 for k, pv, w in refs("deep") if k == "widget" and pv.startswith("Par:")
                and w.get("type") == "textentry")
        self.assertEqual(n, 49)

    def test_linked_pv_rows(self):
        pvs = all_pvs("deep")
        for f in ["MFC", "O2", "CYL", "STN"]:
            with self.subTest(field=f):
                self.assertIn(f"Cfg:{f}", writable_pvs("deep"))
                self.assertIn(f"Cfg:Active:{f}", pvs)
                self.assertIn(f"Cfg:Default:{f}", pvs)
        leds = {pv for k, pv, w in refs("deep") if k == "widget" and w.get("type") == "led"}
        self.assertEqual({p for p in leds if p.startswith("Cfg:Conn:")},
                         {"Cfg:Conn:MFC", "Cfg:Conn:O2", "Cfg:Conn:CYL"})
        # the macro default is greyed
        for k, pv, w in refs("deep"):
            if pv.startswith("Cfg:Default:"):
                col = w.find("foreground_color/color")
                self.assertIsNotNone(col, pv)
                self.assertTrue(all(int(col.get(c)) >= 0x80 for c in ("red", "green", "blue")),
                                f"{pv} not greyed")

    def test_apply_and_restore_buttons(self):
        apply_ = buttons_writing("deep", "Cfg:Apply")
        self.assertEqual(len(apply_), 1)
        w, val = apply_[0]
        self.assertEqual(val, "1")
        self.assertEqual(w.findtext("show_confirm_dialog"), "true")
        msg = w.findtext("confirm_message", "")
        self.assertIn("Alicat", msg)
        self.assertIn("re-baselined", msg)
        self.assertIn("PC trial", msg)       # writes are disabled only in the trial (2026-09-28)
        self.assertEqual(w.findtext("enabled"), "false")  # enabled only by the IDLE rule
        (r,) = rules_of(w, "enabled")
        self.assertEqual(rule_pvs(r), ["Sts:State"])
        self.assertEqual(eval_rule(r, [0]), "true")
        for s in range(1, 8):
            self.assertIsNone(eval_rule(r, [s]))
        restore = buttons_writing("deep", "Cfg:RestoreDefaults")
        self.assertEqual([v for _, v in restore], ["1"])

    def test_pending_and_status(self):
        pvs = all_pvs("deep")
        self.assertIn("Cfg:Status", pvs)
        pend = [w for k, pv, w in refs("deep") if pv == "Cfg:Pending"]
        self.assertTrue(pend)
        texts = " ".join(widget_text(w) for w in pend)
        self.assertIn("unapplied edits", texts)

    def test_mode_names_base_flow_and_n(self):
        for x in sg_pvs.MODE_SLOTS:
            for f in ("name", "baseFlow", "n"):
                self.assertIn(f"Mode:{x}:{f}", writable_pvs("deep"))


def _spec_13_2_texts():
    body = " ".join(_section("13.2"))
    to_live = re.search(r"to normal: `([^`]*)`", body).group(1)
    to_shadow = re.search(r"to shadow: `([^`]*)`", body).group(1)
    return _ws(to_live), _ws(to_shadow)


class TestAdmin(unittest.TestCase):
    def _switch(self, value):
        bs = [(w, v) for w, v in buttons_writing("admin", "Par:writeEnable") if v == value]
        self.assertEqual(len(bs), 1, f"one button writing writeEnable={value}")
        return bs[0][0]

    def _check_confirm(self, w, spec_text):
        self.assertEqual(w.findtext("show_confirm_dialog"), "true")
        (r,) = rules_of(w, "confirm_message")
        self.assertEqual(r.get("out_exp"), "true")
        self.assertEqual(rule_pvs(r), ["Cfg:Active:MFC", "Sts:SetpointRBV"])
        got = eval_rule(r, ["SIM:Alicat1:", 0.2913])
        want = spec_text.replace("<Cfg:Active:MFC>", "SIM:Alicat1:").replace(
            "<Sts:SetpointRBV>", "0.29")
        self.assertEqual(_ws(got), want)
        # the static fallback (no PV values yet) still says what will happen
        self.assertTrue(w.findtext("confirm_message", "").startswith(spec_text.split("?")[0]))

    def test_write_enable_confirms_both_ways(self):
        to_live, to_shadow = _spec_13_2_texts()
        self._check_confirm(self._switch("1"), to_live)
        self._check_confirm(self._switch("0"), to_shadow)

    def test_write_enable_buttons_enabled_only_for_a_change(self):
        for value, enabled_when in (("1", 0), ("0", 1)):
            w = self._switch(value)
            self.assertEqual(w.findtext("enabled"), "false")
            (r,) = rules_of(w, "enabled")
            self.assertEqual(rule_pvs(r), ["Par:writeEnable"])
            self.assertEqual(eval_rule(r, [enabled_when]), "true")
            self.assertIsNone(eval_rule(r, [1 - enabled_when]))

    def test_admin_commands(self):
        for cmd in ("Cmd:NewCylinder", "Cmd:MarkNewRun", "Cmd:ReleaseIdle",
                    "Cmd:ResetOverrideCount"):
            with self.subTest(cmd=cmd):
                self.assertEqual([v for _, v in buttons_writing("admin", cmd)], ["1"])

    def test_admin_displays(self):
        pvs = all_pvs("admin")
        want = {"Diag:OverrideLog", "Diag:OverrideCount", "He:Rep:Text", "He:EmptyMedianH"}
        want |= {f"He:Est{w}H" for w in ("3d", "2d", "1d", "12h", "6h")}
        want |= {f"PID.{f}" for f in ("FBON", "VAL", "CVAL", "OVAL", "DRVL", "DRVH")}
        self.assertEqual(want - pvs, set())
        self.assertTrue(len([p for p in pvs if p.startswith("Diag:")]) >= 10)

    def test_admin_links_to_deep(self):
        self.assertIn("sampleGas_deep.bob", [f for f, _ in open_display_targets("admin")])


# ---------------------------------------------------------------------------
# Task 2: simple and full panels
# ---------------------------------------------------------------------------

SIMPLE_PVS = {
    # §13.0 "Shows only"
    "Sts:Banner", "Sts:WorstSevr", "Sts:WriteEnable", "Sts:O2", "Par:target", "Sts:InRange",
    "Sts:Flow", "He:EmptyMedianH", "He:LeftL", "Sts:State", "Sts:StateDesc",
    # §13.0 controls; the mode is shown
    "Cmd:Purge", "Cmd:FlowZero", "Cmd:ResumeFlow", "Mode",
    # Resume Flow enabling (same as §13.1)
    "Sts:O2Valid", "Par:lidLevel",
    # mockup title "Sample gas <station>"
    "Cfg:Active:STN",
    # §13.4: the "controller not ticking" overlay on the banner
    "Sts:TickAge",
}

MAIN_PVS = {
    "Sts:Banner", "Sts:WorstSevr", "Sts:O2", "Par:target", "Par:tol", "Sts:Flow",
    "Sts:ExpectedFlow", "Sts:MfcRunning", "Sts:SetpointRBV", "Sts:LastCmd", "He:LeftL",
    "He:ForecastText", "Sts:State", "Sts:StateDesc", "Sts:InRange", "Sts:Progress", "Mode",
    "Cmd:Purge", "Cmd:FlowZero", "Cmd:ResumeFlow", "Sts:LastAction", "Sts:WriteEnable",
    "Sts:CylPressure",  # §7.1 (fix round 1 ruling)
}


class TestSimplePanel(unittest.TestCase):
    def test_exactly_the_13_0_pvs(self):
        self.assertEqual(all_pvs("simple"), SIMPLE_PVS)

    def test_not_shown(self):
        pvs = all_pvs("simple")
        self.assertFalse([p for p in pvs if p.startswith("PID")])
        self.assertNotIn("Sts:SetpointRBV", pvs)
        self.assertNotIn("Sts:LastCmd", pvs)
        self.assertEqual(writable_pvs("simple"), {"Cmd:Purge", "Cmd:FlowZero", "Cmd:ResumeFlow"})
        self.assertFalse([w for w in root("simple").iter("widget")
                          if w.get("type") == "stripchart"])

    def test_opens_full_panel(self):
        self.assertIn("sampleGas_main.bob", [f for f, _ in open_display_targets("simple")])

    def test_helium_left_in_days(self):
        f = [w.findtext("pv_name") for w in root("simple").iter("widget")
             if "He:EmptyMedianH" in (w.findtext("pv_name") or "")]
        self.assertEqual(len(f), 1)
        self.assertRegex(f[0], r"^='\$\(P\)He:EmptyMedianH' */ *24$")


class TestFullPanel(unittest.TestCase):
    def test_13_1_pvs(self):
        self.assertEqual(MAIN_PVS - all_pvs("main"), set())

    def test_no_new_cylinder_button(self):
        self.assertEqual(buttons_writing("main", "Cmd:NewCylinder"), [])
        self.assertNotIn("Cmd:NewCylinder", all_pvs("main"))

    def test_controls(self):
        self.assertIn("Mode", {pv for k, pv, w in refs("main")
                               if k == "widget" and w.get("type") == "combo"})
        self.assertIn("Par:target", writable_pvs("main"))
        self.assertIn("sampleGas_admin.bob", [f for f, _ in open_display_targets("main")])

    def test_trend(self):
        charts = [w for w in root("main").iter("widget") if w.get("type") == "stripchart"]
        self.assertEqual(len(charts), 1)
        c = charts[0]
        ys = [norm_pv(t.findtext("y_pv")) for t in c.findall("traces/trace")]
        self.assertEqual(ys, ["Sts:O2", "Sts:Flow", "Par:target"])
        axes = c.findall("y_axes/y_axis")
        self.assertEqual(len(axes), 2)
        self.assertTrue(all(a.findtext("log_scale") == "true" for a in axes))
        o2_axis = [t.findtext("axis") for t in c.findall("traces/trace")]
        self.assertEqual(o2_axis, ["0", "1", "0"])  # the target line is on the O2 axis
        self.assertEqual(c.findtext("start"), "30 minutes")
        self.assertEqual(c.findtext("end"), "now")
        self.assertIsNone(c.find("time_range"))

    def test_cylinder_pressure_line(self):
        # §7.1 lists Sts:CylPressure on the main panel; shown under the cylinder readout.
        ws = [w for k, pv, w in refs("main") if k == "widget" and pv == "Sts:CylPressure"]
        self.assertEqual(len(ws), 1)
        self.assertNotEqual(ws[0].findtext("border_alarm_sensitive"), "false")  # INVALID shows
        self.assertIn("cylinder pressure:", [widget_text(w) for w in root("main").iter("widget")])


class TestSharedPanelRules(unittest.TestCase):
    def test_one_resume_flow_button_with_the_13_1_rule(self):
        for scr in ("simple", "main"):
            with self.subTest(screen=scr):
                bs = buttons_writing(scr, "Cmd:ResumeFlow")
                self.assertEqual(len(bs), 1)
                w, val = bs[0]
                self.assertEqual(val, "1")
                self.assertEqual(widget_text(w), "Resume Flow")
                self.assertEqual(w.findtext("enabled"), "false")
                self.assertEqual(w.findtext("tooltip"),
                                 "resume feedback from the current flow, without a purge")
                (r,) = rules_of(w, "enabled")
                self.assertEqual(rule_pvs(r), ["Sts:State", "Sts:O2Valid", "Sts:O2",
                                               "Par:lidLevel"])
                IDLE, REGULATE, OPEN_LOOP = 0, 4, 5
                truth = [
                    ((OPEN_LOOP, 1, 15.0, 10), True),   # OPEN_LOOP + valid: any O2
                    ((OPEN_LOOP, 0, 1.0, 10), False),
                    ((IDLE, 1, 0.9, 10), True),         # IDLE + valid + below lidLevel
                    ((IDLE, 1, 12.0, 10), False),
                    ((IDLE, 0, 0.9, 10), False),
                    ((REGULATE, 1, 0.9, 10), False),
                ]
                for s in range(8):
                    if s not in (IDLE, OPEN_LOOP):
                        truth.append(((s, 1, 0.9, 10), False))
                for vals, want in truth:
                    got = eval_rule(r, list(vals)) == "true"
                    self.assertEqual(got, want, vals)

    def test_purge_and_flow_zero_buttons(self):
        for scr in ("simple", "main"):
            for cmd, text in (("Cmd:Purge", "Purge"), ("Cmd:FlowZero", "Flow Zero")):
                with self.subTest(screen=scr, cmd=cmd):
                    bs = buttons_writing(scr, cmd)
                    self.assertEqual([(widget_text(w), v) for w, v in bs], [(text, "1")])

    def test_shadow_badge(self):
        for scr in ("simple", "main"):
            with self.subTest(screen=scr):
                badges = [w for w in root(scr).iter("widget")
                          if "SHADOW MODE" in widget_text(w)]
                self.assertEqual(len(badges), 1)
                b = badges[0]
                (r,) = rules_of(b, "visible")
                self.assertEqual(rule_pvs(r), ["Sts:WriteEnable"])
                # visible unless writes are known to be enabled (disconnected -> still shown)
                self.assertNotEqual(b.findtext("visible"), "false")
                self.assertEqual(eval_rule(r, [1]), "false")
                self.assertIsNone(eval_rule(r, [0]))
        self.assertIn("no writes", [widget_text(w) for w in root("main").iter("widget")
                                    if "SHADOW MODE" in widget_text(w)][0])

    def test_banner_coloured_by_worst_sevr(self):
        green, amber, red = "#c0dd97", "#fac775", "#e24b4a"
        for scr in ("simple", "main"):
            with self.subTest(screen=scr):
                ws = [w for k, pv, w in refs(scr) if k == "widget" and pv == "Sts:Banner"]
                self.assertEqual(len(ws), 1)
                w = ws[0]
                self.assertEqual(w.findtext("pv_name"), "$(P)Sts:Banner.VAL$")  # lsi > 40 chars
                self.assertEqual(w.findtext("format"), "6")
                (r,) = rules_of(w, "background_color")
                self.assertEqual(rule_pvs(r), ["Sts:WorstSevr"])
                self.assertEqual(eval_rule(r, [0]), green)
                self.assertEqual(eval_rule(r, [1]), amber)
                self.assertEqual(eval_rule(r, [2]), red)
                # no value (IOC down): the file colour applies, and it must not say "OK"
                self.assertNotIn(file_colour(w, "background_color"), (green, amber, red))
                self.assertNotEqual(w.findtext("border_alarm_sensitive"), "false")

    def test_state_label_colours(self):
        for scr in ("simple", "main"):
            with self.subTest(screen=scr):
                ws = [w for k, pv, w in refs(scr) if k == "widget" and pv == "Sts:State"]
                self.assertEqual(len(ws), 1)
                w = ws[0]
                (r,) = rules_of(w, "background_color")
                self.assertEqual(rule_pvs(r), ["Sts:State"])
                for s, col in STATE_COLOURS.items():
                    self.assertEqual(eval_rule(r, [s]), col, s)
                # fallback = the simulator's IOC_DOWN colour, text visible, border on
                self.assertEqual(file_colour(w, "background_color"), "#000000")
                self.assertEqual(file_colour(w, "foreground_color"), "#ffffff")
                self.assertNotEqual(w.findtext("border_alarm_sensitive"), "false")

    def test_in_range_led(self):
        for scr in ("simple", "main"):
            leds = [pv for k, pv, w in refs(scr) if k == "widget" and w.get("type") == "led"]
            self.assertIn("Sts:InRange", leds)


# ---------------------------------------------------------------------------
# Task 4, steps 1-2: alarm config and archive list
# ---------------------------------------------------------------------------

class TestAlarmConfig(unittest.TestCase):
    def _check(self, path, prefixes):
        r = ET.parse(path).getroot()
        self.assertEqual(r.tag, "config")
        comps = r.findall("component")
        self.assertEqual(len(comps), len(prefixes))
        for comp, prefix in zip(comps, prefixes):
            with self.subTest(prefix=prefix):
                pvs = comp.findall(".//pv")
                names = [p.get("name") for p in pvs]
                want = [f"{prefix}Alm:{n}" for n, _ in sg_pvs.ALARM_TABLE]
                want.append(f"{prefix}Sts:Heartbeat")
                want.append(f"{prefix}Sts:TickAge")         # staleness (spec 13.4)
                self.assertEqual(sorted(names), sorted(want))
                for p in pvs:
                    self.assertTrue(p.findtext("description", "").strip(), p.get("name"))
                    self.assertEqual(p.findtext("enabled"), "true")
                    self.assertEqual(p.findtext("latching"), "false")
        return r

    def test_production(self):
        self._check(ALARMS_PROD, ["15IDC:SampleGas:"])
        with open(ALARMS_PROD, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("SIM", text)  # the bench prefixes live only in the bench file
        self.assertIn("stale", text.lower())

    def test_bench(self):
        self._check(ALARMS_BENCH, ["SIM:SampleGas:", "SIM2:SampleGas:"])
        with open(ALARMS_BENCH, encoding="utf-8") as f:
            self.assertNotIn("15IDC:", f.read())

    def test_config_names_differ(self):
        a = ET.parse(ALARMS_PROD).getroot().get("name")
        b = ET.parse(ALARMS_BENCH).getroot().get("name")
        self.assertTrue(a and b and a != b)


class TestArchiveList(unittest.TestCase):
    def _rows(self):
        rows = {}
        with open(ARCHIVE, encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                pv, period, method = line.split()
                self.assertNotIn(pv, rows, f"duplicate {pv}")
                rows[pv] = (float(period), method)
        return rows

    def test_rates(self):
        rows = self._rows()
        P = "15IDC:SampleGas:"
        for s in ("Sts:O2", "Sts:State", "Sts:Flow", "Sts:SetpointRBV", "Sts:LastCmd"):
            self.assertEqual(rows.pop(P + s), (1.0, "SCAN"), s)
        for n, _ in sg_pvs.ALARM_TABLE:
            self.assertEqual(rows.pop(P + f"Alm:{n}")[1], "MONITOR", n)
        for f in ("MFC", "O2", "CYL", "STN"):
            self.assertEqual(rows.pop(P + f"Cfg:Active:{f}")[1], "MONITOR", f)
        self.assertEqual(rows, {}, "unexpected extra PVs")


if __name__ == "__main__":
    unittest.main()
