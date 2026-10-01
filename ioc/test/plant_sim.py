"""caproto Channel Access server wrapping plantsim.plant.Plant for bench testing.

Serves the Alicat MFC PVs, the O2 analyzer PV and the World: control PVs for one or two
simulated plants (spec Sec 6.1; PV table in
.superpowers/sdd/2026-09-25-ioc-plan3-plant-simulator/task-3-brief.md) on localhost only, so a
real EPICS IOC (or a test harness standing in for one) can be pointed at a bench plant instead of
the real beamline hardware.

Usage:
    python plant_sim.py [--plant ALICAT_PREFIX,O2_PV,WORLD_PREFIX ...] [--seed N] [--no-noise]
                         [--second] [--port 5066] [--o2-drop F] [--o2-jitter S] [--o2-seed N]

--o2-drop / --o2-jitter (2026-10-01): the served O2 PV skips that fraction of its 1 s updates
(posts nothing), or posts them up to S s late, seeded (class O2Feed); <W>O2Skipped counts the skips.

Safety: the server binds to 127.0.0.1 only (never the beamline network) and refuses to start
(exit code 2) if:
  * any configured prefix looks like a beamline PV (starts with "15ID"),
  * EPICS_CAS_INTF_ADDR_LIST is set to anything other than 127.0.0.1,
  * the CA beacon address list does not resolve to 127.0.0.1 alone (caproto 1.3.0 reads
    EPICS_CAS_BEACON_ADDR_LIST and EPICS_CAS_AUTO_BEACON_ADDR_LIST for this -- see
    CAPROTO_NOTES; unconfined, a beacon is a UDP broadcast onto whatever network this PC is on,
    which may carry the real beamline control system),
  * two plants would serve the same PV name (a duplicate prefix/name would otherwise silently
    drop one plant's PVs when the per-plant pvdb dicts are merged), or
  * the TCP port actually bound is not the one requested (see CAPROTO_NOTES: caproto silently
    falls back to a random port if the requested one is busy, which would be a "some other
    process is already listening on our bench port" situation this simulator must not paper
    over).
EPICS_CAS_INTF_ADDR_LIST, EPICS_CAS_BEACON_ADDR_LIST and EPICS_CAS_AUTO_BEACON_ADDR_LIST are set
to their safe values here if unset; any other value is a refusal, not a silent override. See
CAPROTO_NOTES below for the two places this module's behaviour departs from what a naive reading
of the caproto docs would suggest.

CAPROTO_NOTES (caproto 1.3.0, verified empirically -- see task-3-report.md):
  * The env var that actually controls the server's listening TCP/UDP port is
    EPICS_CA_SERVER_PORT (caproto/server/common.py: Context.__init__ reads exactly that key, and
    _bind_tcp_sockets_with_consistent_port_number() tries it first via ca.random_ports(..,
    try_first=self.ca_server_port)). EPICS_CAS_SERVER_PORT is defined in caproto's environment
    defaults but is never read anywhere in the package. We set both, but only the former has any
    effect. Similarly, the beacon (periodic UDP "I'm alive" broadcast) address list is controlled
    by EPICS_CAS_BEACON_ADDR_LIST / EPICS_CAS_AUTO_BEACON_ADDR_LIST, read by
    caproto.get_beacon_address_list() (caproto/_utils.py) and used directly in
    caproto/asyncio/server.py Context.run() (`for address in ca.get_beacon_address_list():`) --
    gating only EPICS_CAS_INTF_ADDR_LIST (which confines the TCP/UDP *listener*) is not enough;
    with a clean environment the beacon list defaults to [('255.255.255.255', 5065)], a broadcast
    onto whatever network this PC's interfaces reach.
  * pvproperty.putter callbacks do not "additionally" store the put value: the framework's
    ChannelData.write() calls verify_value(), which calls the putter and stores/publishes
    *whatever the putter returns*. So every putter below must `return` the value it wants shown
    on that PV (e.g. Setpoint's putter returns the quantised sp_val, not the raw put value) --
    it must not call `await instance.write(...)` on itself (that would recurse back into
    verify_value). Writes from the periodic loop, by contrast, target a *different* PV instance
    than any putter and use `verify_value=False` so they do not re-invoke a putter and are not
    themselves subject to alarm-forcing by a stale putter identity.
"""

import argparse
import asyncio
import math
import os
import random
import socket
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from plantsim.plant import DT, Plant, default_plant_params  # noqa: E402

from caproto import AlarmSeverity, AlarmStatus, ChannelType, get_beacon_address_list  # noqa: E402
from caproto.asyncio.server import Context  # noqa: E402
from caproto.server import PVGroup, pvproperty  # noqa: E402

