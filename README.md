# helium-flow-fb

IOC-resident helium purge with O2 feedback for the sample enclosure at APS 15-ID (ChemMatCARS), the
measured enclosure physics it is designed on, and a graphical simulator of the controller.

| Path | What |
|---|---|
| `simulator/sample_gas_simulator.html` | Self-contained simulator of the controller and plant. Open it in a browser; **? Help → Self-test** runs 19 scenarios. Tag `sim-v1.0` is the approved reference behaviour. |
| `docs/simulator/` | User guide, illustrated tour, and a guide for agents building control simulators. |
| `docs/superpowers/specs/2026-09-24-o2-purge-feedback-design.md` | Controller design. Section 2.1 holds the measured model and lid-detection margins. |
| `ioc/` | **The EPICS IOC `15LSS_sample_gas`**: C controller core, IOC glue, SNL program, database and Phoebus screens (generated from one PV table), a caproto plant simulator and the bench tests. Start with `ioc/README.md`. |
| `docs/ioc/INSTALL.md` | Installing and commissioning the IOC on the Linux soft-IOC host, with a shadow-mode first start. |
| `docs/ioc/OPERATOR_GUIDE.md` | The screens and everyday use, with screenshots. |
| `docs/ioc/15LSS_sample_gas_IOC_spec.md` | Implementation specification for the IOC (epid + SNL), with the user's decisions. |
| `analysis/` | Archiver-data analysis behind section 2.1: raw CSVs, scripts, figures. `python analysis/run_all.py` regenerates everything. See `analysis/README.md`. |
| `tools/` | PDF rendering, Help-page embedding and screenshot scripts for the docs. |

**Status (2026-09-29):** the IOC is implemented and tested on a Windows/MinGW bench against the
simulator. That includes a trace replay that matches the reference simulator exactly over 19
scenarios, and real-time scenario runs. It has run from a PC against the real enclosure,
read-only and in a supervised gas-off write test. Not yet done: a gas-on test and the install on
the beamline's Linux host (`docs/ioc/INSTALL.md`). Writes to the live `15IDC:*` PVs happen only
with the user's approval; bench work uses `SIM:` prefixes on localhost only.

MIT licensed; see `LICENSE`.
