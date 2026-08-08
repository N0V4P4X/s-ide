# bin — relay / plans graph tooling

Scripts that generate or maintain committed graph files. Stdlib Python only.

| Script | Output | What it does |
|---|---|---|
| `relay-graph.py` | `plans.graph.json` | Pure function of Vault front matter: every note in `20-projects/` + `70-tasks/` becomes a node (tier→category, status passthrough, stage carried), edges from `blocked-by:` (→`blocks`) and `projects:` wikilinks (→`schedules`). Run from the repo root; set `SIDE_VAULT` or pass `--vault` if your vault isn't `~/Documents/Vault`. |

`plans.graph.json` must never be hand-edited — it is regenerated on demand and the
Friday stand-up job runs it, so the plan graph refreshes on the same cadence as
everything else.