# ---------------------------------------------------------------------------------------------
# Enum string tables shared by the Alicat Gas_RBV / World:Gas PVs and the other enum PVs.
#
# The real Alicat gas table (ipApp/Db/Alicat_BC.db, mbbi) is not vendored into this repo -- only
# "index 7 = He" is specified (task-3-brief.md; docs/ioc/15LSS_sample_gas_IOC_spec.md Sec 6.1).
# This list is therefore a placeholder that satisfies that one constraint (16 states, index 7 =
# 'He'); the other 15 names are a reasonable guess at the standard Alicat table and are NOT
# sourced from the deployed db. Nothing in this simulator or its tests depends on any entry other
# than index 7.
GAS_STATES = [
    'Air', 'Ar', 'CO2', 'CO', 'C2H6', 'H2', 'N2', 'He',
    'N2O', 'Ne', 'O2', 'C3H8', 'n-C4H10', 'C2H2', 'C2H4', 'i-C4H10',
]
RUNNING_STATES = ['Paused', 'Running']
HOLD_STATES = ['current', 'open', 'stuck']
LIDTYPE_STATES = ['A', 'B']
MODE_STATES = ['normal', 'invalid', 'frozen']
NOISE_STATES = ['Off', 'On']

DEFAULT_PLANT_1 = ('SIM:Alicat1:', 'SIM:O2', 'SIM:World:')
DEFAULT_PLANT_2 = ('SIM:Alicat2:', 'SIM:O2b', 'SIM:World2:')


# ---------------------------------------------------------------------------------------------
# Validation helpers for put() callbacks (final-fix-brief I-2, M-3). caproto's own docstring for
# ChannelData.verify_value says "To reject a value, raise an exception" -- raising here (instead
# of storing a bad value) makes caproto return a put error to the client and leaves the plant
# untouched (see ChannelData.write, _data.py: on an exception from the putter it sets a WRITE/
# MAJOR alarm and re-raises *before* the new value is ever stored).

def _finite(value):
    """Coerce to float and reject NaN/inf (I-2).

    A non-finite value stored into plant state eventually reaches `math.floor()` in
    `Plant.poll_alicat()` (called every tick from the periodic loop) or a division in
    `Plant.preset()`/`steady_at()`, which raises and -- before this fix -- took the whole server
    down with it. Rejecting at the putter, before any plant state is touched, is cheaper and
    keeps the error where the bad value came from.
    """
    v = float(value)
    if not math.isfinite(v):
        raise ValueError(f'value must be finite, got {value!r}')
    return v


def _validate_enum(value, states, pv_label):
    """Reject any put that is not one of `states` (M-3).

    caproto's ChannelEnum.verify_value (caproto/_data.py) resolves a valid put -- whether the
    client sent the numeric index or the string label -- to the enum_strings label *before*
    calling the putter (see the mk_put_lidtype docstring below). On an *invalid* put (an
    out-of-range index, the wrong type, or a string that is not in enum_strings) its lookup
    raises IndexError/TypeError internally, which it catches and swallows, passing the raw,
    unresolved value through to the putter unchanged instead of rejecting it. Without this
    check a putter would silently accept that raw value (e.g. `plant.lid_type = str(5)` -> the
    nonsense state `'5'`) instead of caproto returning a put error.
    """
    if not isinstance(value, str) or value not in states:
        raise ValueError(f'{pv_label}: {value!r} is not one of {states} (index or label)')
    return value


# ---------------------------------------------------------------------------------------------
# put() callback factories. Each returns an async (group, instance, value) -> new_value
# function, per the caproto putter contract described above.

async def _count_alicat_put(group, what, value):
    """Count a put to one of the Alicat's writable PVs (<W>AlicatPuts) and log it, so a test can
    prove "no put" without depending on when its own CA monitor connected (a put made in the
    instant a client connects is otherwise indistinguishable from the connection value)."""
    group.alicat_puts += 1
    print(f'plant_sim.py: {time.strftime("%H:%M:%S")} put {what} = {value} '
          f'(Alicat put #{group.alicat_puts})', file=sys.stderr, flush=True)
    await group.AlicatPuts.write(group.alicat_puts, verify_value=False)


def mk_put_setpoint(plant):
    async def _put(group, instance, value):
        value = _finite(value)
        plant.put_setpoint(value)
        await _count_alicat_put(group, 'Setpoint', value)
        return plant.a['sp_val']  # VAL always becomes sp_val, even on hold
    return _put


def mk_put_ramp(plant, alicat=True):
    """alicat=False for <W>SetRamp: the same ramp, set by the world, not a put to the Alicat."""
    async def _put(group, instance, value):
        value = _finite(value)
        if value < 0:
            raise ValueError(f'ramp rate must be >= 0 SLPM/s (0 = unlimited), got {value!r}')
        plant.put_ramp(value)
        if alicat:
            await _count_alicat_put(group, 'RampRate', value)
        return value
    return _put


def mk_put_run(plant):
    async def _put(group, instance, value):
        plant.put_run()
        await _count_alicat_put(group, 'Run', value)
        return value
    return _put


def mk_action(action):
    async def _put(group, instance, value):
        # Spec: "put 1 -> action" -- a put of any other value (including the 0 the record
        # settles back to, or a client re-sending the last value) must not re-fire it.
        if int(value) == 1:
            action()
        return value
    return _put


