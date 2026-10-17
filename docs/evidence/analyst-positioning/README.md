# Analyst × positioning — live evidence (2026-10-17, worktree server :5094)

* `run_01..06_*.json` — full `/api/agent/analysis` responses (force=true) from `ap_run.py`
  (curl-equivalent). `_wall_s` = client wall time.
* `store_summary.json` — all 9 logged analyses (6 above + 3 started with the card's Analyze
  button), with per-run positioning-factor counts and cited groups.
* `live_positioning_block_*.json` — `analyst_positioning.positioning_section` applied to a live
  `/api/positioning?watch=0` response (the exact block Hermes sees).
* `card_*.png`, `positioning_section_*.png` — headless Chrome (`--headless=new`, own profile,
  CDP 9567), 1600×1100, the `#analyst-panel` card with the Positioning section open.
