# o2-purge

IOC-resident helium purge with O2 feedback for the 15IDC sample enclosure, and the measured
enclosure physics it is designed on.

| Path | What |
|---|---|
| `docs/superpowers/specs/2026-09-24-o2-purge-feedback-design.md` | Controller design. Section 2.1 holds the measured model and lid-detection margins. |
| `analysis/` | Archiver-data analysis behind section 2.1: raw CSVs, scripts, figures. `python analysis/run_all.py` regenerates everything. See `analysis/README.md`. |

No controller code exists yet. **No tool may write the live `15IDC:*` PVs** until the user lifts that rule (spec section 2).