def mk_put_lidtype(plant):
    async def _put(group, instance, value):
        # caproto's ChannelEnum resolves an incoming put (whether the client sent the numeric
        # index or the string label) to the enum_strings label *before* calling the putter, and
        # that resolution has already happened by the time verify_value() reaches us here -- see
        # CAPROTO_NOTES at the top of this file. `value` is therefore already one of
        # LIDTYPE_STATES on a *valid* put; _validate_enum (M-3) rejects anything else (e.g. an
        # out-of-range index that caproto's own lookup silently let through unresolved).
        value = _validate_enum(value, LIDTYPE_STATES, 'LidType')
        plant.lid_type = value
        return value
    return _put


def mk_put_hold(plant):
    async def _put(group, instance, value):
        value = _validate_enum(value, HOLD_STATES, 'Hold')
        plant.hold(value)
        return value
    return _put


def mk_put_gas(plant):
    async def _put(group, instance, value):
        value = _validate_enum(value, GAS_STATES, 'Gas')
        plant.a['gas'] = value
        return value
    return _put


def mk_put_cylpressure(plant):
    async def _put(group, instance, value):
        value = _finite(value)
        if value < 0:
            raise ValueError(f'cylinder pressure must be >= 0 psi, got {value!r}')
        plant.cyl_p = value
        return value
    return _put


def mk_put_mode(plant):
    async def _put(group, instance, value):
        value = _validate_enum(value, MODE_STATES, 'AnalyzerMode')
        plant.an['mode'] = value
        return value
    return _put


def mk_put_preset(plant):
    async def _put(group, instance, value):
        value = _finite(value)
        if value <= 0:
            raise ValueError(f'Preset flow must be > 0 SLPM, got {value!r}')
        plant.preset(value)
        return value
    return _put


def mk_put_seed(plant):
    async def _put(group, instance, value):
        plant._rng = random.Random(int(value))
        plant._gauss_cache = None
        return value
    return _put


def mk_put_noise(plant):
    async def _put(group, instance, value):
        value = _validate_enum(value, NOISE_STATES, 'Noise')
        defaults = default_plant_params()
        on = value == 'On'
        plant.pp['noise'] = defaults['noise'] if on else 0
        plant.pp['wanderRel'] = defaults['wanderRel'] if on else 0
        return value
    return _put


# ---------------------------------------------------------------------------------------------
# Dynamic PVGroup construction: one subclass per plant, with every pvproperty's `name=` set to
# the fully-qualified PV name (group prefix is left '' so PVGroup does not prepend anything --
# see server.py PVSpec.get_instantiation_info: full_pvname = group.prefix + name).

