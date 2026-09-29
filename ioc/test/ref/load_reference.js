'use strict';
// Loads the reference simulator's core JS (Plant, Controller, Station, ...) straight out of
// simulator/sample_gas_simulator.html, never modified, so Python and JS can both be checked
// against the same source of truth.
//
// Same slicing rule as ioc/test/ref/make_traces.js (Plan 1, other branch): slice from the line
// starting "const clamp = " to before the next line containing "//  Charts". Kept as a separate
// file so the two plans do not touch the same file.

const fs = require('fs');
const vm = require('vm');

module.exports = function loadReference(htmlPath) {
  const html = fs.readFileSync(htmlPath, 'utf8').replace(/\r\n/g, '\n');
  const lines = html.split('\n');
  const iStart = lines.findIndex(l => l.startsWith('const clamp = '));
  if (iStart < 0) throw new Error(`marker "const clamp = " not found in ${htmlPath}`);
  const iEnd = lines.findIndex((l, i) => i > iStart && l.includes('//  Charts'));
  if (iEnd < 0) throw new Error(`marker "//  Charts" not found after "const clamp = " in ${htmlPath}`);
  const core = lines.slice(iStart, iEnd).join('\n');
  const wrapper = `(function () {\n${core}\n` +
    `return { Plant, defaultPlantParams, DT, STEPS_PER_S };\n})`;
  return vm.runInThisContext(wrapper, { filename: `${htmlPath}(core)` })();
};
