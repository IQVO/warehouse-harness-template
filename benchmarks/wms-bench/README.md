# WMS-Bench: a fleet-owned benchmark for harness changes (harness-template v4)

Google-Research's Harness² is evaluated on three professional benchmarks (JobBench, LAB, WorkBuddy-Bench)
across harness configurations. EVALS.md already does the same KIND of thing for this fleet (full-vs-bare
ablations, objective gates + transcript scanning) but as one-off rounds: different repos, different task
sets, n=1-3 per cell, scored by hand each time. That was honest about its own limits ("one repo, one model,
small n") and found real things anyway (guides stop the rare destructive shortcut; a sentence in a skill is
followed about half the time). What it never became is a FIXED, repeatable suite you can point at
harness-template v3 vs v4 vs v5 and get a comparable number.

WMS-Bench is that fixed suite: a small, versioned set of representative tasks pulled from this fleet's own
INCIDENTS.md, one per task SHAPE (not per repo), runnable against any repo/executor/harness-version
combination with the SAME gates every time. It reuses `tools/harness_eval2.py`'s scoring engine --
this is a manifest + runner wrapper, not new scoring code.

## Layout

    benchmarks/wms-bench/
      README.md          this file
      tasks/*.yaml        one task per file: repo, prompt, gate function, source incident
      run.py              thin wrapper: reads a tasks/*.yaml, shells out to harness_eval2.py / harness_probe.py

## Task manifest format (tasks/*.yaml)

    id: stock-event                      # must match a key in harness_eval2.PROMPTS or harness_eval.TASKS
    repo: inventory-storage               # which fleet repo this task runs against
    kind: feature                        # feature | temptation | incident-response
    source_incident: null                 # INCIDENTS.md date, if this task recreates a past incident
    why: >
      Representative "add an integration event" task. Round 2/3 of EVALS.md ran this exact shape and found
      the catalogue/context-map convention followed about half the time regardless of guides -- this is the
      task that question needs re-running against every future harness version.

## Running it

    python3 benchmarks/wms-bench/run.py --task tasks/stock-event.yaml --variants full,bare --runs 3

which is a direct pass-through to:

    python3 tools/harness_eval2.py --repo <resolved repo path> --tasks stock-event --variants full,bare --runs 3 \\
        --out benchmarks/wms-bench/results/stock-event-<harness-version>.jsonl

`run.py` only adds: resolving `repo:` to an absolute checkout path, and naming the output file after the
CURRENT harness-template version (read from the target repo's version marker -- see migrate_v4.py) so
`benchmarks/wms-bench/results/*.jsonl` accumulates one file per (task, version) and `report.py` can diff
v3 vs v4 directly instead of someone remembering which run was which.

## What's seeded vs what's still manual

Seeded now: 3 tasks (`tasks/stock-event.yaml`, `tasks/push-develop.yaml`, `tasks/package-diverted-event.yaml`)
covering one of each kind, reusing prompts/gates that already exist in harness_eval.py / harness_eval2.py so
day one is a real run, not a stub. NOT built yet, intentionally left as follow-up once v4 has a few real
results to compare: a `report.py` that renders the v3-vs-v4 table (today, aggregate by hand the same way
EVALS.md's rounds did -- the JSONL schema is identical, `harness_eval2.report()` already renders one
version's table), and more tasks per bounded context (REST endpoint, flaky-test repair) -- add a `tasks/*.yaml`
file per new task, no runner changes needed.