def build_plant_group(idx, a_prefix, o2_name, w_prefix, plant, noise_on, run_loop=None):
    # Initial values come from the plant's own state: 0 for a fresh plant (as before), the
    # preset's values after --preset, so a client connecting before the first 1 Hz publish never
    # sees a stopped Alicat that is in fact flowing.
    r = plant.rbv
    attrs = {
        'alicat_puts': 0,       # puts to Setpoint / RampRate / Run so far (<W>AlicatPuts)
        # ---- Alicat MFC (spec Sec 6.1 / Alicat_BC.db names)
        'Setpoint': pvproperty(
            value=float(plant.a['sp_val']), dtype=float, precision=3, units='SLPM',
            name=a_prefix + 'Setpoint', alarm_group=a_prefix + 'Setpoint', put=mk_put_setpoint(plant)),
        'Setpoint_RBV': pvproperty(
            value=float(r['sp']), dtype=float, read_only=True, name=a_prefix + 'Setpoint_RBV'),
        'Flow_RBV': pvproperty(
            value=float(r['flow']), dtype=float, precision=2, units='SLPM', read_only=True,
            name=a_prefix + 'Flow_RBV'),
        'Total_RBV': pvproperty(
            value=float(r['total']), dtype=float, read_only=True, name=a_prefix + 'Total_RBV'),
        'Running_RBV': pvproperty(
            value=1, dtype=ChannelType.ENUM, enum_strings=RUNNING_STATES, read_only=True,
            name=a_prefix + 'Running_RBV'),
        'Status': pvproperty(
            # DBR_STRING like the real stringin (a plain `str` dtype is served as a char
            # array, which the IOC's SNL `string` channel reads as its first character only)
            value='', dtype=ChannelType.STRING, read_only=True, name=a_prefix + 'Status'),
        'RampRate': pvproperty(
            value=float(plant.a['ramp']), dtype=float,
            name=a_prefix + 'RampRate', alarm_group=a_prefix + 'RampRate', put=mk_put_ramp(plant)),
        'RampRate_RBV': pvproperty(
            value=float(plant.a['ramp']), dtype=float, read_only=True,
            name=a_prefix + 'RampRate_RBV'),
        'Run': pvproperty(
            value=0, dtype=int, name=a_prefix + 'Run', alarm_group=a_prefix + 'Run', put=mk_put_run(plant)),
        'Gas_RBV': pvproperty(
            value=GAS_STATES.index(plant.a['gas']), dtype=ChannelType.ENUM,
            enum_strings=GAS_STATES, read_only=True, name=a_prefix + 'Gas_RBV'),
        'FlowUnits_RBV': pvproperty(
            value='SLPM', dtype=ChannelType.STRING, read_only=True,
            name=a_prefix + 'FlowUnits_RBV'),

        # ---- O2 analyzer (single PV, not a prefix group)
        #
        # alarm_group is set to a name unique to this PV: PVGroup.alarms is a
        # defaultdict(ChannelAlarm) keyed by alarm_group (server.py PVSpec.get_instantiation_info:
        # `alarm = group.alarms[self.alarm_group]`), so every pvproperty in this dict that leaves
        # alarm_group at its default (None) shares ONE ChannelAlarm. ChannelAlarm.write()
        # publishes to every channel attached to it (_data.py ChannelAlarm.publish loops over
        # self._channels), so writing O2's alarm status/severity every tick was re-publishing
        # every OTHER PV in this group too -- verified empirically: it doubled the monitor
        # update rate on Flow_RBV (see task-3-report.md). Giving O2 its own alarm_group keeps its
        # alarm state (and the extra publish that comes with setting it) from touching any PV
        # that never has its own alarm changed.
        'O2': pvproperty(
            value=float(plant.an['value']), dtype=float, precision=4, units='%',
            read_only=True, name=o2_name, alarm_group=f'o2_{idx}'),

        # ---- World: control (test / bench-only actions)
        'LiftLid': pvproperty(
            value=0, dtype=int, name=w_prefix + 'LiftLid', alarm_group=w_prefix + 'LiftLid', put=mk_action(plant.lift_lid)),
        'CloseLid': pvproperty(
            value=0, dtype=int, name=w_prefix + 'CloseLid', alarm_group=w_prefix + 'CloseLid', put=mk_action(plant.close_lid)),
        'CrackLid': pvproperty(
            value=0, dtype=int, name=w_prefix + 'CrackLid', alarm_group=w_prefix + 'CrackLid', put=mk_action(plant.crack_lid)),
        'Reseat': pvproperty(
            value=0, dtype=int, name=w_prefix + 'Reseat', alarm_group=w_prefix + 'Reseat', put=mk_action(plant.reseat)),
        'ClearHold': pvproperty(
            value=0, dtype=int, name=w_prefix + 'ClearHold', alarm_group=w_prefix + 'ClearHold', put=mk_action(plant.clear_hold)),
        'BreathDip': pvproperty(
            value=0, dtype=int, name=w_prefix + 'BreathDip', alarm_group=w_prefix + 'BreathDip', put=mk_action(plant.breath_dip)),
        'HandlingDip': pvproperty(
            value=0, dtype=int, name=w_prefix + 'HandlingDip', alarm_group=w_prefix + 'HandlingDip', put=mk_action(plant.handling_dip)),
        'LidType': pvproperty(
            value=LIDTYPE_STATES.index(plant.lid_type), dtype=ChannelType.ENUM,
            enum_strings=LIDTYPE_STATES, name=w_prefix + 'LidType', alarm_group=w_prefix + 'LidType', put=mk_put_lidtype(plant)),
        'Hold': pvproperty(
            value=0, dtype=ChannelType.ENUM, enum_strings=HOLD_STATES,
            name=w_prefix + 'Hold', alarm_group=w_prefix + 'Hold', put=mk_put_hold(plant)),
        'SetRamp': pvproperty(
            value=float(plant.a['ramp']), dtype=float,
            name=w_prefix + 'SetRamp', alarm_group=w_prefix + 'SetRamp', put=mk_put_ramp(plant, alicat=False)),
        'Gas': pvproperty(
            value=GAS_STATES.index(plant.a['gas']), dtype=ChannelType.ENUM,
            enum_strings=GAS_STATES, name=w_prefix + 'Gas', alarm_group=w_prefix + 'Gas', put=mk_put_gas(plant)),
        'CylPressure': pvproperty(
            value=float(plant.cyl_p), dtype=float,
            name=w_prefix + 'CylPressure', alarm_group=w_prefix + 'CylPressure', put=mk_put_cylpressure(plant)),
        'AnalyzerMode': pvproperty(
            value=MODE_STATES.index(plant.an['mode']), dtype=ChannelType.ENUM,
            enum_strings=MODE_STATES, name=w_prefix + 'AnalyzerMode', alarm_group=w_prefix + 'AnalyzerMode', put=mk_put_mode(plant)),
        'Preset': pvproperty(
            value=0.0, dtype=float, name=w_prefix + 'Preset', alarm_group=w_prefix + 'Preset', put=mk_put_preset(plant)),
        'Seed': pvproperty(
            value=0, dtype=int, name=w_prefix + 'Seed', alarm_group=w_prefix + 'Seed', put=mk_put_seed(plant)),
        'Noise': pvproperty(
            value=(1 if noise_on else 0), dtype=ChannelType.ENUM, enum_strings=NOISE_STATES,
            name=w_prefix + 'Noise', alarm_group=w_prefix + 'Noise', put=mk_put_noise(plant)),
        'Bulk': pvproperty(
            value=float(plant.C), dtype=float, read_only=True, name=w_prefix + 'Bulk'),
        'AlicatPuts': pvproperty(
            value=0, dtype=int, read_only=True, name=w_prefix + 'AlicatPuts',
            alarm_group=w_prefix + 'AlicatPuts'),
        'O2Skipped': pvproperty(        # O2 updates skipped by --o2-drop so far
            value=0, dtype=int, read_only=True, name=w_prefix + 'O2Skipped',
            alarm_group=w_prefix + 'O2Skipped'),
        'Time': pvproperty(
            value=0.0, dtype=float, read_only=True, name=w_prefix + 'Time', startup=run_loop),
    }
    cls = type(f'PlantGroup{idx}', (PVGroup,), attrs)
    return cls(prefix='')


