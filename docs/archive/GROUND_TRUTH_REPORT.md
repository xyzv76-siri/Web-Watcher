# Ground Truth Acceptance Report

## Baseline
- Commit: 66313a4
- Branch: master (clean, up to date with origin/master)
- Tests: 1471 passed (1458 existing + 13 new acceptance tests)

## Modified Files
- `src/web_watcher/scheduled_runner.py` — 8 lines changed (bug fixes)
- `tests/test_ground_truth_acceptance.py` — new file, 13 tests
- `GROUND_TRUTH_PLAN.md` — new file
- `TODO.md` — updated

## 13 Scenarios Results

| # | Scenario | Result | Notes |
|---|----------|--------|-------|
| 1 | Single rule executes | PASS | Basic execution verified |
| 2 | Include tag filtering | PASS | Only pricing rule executed |
| 3 | Exclude tag filtering | PASS | News rule excluded |
| 4 | Disabled registry rule | PASS | Rule not executed, registry_filtered=1 |
| 5 | Tag + registry combined | PASS | Both filters stack correctly |
| 6 | Priority ordering | PASS | High priority executed first |
| 7 | Priority + claim | PASS | Both rules claimed and executed in priority order |
| 8 | YAML modified auto reload | PASS | File change detected and reloaded |
| 9 | YAML new rule appears | PASS | New rule visible after reload |
| 10 | YAML delete rule disappears | PASS | Deleted rule removed after reload |
| 11 | Reload preserves registry | PASS | Disabled/priority/group kept after YAML reload |
| 12 | Daemon continuous | PASS | Registry changes take effect immediately |
| 13 | CLI reload semantics | PASS | CLI reload and auto reload consistent |

## Bugs Fixed During Acceptance

### Bug 1: Tag/Registry filtering did not affect execution
**Root cause:** Tag filtering and registry filtering only modified `_rule_cache`, but `claimed` targets came from `repo.claim_targets()` independently. Filtered-out targets were still claimed and executed.

**Fix:** After claiming, filter `claimed` against `_rule_cache`. Release leases for filtered-out targets. Sort remaining targets by registry priority before execution.

### Bug 2: Registry default-enabled semantics missing
**Root cause:** `get_enabled_rules()` returned only rules with explicit registry entries and `enabled=1`. New rules without registry entries were not filtered, but disabled rules without any enabled rules in registry would bypass filtering entirely.

**Fix:** Use `list_rules()` to build registry map. Rules without registry entry default to enabled. Only explicitly disabled rules are filtered out.

## Architecture Confirmed

Execution order is now:
1. Hot Reload (if rules.yaml changed)
2. sync_rules()
3. Tag filtering → updates _rule_cache
4. Registry filtering → updates _rule_cache, sorts by priority
5. Claim targets from repo
6. Filter claimed targets against _rule_cache (release leases for filtered-out)
7. Sort claimed targets by priority
8. Execute adapters in priority order

## Source-of-Truth Boundary

- **YAML rules.yaml** — Rule definition source of truth
- **SQLite Registry** — Runtime management state (enabled/disabled/priority/group)
- **ScheduledRunner._rule_cache** — Ephemeral execution view (merged from YAML + Registry)
- **repo.targets** — Claim/fencing state

No cross-contamination during reload or execution.
