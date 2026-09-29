# SPEC.md — warehouse-harness-template

(Intended as CLAUDE.md, but that filename is protected in this
environment and requires the user's own manual creation/edit — see this
repo's own harness lesson about the protected-agent-filename write guard.
Please rename this file to CLAUDE.md, and symlink AGENTS.md -> CLAUDE.md
to match the fleet's convention, at your convenience.)

This repo is `harness-template: v1` for the warehouse-systems fleet's Go
bounded-context services. It is a TEMPLATE, not a runnable service: no
domain code, no `cmd/`, no `apis/openapi.yaml`.

## What's here

- `Makefile`, `.github/workflows/ci.yml`, `lefthook.yml`, `.golangci.yml`,
  `.gremlins.yaml` — the canonical local+CI quality gate, with
  `{{SERVICE}}` / `{{SERVICE_REPO}}` / `{{RICHEST_AGGREGATE}}` placeholders.
- `internal/architecture/architecture_test.go` + `fitness_test.go` — arch-go
  fitness tests. These COMPILE AND PASS as-is against this empty tree
  (there's nothing to violate yet) — `go test ./internal/architecture/...
  -v` is the fastest way to confirm you haven't broken the template.
- `.claude/rules/*.md` — skeleton guide files (domain model, REST API,
  integration events) with `<!-- fill in -->` placeholders, not fabricated
  content.
- `.claude/skills/how-to-*.md`, `.claude/commands/*.md` — reusable how-tos
  and review sensors, ported from `inventory-storage` (the fleet's richest
  reference harness) with a template note asking the instantiator to adapt
  every example to their own real code.
- `scripts/coverage-quality.py` — the drift job's "high line-coverage but
  a surviving mutant" detector, verbatim from inventory-storage (this
  script's logic is package-path-agnostic).
- `scripts/new-service.sh` — instantiation script. See its header comment.
- `HARNESS.md` — the manifest: every sensor's purpose/cost/lifecycle
  position, and the real incident behind every fitness test.

## Working in THIS repo (the template itself, not an instantiated copy)

- Before changing any sensor's config (`.golangci.yml`, `.gremlins.yaml`
  thresholds, CI job shape), check whether the change should also flow
  back into the 9 already-instantiated repos, or whether it's template-v2
  material. This repo has no `harness-audit` conformance checker yet
  (Task 6.2 of the harness-coverage-expansion plan) to catch that drift
  automatically — until it exists, changes here need a manual fan-out
  decision.
- `go build ./... && go vet ./... && gofmt -l .` should stay clean at all
  times — this repo has zero domain code, so there's no excuse for either
  to ever fail.
- `go test ./internal/architecture/... -v` should PASS on every commit —
  if it starts failing against this repo's own (nonexistent) domain code,
  something in the fitness tests' assumptions broke.

## When bumping the template version

Increment `harness-template: v<N>` (referenced in `HARNESS.md`'s
Versioning section and meant to be recorded in every instantiated repo's
own AGENTS.md) whenever a change here isn't purely additive/optional —
e.g. tightening a threshold, adding a new blocking CI job, changing a
placeholder's name. Purely-additive changes (a new advisory sensor, a new
`.claude/skills/` guide) don't need a version bump.