def _plant_pv_refs(group):
    """Collect the ChannelData instances the periodic loop needs to write, keyed by role."""
    return {
        'setpoint_rbv': group.Setpoint_RBV,
        'flow_rbv': group.Flow_RBV,
        'total_rbv': group.Total_RBV,
        'running_rbv': group.Running_RBV,
        'status': group.Status,
        'ramprate_rbv': group.RampRate_RBV,
        'gas_rbv': group.Gas_RBV,
        'flowunits_rbv': group.FlowUnits_RBV,
        'o2': group.O2,
        'o2skipped': group.O2Skipped,
        'cylpressure': group.CylPressure,
        'bulk': group.Bulk,
        'time': group.Time,
    }


class O2Feed:
    """Missed and late O2 updates (the user, 2026-10-01: "building our system to handle missed
    updates on a ~1Hz schedule is good for robustness"). For each 1 s analyzer update, decide()
    returns None to skip it (the served O2 PV posts nothing, so a client sees the previous value
    and time stamp, as when the real analyzer's update is missed), or the delay in seconds after
    the publish instant at which to post it. drop = the fraction of updates skipped; jitter = the
    largest delay (uniform in [0, jitter)). jitter < 1 s keeps the updates in order. Its own seeded
    RNG: the plant's random stream is the same with or without it. Both draws are made for every
    update, so the drops of a seed do not depend on the jitter setting."""

    def __init__(self, drop=0.0, jitter=0.0, seed=None):
        if not (0 <= drop < 1):
            raise ValueError(f'--o2-drop must be in [0, 1), got {drop!r}')
        if not (0 <= jitter < 1):
            raise ValueError(f'--o2-jitter must be in [0, 1) s, got {jitter!r}')
        self.drop, self.jitter = drop, jitter
        self._rng = random.Random(seed)
        self.skipped = 0
        self.posted = 0

    @property
    def active(self):
        return self.drop > 0 or self.jitter > 0

    def decide(self):
        u = self._rng.random()
        d = self._rng.random() * self.jitter
        if u < self.drop:
            self.skipped += 1
            return None
        self.posted += 1
        return d


async def _write_o2(pv, value, sevr):
    if sevr == 3:
        await pv.write(value, status=AlarmStatus.UDF, severity=AlarmSeverity.INVALID_ALARM,
                       verify_value=False)
    else:
        await pv.write(value, status=AlarmStatus.NO_ALARM, severity=AlarmSeverity.NO_ALARM,
                       verify_value=False)


async def _write_o2_later(pv, value, sevr, delay):
    await asyncio.sleep(delay)
    await _write_o2(pv, value, sevr)


async def _publish(ctx):
    """1 Hz readback: sample the analyzer, poll the Alicat, and write every readback PV."""
    plant = ctx['plant']
    pv = ctx['pv']
    plant.sample_analyzer()
    plant.poll_alicat()
    r = plant.rbv

    await pv['setpoint_rbv'].write(r['sp'], verify_value=False)
    await pv['flow_rbv'].write(r['flow'], verify_value=False)
    await pv['total_rbv'].write(r['total'], verify_value=False)
    await pv['running_rbv'].write(1 if r['running'] else 0, verify_value=False)
    await pv['status'].write('' if r['running'] else 'HLD', verify_value=False)
    await pv['ramprate_rbv'].write(r['ramp'], verify_value=False)
    gas_idx = GAS_STATES.index(r['gas']) if r['gas'] in GAS_STATES else GAS_STATES.index('He')
    await pv['gas_rbv'].write(gas_idx, verify_value=False)
    await pv['flowunits_rbv'].write(r['units'], verify_value=False)

    an = plant.an
    feed = ctx.get('o2feed')
    delay = feed.decide() if feed is not None and feed.active else 0.0
    if delay is None:                         # a missed update: post nothing
        await pv['o2skipped'].write(feed.skipped, verify_value=False)
    elif delay <= 0:
        await _write_o2(pv['o2'], an['value'], an['sevr'])
    else:                                     # a late update: the value measured now, posted later
        task = asyncio.get_running_loop().create_task(
            _write_o2_later(pv['o2'], an['value'], an['sevr'], delay))
        ctx.setdefault('o2tasks', set()).add(task)
        task.add_done_callback(ctx['o2tasks'].discard)

    await pv['cylpressure'].write(plant.cyl_p, verify_value=False)
    await pv['bulk'].write(plant.C, verify_value=False)
    await pv['time'].write(plant.t, verify_value=False)


