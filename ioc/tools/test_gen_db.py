"""test_gen_db.py: verify sg_pvs.py / gen_db.py against the spec.

Run: python -m unittest -v test_gen_db   (from ioc/tools; stdlib unittest, no pytest, no new
packages; use the project's dedicated venv interpreter, not a bare "python" on PATH).
"""
import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
SPEC_PATH = os.path.join(REPO_ROOT, "docs", "ioc", "15LSS_sample_gas_IOC_spec.md")

sys.path.insert(0, HERE)
import sg_pvs  # noqa: E402
import gen_db  # noqa: E402

with open(SPEC_PATH, encoding="utf-8") as _f:
    SPEC_LINES = _f.read().splitlines()


def _section(tag):
    """Return the raw lines of spec section `tag` (e.g. "7.1"), up to the next "### " or "## "."""
    start = next(i for i, l in enumerate(SPEC_LINES) if l.startswith(f"### {tag} "))
    end = len(SPEC_LINES)
    for i in range(start + 1, len(SPEC_LINES)):
        if SPEC_LINES[i].startswith("### ") or SPEC_LINES[i].startswith("## "):
            end = i
            break
    return SPEC_LINES[start:end]


# ---------------------------------------------------------------------------
# §9.1 parser: ground truth for PARAMS.
# ---------------------------------------------------------------------------

def parse_9_1():
    lines = _section("9.1")
    rows = []
    for l in lines:
        if not l.startswith("| "):
            continue
        if l.startswith("| key"):
            continue
        cells = [c.strip() for c in l.strip().strip("|").split("|")]
        if len(cells) != 7:
            continue
        key, default, unit, mn, mx, level, meaning = cells
        norm = lambda s: s.replace("\u2212", "-")
        rows.append({
            "key": key,
            "default": float(norm(default)),
            "unit": unit,
            "min": float(norm(mn)),
            "max": float(norm(mx)),
            "level": level,
        })
    return rows


# ---------------------------------------------------------------------------
# §13.3 parser: ground truth for the nine Deep-admin groups.
# ---------------------------------------------------------------------------

def parse_13_3_groups():
    lines = _section("13.3")
    groups = {}
    current = None
    for l in lines:
        # the nine groups are nested one level ("  - **Name:** ...") under the "nine labelled
        # groups" bullet; top-level bullets like "- **Linked PV names**" or "- **Buttons:**"
        # (no leading indent) are siblings, not groups, and must not match here.
        m = re.match(r"^ {2}-\s+\*\*([^*]+):\*\*\s*(.*)$", l)
        if m:
            current = m.group(1)
            groups[current] = []
            rest = m.group(2)
            groups[current].extend(k.strip() for k in rest.split(",") if k.strip())
            continue
        if current is not None and re.match(r"^ {4,}\S", l):
            groups[current].extend(k.strip() for k in l.strip().split(",") if k.strip())
            continue
        current = None
    return groups


# ---------------------------------------------------------------------------
# PV-name parser for §7.1/7.3/7.5/7.6/7.7/7.8/7.9 (P2-R3 ruling): backtick tokens, optionally
# prefixed "$(P)", expanded against <W> in {3d,2d,1d,12h,6h}; ":x" and " / x" continuations
# combine with the nearest preceding fully-qualified name; §7.6's bare tokens (no "$(P)", no
# colon) take a fixed "Diag:" prefix. Prose-only backticks (format strings, lowercase reference
# keys, external PVs like Total_RBV) never match FULLNAME_RE and are ignored.
# ---------------------------------------------------------------------------

FULLNAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]*(?::[A-Za-z][A-Za-z0-9]*)*$")
_WLIST = ["3d", "2d", "1d", "12h", "6h"]


def expand_line(line, fixed_prefix=None):
    names = []
    base = ""
    pos = 0
    for m in re.finditer(r"`([^`]+)`", line):
        token = m.group(1)
        delim = line[pos:m.start()]
        pos = m.end()
        had_p = token.startswith("$(P)")
        core = token[4:] if had_p else token
        if "<W>" in core:
            for w in _WLIST:
                cand = core.replace("<W>", w)
                if FULLNAME_RE.match(cand):
                    names.append(cand)
                    base = cand[: cand.rfind(":") + 1] if ":" in cand else cand + ":"
            continue
        if core.startswith(":"):
            if base:
                names.append(base + core[1:])
            continue
        if delim.strip() == "/" and base and FULLNAME_RE.match(core):
            names.append(base + core)
            continue
        if FULLNAME_RE.match(core):
            if not had_p and ":" not in core and fixed_prefix:
                name = fixed_prefix + core
            else:
                name = core
            names.append(name)
            base = name[: name.rfind(":") + 1] if ":" in name else name + ":"
            continue
        # else: not a PV-like token (format string, lowercase reference key, external PV) -> skip
    return names


