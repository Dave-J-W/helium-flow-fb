#!/usr/bin/env node
// Reference open-loop exporter for the Python port of the Plant class (Plan 3, task 1).
//
// Drives the reference simulator's own Plant class (never modified) directly -- no Controller,
// no Station -- through six named cases, and records what a Python port must reproduce to within
// 0.5 % relative. See ioc/test/test_plant_model.py (run_case) for the Python side that must match
// this second-by-second driving loop exactly.
//
// Usage: node plant_openloop.js --html <simulator.html> --out <file>

'use strict';
const fs = require('fs');
const path = require('path');
const loadReference = require('./load_reference.js');

// ---------------------------------------------------------------------------------------------- arguments
const args = { html: null, out: null };
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i], v = process.argv[i + 1];
  if (a === '--html') { args.html = v; i++; }
  else if (a === '--out') { args.out = v; i++; }
  else die(`unknown argument ${a}`);
}
if (!args.html) die('missing --html <path>');
if (!args.out) die('missing --out <file>');
function die(msg) { process.stderr.write(`plant_openloop: ${msg}\n`); process.exit(2); }

// ---------------------------------------------------------------------------------------------- reference
const { Plant, defaultPlantParams, DT, STEPS_PER_S } = loadReference(path.resolve(args.html));

// The plant-only half of Station.prototype.presetRegulating (simulator/sample_gas_simulator.html:1585-1592):
// start already at steady state for a given flow, with the Alicat already running at that flow.
function presetPlant(plant, F) {
  plant.setO2Everywhere(plant.steadyAt(F));
  Object.assign(plant.a, { spVal: F, spDev: F, spRamped: F, flow: F, running: true });
  plant.dEff = plant.pp.delayMax;
  plant.pollAlicat();
}

function newPlant(setup) {
  const pp = defaultPlantParams();
  pp.noise = 0; pp.wanderRel = 0;
  const plant = new Plant(pp);
  if (setup) setup(plant);
  return plant;
}

// ---------------------------------------------------------------------------------------------- cases
const CASES = {
  purge_from_air: {
    n: 900,
    setup: null,   // lid closed, air start (Plant.reset() default)
    actions: { 5: p => p.putSetpoint(20) },
  },
  hold_normal_lid: {
    n: 3600,
    setup: p => presetPlant(p, 0.25),
    actions: {},
  },
  lid_lift: {
    n: 600,
    setup: p => presetPlant(p, 0.25),
    actions: {
      120: p => p.liftLid(),
      300: p => p.closeLid(),
    },
  },
  collimator: {
    n: 1800,
    setup: p => { p.lidType = 'B'; presetPlant(p, 0.84); },
    actions: { 600: p => p.putSetpoint(1.2) },
  },
  alicat_semantics: {
    n: 400,
    setup: p => presetPlant(p, 0.29),
    actions: {
      60: p => p.hold('open'),
      70: p => p.putSetpoint(0.5),     // held: must not reach the device
      90: p => p.putRun(),
      120: p => p.putSetpoint(0.5),
      200: p => p.putRamp(0),
      210: p => p.putSetpoint(2),
      300: p => { p.cylP = 140; },
      310: p => p.putSetpoint(20),
    },
  },
  analyzer_modes: {
    n: 200,
    setup: p => presetPlant(p, 0.25),
    actions: {
      50: p => { p.an.mode = 'invalid'; },
      100: p => { p.an.mode = 'normal'; },
      150: p => { p.an.mode = 'frozen'; },
    },
  },
};

// ---------------------------------------------------------------------------------------------- run
if (Math.round(1 / DT) !== STEPS_PER_S) die(`DT ${DT} and STEPS_PER_S ${STEPS_PER_S} disagree`);

function runCase(name, def) {
  const plant = newPlant(def.setup);
  const records = [];
  for (let sec = 1; sec <= def.n; sec++) {
    const act = def.actions[sec];
    if (act) act(plant);
    for (let k = 1; k <= STEPS_PER_S; k++) {
      plant.t = (sec - 1) + k * DT;
      plant.step(DT);
    }
    plant.sampleAnalyzer();
    plant.pollAlicat();
    records.push({
      t: sec, o2: plant.an.value, sevr: plant.an.sevr, C: plant.C,
      flow: plant.rbv.flow, sp: plant.rbv.sp, total: plant.rbv.total, running: plant.rbv.running,
    });
  }
  return records;
}

const out = {};
for (const [name, def] of Object.entries(CASES)) out[name] = runCase(name, def);
fs.mkdirSync(path.dirname(path.resolve(args.out)), { recursive: true });
fs.writeFileSync(args.out, JSON.stringify(out));
console.log(`plant_openloop: wrote ${Object.keys(out).length} cases to ${args.out}`);