# ---------------------------------------------------------------------------------------------
# _run_loop timing helpers (final-fix-brief I-1, M-2, and the "catch-up ticks" minor item).
# Pulled out as pure functions so the schedule/cadence math is unit-testable without spinning up
# an actual asyncio loop or CA server.

def _compute_start_mono(now_wall, now_mono, phase):
    """monotonic-clock origin for _run_loop's tick schedule (ticks fire at start_mono + k * DT).

    I-1: the *publish* tick (see _is_publish_tick -- the 4th physics step of each whole-second
    group, k = 3, 7, 11, ...) must land at wall-clock whole_second + phase, not at the whole
    second itself. Fix round 1 aligned k = 0 to a whole wall-clock second, which put the publish
    tick (then wrongly k = 0, 4, 8, ... -- see _is_publish_tick) at the *same instant* an IOC's
    own 1 Hz tick samples its inputs (spec Sec 8.1): the IOC would then randomly see this
    second's or the previous second's readback, a jitter (~15 ms) + CA latency race. Landing the
    publish `phase` seconds into the second instead gives the IOC's on-the-second sample a fixed
    margin before the next update.
    """
    next_whole_second = math.ceil(now_wall)
    first_publish_wall = next_whole_second + phase
    return now_mono + (first_publish_wall - now_wall) - 3 * DT


def _is_publish_tick(k):
    """True on the tick that completes the 4th physics step of a group.

    Matches the reference cadence exactly (Station.step, simulator/sample_gas_simulator.html
    lines 1534-1541: `if (this.steps % STEPS_PER_S === 0) { this.plant.sampleAnalyzer();
    this.plant.pollAlicat(); ... }`, STEPS_PER_S == 4, this.steps 1-indexed) -- i.e. sample after
    physics step count 4, 8, 12, ..., not 1, 5, 9 (M-2: fix round 1 sampled one step too early
    in every group of four). `k` is the 0-indexed tick counter in _run_loop; the step count
    taken so far, after this tick's plant.step() call, is k + 1.
    """
    return (k + 1) % 4 == 0


def _catchup_lag_warning(lag):
    """Message to print when a catch-up tick (whose scheduled time has already passed, so
    `_run_loop` does not sleep before it) is more than 1 s behind schedule, or None if the lag
    is within tolerance.

    Plant time must stay locked to wall time (a catch-up tick is never skipped), but a lag this
    large means the loop cannot keep up with the 0.25 s tick rate, which is worth a loud warning
    rather than silently falling further behind.
    """
    if lag > 1.0:
        return (f'plant_sim.py: warning: catch-up tick running {lag:.3f} s behind schedule '
                '(loop cannot keep up with the 0.25 s tick rate)')
    return None


# ---------------------------------------------------------------------------------------------
# Safety gates (spec: beamline PV/hardware approval rules -- never serve a 15ID* name, never
# bind off localhost).

def _check_port_not_in_use(port):
    """I-3: refuse to start if something is already listening on 127.0.0.1:port.

    caproto sets SO_REUSEADDR on its listening TCP socket, and on this platform that lets a
    *second* process bind a LISTENING socket to a port a first process is already listening on
    (README "Known Windows limitation"): both show LISTENING in netstat, no error on either
    side. That means the busy-port fallback this server already checks for (see _serve/
    _startup below: caproto silently picks a random port if `bind()` itself fails) never
    triggers here -- Windows never reports the port as busy in the first place. A plain TCP
    connect probe does not depend on that fallback: if something answers on 127.0.0.1:port
    before we even try to bind, it does not matter whether the OS considers the port free.
    """
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.5):
            pass
    except OSError:
        return  # nobody answered -- nothing is listening on this port yet
    print(f"plant_sim.py: refusing to start: another CA server already listens on "
          f"127.0.0.1:{port}", file=sys.stderr)
    sys.exit(2)


def _check_prefixes_not_beamline(plant_specs):
    for a_prefix, o2_name, w_prefix in plant_specs:
        for name in (a_prefix, o2_name, w_prefix):
            if name.upper().startswith('15ID'):
                print(f"plant_sim.py: refusing to start: {name!r} looks like a beamline PV "
                      "(starts with 15ID)", file=sys.stderr)
                sys.exit(2)


def _check_and_set_intf_addr_list():
    val = os.environ.get('EPICS_CAS_INTF_ADDR_LIST')
    if val is None:
        os.environ['EPICS_CAS_INTF_ADDR_LIST'] = '127.0.0.1'
    elif val != '127.0.0.1':
        print(f"plant_sim.py: refusing to start: EPICS_CAS_INTF_ADDR_LIST={val!r}, "
              "must be 127.0.0.1", file=sys.stderr)
        sys.exit(2)


