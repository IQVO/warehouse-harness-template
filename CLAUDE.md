# CLAUDE.md — warehouse-harness-template

`warehouse-harness-template` is the fleet's versioned agent-harness scaffold (GitHub org `IQVO`). It is a
TEMPLATE, not a runnable service: no cmd/, no apis/, no domain code except the opt-in GS1 package
(`internal/domain/shared/gs1/`). It owns three things: the files every bounded-context repo starts from
(`scripts/new-service.sh`), the template-MANAGED files every repo keeps byte-identical (rolled out by
`tools/migrate_v3.py` + `tools/migrate_v4.py`), and the fleet's harness sensors (`scripts/harness/`).
`HARNESS.md` describes every guide and sensor; `SPEC.md` is the longer design note.

## Hard rules

1. **Template-managed files are edited HERE first, never in a service.** The lists are `MANAGED` and
   `GS1_PACKAGE` in `tools/migrate_v3.py` and `V4_MANAGED` in `tools/migrate_v4.py` (hooks, lint scripts,
   runtime settings, `.claude/agents/`, the managed fitness tests, `INCIDENTS.md`). The weekly sync copies
   them byte-for-byte; a service-side edit is drift and is overwritten.
2. **Never hand-edit fleet rules.** `.claude/rules/fleet/` is a copy of warehouse-docs agents/fleet (and
   agents/retailnetwork); edit warehouse-docs first, then sync. `scripts/harness/fleet_drift.py` fails a copy
   that differs.
3. **`INCIDENTS.md` is the fleet-shared log.** Add a row here first (fix, guard, fleet rollout, status);
   every repo carries a byte-identical copy via `V4_MANAGED`.
4. **Every sensor speaks WHAT / WHY / FIX** and has a red-then-green test, including a case named after the
   incident that motivated it. A sensor that runs in this repo either passes here or has a template mode
   (detected by `.github/workflows/ci.yml.template` existing, like `repo_lint` R5/R12 and `guide_lint`).
5. **Template-only files never reach a scaffold.** `scripts/new-service.sh` activates `ci.yml.template`,
   deletes `.github/workflows/selftest.yml`, renders `CLAUDE.md.template` to the service's CLAUDE.md (plus the
   AGENTS.md -> CLAUDE.md symlink) and consumes the template; `scripts/scaffold-selftest.sh` asserts all of it.
6. **CloudEvents 1.0 stays mandatory and there is no auth layer** in anything this template generates
   (`templates/cloudevents/`, `TestNoAuthMiddlewareReintroduced`). Never add an envelope toggle or auth.
7. **GitFlow**: feature branch -> PR into develop, squash-merge only when `selftest` is green; never push
   develop/main, never `--no-verify`.

## The gate (selftest, not make check-all)

There is no cmd/, so `make check-all` is not this repo's gate: `.github/workflows/selftest.yml` is. It runs:

```bash
python3 scripts/harness/test_hook.py
python3 scripts/harness/test_repo_lint.py && python3 scripts/harness/repo_lint.py
python3 scripts/harness/guide_lint.py && python3 scripts/harness/test_guide_lint.py
python3 scripts/harness/test_fleet_drift.py
python3 tools/test_migrate_gs1.py && go test ./internal/domain/shared/gs1/ -race -count=1
python3 tools/test_harness_sync.py      # scoped-rules block (both profiles, migrate_v4), health drift lists
python3 -m py_compile scripts/harness/*.py scripts/*.py tools/*.py
go vet ./internal/architecture/ && go test ./internal/architecture/
bash scripts/scaffold-selftest.sh      # new-service.sh output; sensors red then green
```

## Adding or changing a sensor

1. `repo_lint` rule: add `R<N>` to `scripts/harness/repo_lint.py` (docstring list too), with red and green
   cases in `scripts/harness/test_repo_lint.py`; give it a template mode if this repo cannot satisfy it.
2. Fitness test: add it under `internal/architecture/` with a `...SensorBites` test on fixture trees; if every
   service must carry it byte-identical, add it to `MANAGED`.
3. Wire it into `.github/workflows/selftest.yml` (and `.github/workflows/ci.yml.template` for services),
   describe it in `HARNESS.md`, and add the incident row to `INCIDENTS.md`.
4. Prove a generator change red-first: restore ONLY the generator from HEAD and run
   `bash scripts/scaffold-selftest.sh`.
5. A new managed sensor reaches existing services only through their own migration PR (`tools/migrate_v3.py
   --repo <svc>` then `tools/migrate_v4.py --repo <svc>`, both idempotent, `--dry-run` first). Land the files
   a new rule demands fleet-wide BEFORE rolling the rule out, or the sync goes red everywhere.
6. A new repo is added to `DEFAULT_REPOS` in `scripts/harness-audit.py` when it is created.

## Where things are written down

- `HARNESS.md`: every guide and sensor, its cost and lifecycle position; "Instantiating this template";
  "Versioning". `INCIDENTS.md`: the fleet incident log. `EVALS.md`: measured harness claims.
- `CLAUDE.md.template`: the guide every scaffold starts with. `.claude/rules/*.md` and `.claude/skills/`:
  skeletons a service fills in. `.claude/memory/MEMORY.md`: seeded once per repo, then the repo's own.
- `scripts/harness-audit.py` / `scripts/harness-health.py`: fleet coverage and drift reports.

harness-template: v4

<!-- harness:scoped-rules:start (generated by tools/migrate_v3.py in warehouse-harness-template; do not hand-edit) -->
## Scoped rules and harness

Claude Code loads each rule below automatically when you touch the matching paths. OpenCode and Codex do NOT: read the rule BEFORE editing matching files.

| When touching | Read |
|---|---|
| `internal/adapters/**/kafka/**`, `internal/adapters/outbound/events/**`, `apis/asyncapi*` | `.claude/rules/integration-events.md` |
| `internal/adapters/inbound/http/**`, `apis/openapi*.yaml`, `apis/openapi/**` | `.claude/rules/rest-api.md` |

Hooks (`scripts/harness/hook.py`, wired for Claude Code, Codex and OpenCode) block pushes to develop/main, `--no-verify`, bare `rm -rf`, and edits to generated files, and feed gofmt/vet findings back after each edit. Before saying "done" run `make check-fast`; the full gate is `make check-all`. `HARNESS_OFF=1` disables the hooks when debugging the harness itself.
<!-- harness:scoped-rules:end -->

In this repository the generated line above means the services' gate; here the gate is `selftest` (above).
