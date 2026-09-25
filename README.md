# helium-flow-fb

IOC-resident helium purge with O2 feedback for the sample enclosure at APS 15-ID (ChemMatCARS), the
measured enclosure physics it is designed on, and a graphical simulator of the controller.

| Path | What |
|---|---|
| `simulator/sample_gas_simulator.html` | Self-contained simulator of the controller and plant. Open it in a browser; **? Help → Self-test** runs 19 scenarios. Tag `sim-v1.0` is the approved reference behaviour. |
| `docs/simulator/` | User guide, illustrated tour, and a guide for agents building control simulators. |
| `docs/superpowers/specs/2026-09-24-o2-purge-feedback-design.md` | Controller design. Section 2.1 holds the measured model and lid-detection margins. |
| `docs/ioc/15LSS_sample_gas_IOC_spec.md` | Implementation specification for the EPICS IOC `15LSS_sample_gas` (epid + SNL). |
| `analysis/` | Archiver-data analysis behind section 2.1: raw CSVs, scripts, figures. `python analysis/run_all.py` regenerates everything. See `analysis/README.md`. |
| `tools/` | PDF rendering, Help-page embedding and screenshot scripts for the docs. |

The IOC is specified but not yet implemented. **No tool may write the live `15IDC:*` PVs** until
the user lifts that rule (IOC spec §2.1); bench work uses `SIM:` prefixes on localhost only.

MIT licensed; see `LICENSE`.
