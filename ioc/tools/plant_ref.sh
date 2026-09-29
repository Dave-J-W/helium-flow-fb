#!/bin/bash
set -e
cd "$(dirname "$0")/.."
mkdir -p test/golden
node test/ref/plant_openloop.js --html ../simulator/sample_gas_simulator.html --out test/golden/plant_openloop.json