def _logical_lines(raw_lines):
    """Join a §7.x table/bullet block into logical lines: a >=2-space-indented line that isn't
    itself a new "- " / "| " item continues the previous logical line."""
    out = []
    for raw in raw_lines:
        if raw.strip() == "":
            continue
        if raw.startswith("  ") and out and not raw.lstrip().startswith(("-", "|", "#")):
            out[-1] = out[-1] + " " + raw.strip()
        else:
            out.append(raw.strip())
    return out


def names_in_section(tag, fixed_prefix=None, two_col=False):
    names = set()
    for line in _logical_lines(_section(tag)):
        if line.startswith("| ") and not line.startswith("|---"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            cols = cells[:2] if two_col else cells[:1]
            for c in cols:
                names.update(expand_line(c, fixed_prefix))
        else:
            names.update(expand_line(line, fixed_prefix))
    return names


def spec_pv_names():
    names = set()
    names |= names_in_section("7.1")
    names |= names_in_section("7.3")
    alm = names_in_section("7.5")
    for n in list(alm):
        if n.startswith("Alm:") and ":" not in n[len("Alm:"):]:
            alm.add(n + ":Msg")
    names |= alm
    names |= names_in_section("7.6", fixed_prefix="Diag:")
    names |= names_in_section("7.7", two_col=True)
    names |= names_in_section("7.8")
    names |= names_in_section("7.9")
    return names


RECORD_RE = re.compile(r'record\(([A-Za-z_]+),\s*"\$\(P\)([^"]+)"\)\s*\{([^}]*)\}', re.S)
FIELD_RE = re.compile(r'field\(([A-Za-z0-9_]+),\s*"((?:[^"\\]|\\.)*)"\)')


def db_pv_names_from_text(text):
    """Every "$(P)<suffix>" record name in a sampleGas.db-shaped text (in memory: never reads or
    writes the committed file, so this can't be fooled by a stale copy -- fix round 1, finding
    2)."""
    return {m.group(2) for m in RECORD_RE.finditer(text)}


class TestParams(unittest.TestCase):
    def test_param_count_matches_spec(self):
        spec_rows = parse_9_1()
        self.assertEqual(len(sg_pvs.PARAMS), len(spec_rows))

    # Defaults deliberately different from the spec table, with the decision that changed them.
    # writeEnable: the user, 2026-09-28 ("the failure state is not doing anything"): production
    # starts able to act; the PC trial forces shadow at start itself (FORCE_SHADOW=1).
    DEFAULT_OVERRIDES = {"writeEnable": 1.0}

    def test_param_fields_match_spec(self):
        spec_rows = {r["key"]: r for r in parse_9_1()}
        self.assertEqual(set(spec_rows), {p["key"] for p in sg_pvs.PARAMS})
        for p in sg_pvs.PARAMS:
            row = spec_rows[p["key"]]
            with self.subTest(key=p["key"]):
                self.assertAlmostEqual(p["default"],
                                       self.DEFAULT_OVERRIDES.get(p["key"], row["default"]))
                self.assertAlmostEqual(p["min"], row["min"])
                self.assertAlmostEqual(p["max"], row["max"])
                self.assertEqual(p["level"], row["level"])


def parse_9_2():
    """§9.2 mode-slot limits: {field: (unit, min, max)}."""
    rows = {}
    for l in _section("9.2"):
        if not l.startswith("| ") or l.startswith("| field"):
            continue
        cells = [c.strip() for c in l.strip().strip("|").split("|")]
        if len(cells) != 4:
            continue
        norm = lambda s: s.replace("−", "-")
        rows[cells[0]] = (cells[1], float(norm(cells[2])), float(norm(cells[3])))
    return rows


class TestModeFields(unittest.TestCase):
    def test_mode_fields_match_spec_9_2(self):
        spec = parse_9_2()
        self.assertEqual(set(spec), {f["field"] for f in sg_pvs.MODE_FIELDS})
        for f in sg_pvs.MODE_FIELDS:
            with self.subTest(field=f["field"]):
                unit, mn, mx = spec[f["field"]]
                self.assertEqual(f["unit"], unit)
                self.assertAlmostEqual(f["min"], mn)
                self.assertAlmostEqual(f["max"], mx)

    def test_kp_must_be_negative(self):
        # §9.2 "KP must be negative" (D5, user decision 2026-09-29): KP = 0 would switch the
        # feedback off without an alarm, so the record's DRVH keeps it below zero.
        kp = next(f for f in sg_pvs.MODE_FIELDS if f["field"] == "KP")
        self.assertLess(kp["max"], 0)
        for x in sg_pvs.MODE_SLOTS:
            self.assertLessEqual(sg_pvs.MODE_DEFAULTS[x]["KP"], kp["max"])
        text = gen_db.build_db_text()
        for x in sg_pvs.MODE_SLOTS:
            m = re.search(r'record\(ao,\s*"\$\(P\)Mode:%s:KP"\)\s*\{([^}]*)\}' % x, text)
            self.assertIsNotNone(m, f"Mode:{x}:KP")
            fields = dict(FIELD_RE.findall(m.group(1)))
            self.assertLess(float(fields["DRVH"]), 0, f"Mode:{x}:KP DRVH")


def parse_7_5():
    """§7.5 alarm table: [(name after "Alm:", levels cell)] in table order."""
    rows = []
    for l in _section("7.5"):
        m = re.match(r"^\| `Alm:([A-Za-z0-9]+)` \| ([^|]+) \|", l)
        if m:
            rows.append((m.group(1), m.group(2).strip()))
    return rows


class TestAlarms(unittest.TestCase):
    def test_alarm_table_matches_spec_7_5(self):
        # order too: ALARM_TABLE follows enum sg_alarm (sgCore.h) and almNames (sgIoc.c)
        self.assertEqual([n for n, _ in parse_7_5()], [n for n, _ in sg_pvs.ALARM_TABLE])

    def test_new_cannot_act_alarms_are_major(self):
        # G2 (user decision 2026-09-29) and D3: shadow mode and wrong flow units are MAJOR
        levels = dict(parse_7_5())
        self.assertEqual(levels.get("Shadow"), "2")
        self.assertEqual(levels.get("Units"), "2")


class TestBanner(unittest.TestCase):
    def test_banner_holds_several_alarms(self):
        # D7 (conformance audit 2026-09-29): at SIZV 256 two or three alarm texts filled the
        # banner and the lower-severity ones were cut mid-text; spec 7.1 now says 2048.
        m = re.search(r'record\(lsi,\s*"\$\(P\)Sts:Banner"\)\s*\{([^}]*)\}', gen_db.build_db_text())
        self.assertIsNotNone(m)
        self.assertEqual(dict(FIELD_RE.findall(m.group(1))).get("SIZV"), "2048")
        row = next(l for l in _section("7.1") if l.startswith("| `$(P)Sts:Banner`"))
        self.assertIn("2048", row)


class TestDeepGroups(unittest.TestCase):
    def test_50_d_level_keys_in_exactly_one_group(self):
        d_keys = {p["key"] for p in sg_pvs.PARAMS if p["level"] == "D"}
        # 51 until the ramp clamps moved to Admin (2026-09-29); 50 with lidMinSamples (2026-09-30)
        self.assertEqual(len(d_keys), 50)

        spec_groups = parse_13_3_groups()
        self.assertEqual(set(spec_groups), set(sg_pvs.DEEP_GROUPS))

        # every D-level key appears in exactly one spec group
        seen = {}
        for group, keys in spec_groups.items():
            for k in keys:
                self.assertNotIn(k, seen, f"{k} listed in two groups: {seen.get(k)}, {group}")
                seen[k] = group
        self.assertEqual(set(seen), d_keys)

        # sg_pvs.py's own grouping (PARAMS[*]['group']) matches the spec groups
        for p in sg_pvs.PARAMS:
            if p["level"] == "D":
                self.assertEqual(p["group"], seen[p["key"]])


class TestPvCoverage(unittest.TestCase):
    def test_every_spec_pv_is_in_generated_db(self):
        # In-memory only (fix round 1, finding 2): build_db_text() is exactly what gen_db.py
        # would write, but this test never touches the committed sampleGas.db, so it can't mask
        # drift the way calling the writer first would.
        generated = db_pv_names_from_text(gen_db.build_db_text())
        required = spec_pv_names()
        missing = sorted(n for n in required if n not in generated)
        self.assertEqual(missing, [], f"PV names from the spec missing in sampleGas.db: {missing}")


class TestMacroLeaks(unittest.TestCase):
    def test_macros_other_than_p_only_in_cfg_default_vals(self):
        """§7.9/§5.4: MFC/O2/CYL/STN are macros substituted only into the initial VAL of
        Cfg:{MFC,O2,CYL,STN} and Cfg:Default:*; everywhere else (in particular any DESC) they
        must not appear as "$(...)" text. A DESC containing "$(CYL)" is expanded by dbLoadRecords
        at load time, which both breaks that rule and can push DESC over its 40-char limit for a
        long station-specific name, causing a load-time rejection (fix round 1, finding 1)."""
        text = gen_db.build_db_text()
        allowed_records = {f"Cfg:{f}" for f in ["MFC", "O2", "CYL", "STN"]} | \
            {f"Cfg:Default:{f}" for f in ["MFC", "O2", "CYL", "STN"]}
        offenders = []
        for rtyp, name, body in RECORD_RE.findall(text):
            for field, value in FIELD_RE.findall(body):
                if "$(" not in value:
                    continue
                if value.replace("$(P)", "") == value:
                    # contains some other "$(...)" macro
                    if field == "VAL" and name in allowed_records:
                        continue
                    offenders.append((name, field, value))
        self.assertEqual(offenders, [])


class TestCheckMode(unittest.TestCase):
    def test_check_exits_zero_when_up_to_date(self):
        # Fix round 1, finding 2: no "generate, then --check" here -- that can never see drift
        # between the committed files and sg_pvs.py, since it overwrites the committed files with
        # the very thing it is about to compare them to. This only checks the committed state.
        result = subprocess.run([sys.executable, os.path.join(HERE, "gen_db.py"), "--check"],
                                 cwd=HERE, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