def _check_and_set_beacon_addr_list():
    """Confine the CA beacon (periodic UDP broadcast) the same way the listener is confined.

    EPICS_CAS_INTF_ADDR_LIST alone does not stop caproto from beaconing onto the network: see
    CAPROTO_NOTES above. Both variables below are read by caproto.get_beacon_address_list(),
    checked again with _assert_beacon_confined() right before the server actually starts.
    """
    val = os.environ.get('EPICS_CAS_BEACON_ADDR_LIST')
    if val is None:
        os.environ['EPICS_CAS_BEACON_ADDR_LIST'] = '127.0.0.1'
    elif val != '127.0.0.1':
        print(f"plant_sim.py: refusing to start: EPICS_CAS_BEACON_ADDR_LIST={val!r}, "
              "must be 127.0.0.1", file=sys.stderr)
        sys.exit(2)

    auto_val = os.environ.get('EPICS_CAS_AUTO_BEACON_ADDR_LIST')
    if auto_val is None:
        os.environ['EPICS_CAS_AUTO_BEACON_ADDR_LIST'] = 'NO'
    elif auto_val.strip().upper() != 'NO':
        print(f"plant_sim.py: refusing to start: EPICS_CAS_AUTO_BEACON_ADDR_LIST={auto_val!r}, "
              "must be NO (otherwise caproto beacons to 255.255.255.255 in addition to any "
              "manually-specified address)", file=sys.stderr)
        sys.exit(2)


def _assert_beacon_confined():
    """Belt-and-suspenders: ask caproto itself what it resolved the beacon list to."""
    entries = get_beacon_address_list()
    bad = [addr for addr, port in entries if addr != '127.0.0.1']
    if bad:
        print(f"plant_sim.py: refusing to start: beacon address list resolves to {entries!r}, "
              "not confined to 127.0.0.1 (check EPICS_CAS_BEACON_ADDR_LIST / "
              "EPICS_CAS_AUTO_BEACON_ADDR_LIST)", file=sys.stderr)
        sys.exit(2)


def _check_no_duplicate_pv_names(groups):
    """Refuse if two plants would serve the same PV name.

    main() merges each plant's group.pvdb into one dict with plain dict.update(), which would
    otherwise silently let a later plant's PV overwrite an earlier plant's PV of the same name
    (e.g. two --plant configs sharing an O2 name) with no error at all.
    """
    seen_by = {}
    for idx, group in enumerate(groups):
        for name in group.pvdb:
            if name in seen_by:
                print(f"plant_sim.py: refusing to start: PV {name!r} would be served by both "
                      f"plant {seen_by[name]} and plant {idx} -- check for a duplicate prefix "
                      "or O2 name across --plant / --second configs", file=sys.stderr)
                sys.exit(2)
            seen_by[name] = idx


# ---------------------------------------------------------------------------------------------

def parse_plant_arg(s):
    parts = s.split(',')
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            f"--plant expects ALICAT_PREFIX,O2_PV,WORLD_PREFIX, got {s!r}")
    return tuple(parts)


def build_arg_parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plant', action='append', type=parse_plant_arg, default=None,
                    help='ALICAT_PREFIX,O2_PV,WORLD_PREFIX (repeatable)')
    p.add_argument('--second', action='store_true',
                    help=f'also serve a second plant at {DEFAULT_PLANT_2}')
    p.add_argument('--seed', type=int, default=None)
    p.add_argument('--no-noise', action='store_true')
    p.add_argument('--port', type=int, default=5066)
    p.add_argument('--phase', type=float, default=0.75,
                    help='publish (sample_analyzer/poll_alicat/PV writes) at whole wall-clock '
                         'second + this many seconds, not on the second itself (default 0.75; '
                         'see README "Timing")')
    p.add_argument('--o2-drop', type=float, default=0.0, metavar='FRACTION',
                    help='skip this fraction of the 1 s O2 updates (the O2 PV posts nothing), '
                         'seeded (--o2-seed, default from --seed); 0 = off')
    p.add_argument('--o2-jitter', type=float, default=0.0, metavar='S',
                    help='post each O2 update up to this many seconds (< 1) after its publish '
                         'instant, uniformly at random; 0 = off')
    p.add_argument('--o2-seed', type=int, default=None,
                    help='seed of the --o2-drop / --o2-jitter draws (default: from --seed)')
    p.add_argument('--preset', type=float, default=None, metavar='SLPM',
                    help='start plant 0 already at steady state at this flow (as a put to '
                         '<W>Preset, but before the server serves anything, so a client that '
                         'connects first sees the preset values); must be > 0')
    return p


def o2_feed_seed(args, idx):
    """The O2Feed seed of plant idx: --o2-seed, else derived from --seed, else 0 (always seeded)."""
    base = args.o2_seed if args.o2_seed is not None else (
        1000 + args.seed if args.seed is not None else 0)
    return base + idx


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    try:
        O2Feed(args.o2_drop, args.o2_jitter)   # validate before anything starts
    except ValueError as e:
        print(f'plant_sim.py: refusing to start: {e}', file=sys.stderr)
        sys.exit(2)

    plant_specs = list(args.plant) if args.plant else [DEFAULT_PLANT_1]
    if args.second:
        plant_specs.append(DEFAULT_PLANT_2)

    _check_prefixes_not_beamline(plant_specs)
    _check_port_not_in_use(args.port)
    _check_and_set_intf_addr_list()
    _check_and_set_beacon_addr_list()

    # EPICS_CA_SERVER_PORT is what caproto's Context actually reads for the listening port (see
    # CAPROTO_NOTES above); EPICS_CAS_SERVER_PORT is set too, matching the brief, but has no
    # effect in caproto 1.3.0.
    os.environ['EPICS_CA_SERVER_PORT'] = str(args.port)
    os.environ['EPICS_CAS_SERVER_PORT'] = str(args.port)

    contexts = []

    async def _run_loop(group, instance, async_lib):
        # Drift-compensated 0.25 s tick; start_mono is aligned so the publish tick (4th physics
        # step of each group, _is_publish_tick) lands at whole wall-clock second + args.phase
        # (I-1), not at the whole second itself.
        now_wall = time.time()
        now_mono = time.monotonic()
        start_mono = _compute_start_mono(now_wall, now_mono, args.phase)
        k = 0
        while True:
            target = start_mono + k * DT
            delay = target - time.monotonic()
            if delay > 0:
                await async_lib.library.sleep(delay)
            else:
                # Catch-up tick: already late, so don't sleep for `delay` (negative) -- but
                # still yield control once so a burst of catch-up ticks cannot starve the
                # asyncio loop's handling of incoming CA requests. Plant time stays locked to
                # wall time (never permanently skipped); a lag this large just gets logged.
                warning = _catchup_lag_warning(-delay)
                if warning is not None:
                    print(warning, file=sys.stderr)
                await async_lib.library.sleep(0)
            try:
                for ctx in contexts:
                    plant = ctx['plant']
                    plant.t = k * DT
                    plant.step()
                if _is_publish_tick(k):
                    for ctx in contexts:
                        await _publish(ctx)
            except Exception:
                # I-2: an unhandled exception here (e.g. a plant state that a putter should
                # have rejected but didn't) must not leave a process that looks alive in
                # netstat/Task Manager but has silently stopped publishing. Fail loudly and
                # immediately instead.
                print('plant_sim.py: FATAL: unhandled exception in the plant tick/publish '
                      'loop -- exiting', file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                sys.stderr.flush()
                os._exit(3)
            k += 1

    groups = []
    for idx, (a_prefix, o2_name, w_prefix) in enumerate(plant_specs):
        seed = None if args.seed is None else args.seed + idx
        plant = Plant(seed=seed, noise=not args.no_noise)
        if idx == 0 and args.preset is not None:
            if not (math.isfinite(args.preset) and args.preset > 0):
                print(f'plant_sim.py: refusing to start: --preset must be > 0 SLPM, got {args.preset!r}',
                      file=sys.stderr)
                sys.exit(2)
            plant.preset(args.preset)
            print(f'plant_sim.py: plant 0 preset at {args.preset} SLPM', file=sys.stderr)
        run_loop = _run_loop if idx == 0 else None
        group = build_plant_group(idx, a_prefix, o2_name, w_prefix, plant,
                                   noise_on=not args.no_noise, run_loop=run_loop)
        groups.append(group)
        contexts.append({'plant': plant, 'pv': _plant_pv_refs(group),
                         'o2feed': O2Feed(args.o2_drop, args.o2_jitter, o2_feed_seed(args, idx))})
        print(f'plant_sim.py: plant {idx}: Alicat={a_prefix!r} O2={o2_name!r} '
              f'World={w_prefix!r}', file=sys.stderr)
        if args.o2_drop or args.o2_jitter:
            print(f'plant_sim.py: plant {idx}: O2 updates: drop {args.o2_drop}, jitter '
                  f'{args.o2_jitter} s, seed {o2_feed_seed(args, idx)}', file=sys.stderr)

    _check_no_duplicate_pv_names(groups)

    pvdb = {}
    for group in groups:
        pvdb.update(group.pvdb)

    # Belt-and-suspenders re-check right before opening any socket: _check_and_set_beacon_addr_list
    # only validates the two env vars; this asks caproto itself what it actually resolved them to.
    _assert_beacon_confined()

    asyncio.run(_serve(pvdb, args.port))


async def _serve(pvdb, requested_port):
    """Equivalent to caproto.asyncio.server.run(), but keeps a handle on the Context so the
    actual bound port can be checked and logged (caproto.server.run() does not expose it)."""
    server_ctx = Context(pvdb, interfaces=['127.0.0.1'])

    async def _startup(async_lib):
        if server_ctx.port != requested_port:
            print(f"plant_sim.py: refusing to start: bound TCP port is {server_ctx.port}, not "
                  f"the requested {requested_port}. caproto silently falls back to a random "
                  "port when the requested one is busy (ca.random_ports(..., "
                  f"try_first={requested_port})) -- something else is probably already "
                  "listening on it.", file=sys.stderr)
            sys.stderr.flush()
            os._exit(2)   # hard exit: sockets are already open, no cleanup to preserve
        print(f'plant_sim.py: serving {len(pvdb)} PVs on 127.0.0.1:{server_ctx.port}',
              file=sys.stderr)

    await server_ctx.run(startup_hook=_startup)


if __name__ == '__main__':
    main()
