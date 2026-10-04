# Harness incident log

Rule: **every failure that reached CI, a scheduled run, or a human without being caught earlier ends with four things**:
1. the fix,
2. a sensor or guide that would have caught it (a `repo_lint` rule, an architecture fitness test, a guide-lint check,
   a hook, a skill),
3. the sensor rolled out to the whole fleet (not just the repo that broke),
4. a row here. A row with no guard is an open gap and must say so.

Sensors are prompts: a failing check has to say WHAT broke, WHY the rule exists and the exact FIX, so an agent can
self-correct from the message. If an incident needed a human to explain the failure, the message is not good enough yet.

| Date | Symptom | Root cause | Fix | Guard | Status |
|---|---|---|---|---|---|
| 2026-10-04 | `docker-publish` red on main ("installation does not exist") in inventory-storage, facility-layout | image pushed to `ghcr.io/claudioed/...`; the repos moved to the IQVO org | namespace PRs; release develop to main | `repo_lint` R4 owner drift (.github, charts, docs config, terraform, helm-values) | guarded |
| 2026-10-04 | `npm_and_yarn in /web` Dependabot job red weekly in 9 repos | `web/` depends on `file:../../warehouse-ui-kit`, outside the repo; Dependabot cannot fetch it | dropped the `/web` npm entry (alerts fixed with scoped overrides) | `repo_lint` R3 (npm entry with an outside path dependency) | guarded |
| 2026-10-04 | warehouse-planning CI red on every branch | scaffolded from the template: `web` job ran in `warehouse-planning/web` (no such dir); docs job needed a docs site that did not exist | removed the web job, GHCR publish, docs site PR | `repo_lint` R1 (workflow `working-directory` / `cache-dependency-path` must exist, checkout-path aware) and R5 | guarded |
| 2026-09-15 | harness-template `develop` CI red for weeks | the template's own ci.yml ran against unfilled `{{PLACEHOLDER}}` values | `selftest.yml` (hooks, scripts, fitness tests, idempotent migration) | template self-test + `repo_lint` R5 | guarded |
| 2026-10-04 | docs sitemap/canonical URLs on a host that no longer serves them | `docusaurus.config.ts` kept `claudioed.github.io` after the org move; generated API docs embed the raw URL, so the config change also needs `clean-api-docs` + `gen-api-docs` (the generator caches) | config PRs per repo, regenerated docs | `repo_lint` R4 (docs config). Gotcha recorded: always `clean-api-docs` before `gen-api-docs` | guarded |
| 2026-09-28 | weekly `E2E Behaviour` red | the godog client sent no `Idempotency-Key`, which the fleet idempotency middleware now requires | client fix (e2e-tests PR 28) | NONE: a sibling tightening a contract only shows up in the weekly cross-repo run | **open gap**: needs a contract check that runs when an API contract changes |
| 2026-09-28 | weekly mutation red in 3 repos | tests did not kill boundary mutants; gremlins maps a mutant to a test target by directory name = Go package name (`internal/domain/package` declares `package pack`) | tests (+ testable symmetry check), margin 90.32 to 95.24 percent | `harness-health` shows the scheduled-run state; `CommitInterval` and CloudEvents fitness tests added the same week | partially guarded: no check on the efficacy MARGIN. Verified fixed 2026-10-04: the full weekly CI (incl. the exhaustive `mutation` and `drift` jobs) is green on all three repos |
| 2026-10-04 | 3 repos' full-replay cache consumers committed offsets synchronously per message | `kafka-go` with a `GroupID` and no `CommitInterval` (a past order-management crash loop, never generalised) | `CommitInterval` set | fitness test `TestReplayConsumersSetCommitInterval` | guarded |
| 2026-10-04 | `@faker-js/faker` and `braces` keep `Dependabot Updates` red | no patched/resolvable version (an override to faker 10 breaks the docs build) | alerts dismissed with a recorded reason and revisit condition | none possible: a decision, recorded on each alert | accepted risk |
| 2026-10-04 | workforce-management `integration` flaked (`connection reset by peer` on topic creation right after the Kafka container reported ready) | test helper does not retry topic creation | re-ran the job | workforce-management helpers now retry dial+create (PR 131, TopicAlreadyExists on retry counts as success) | **open gap, fleet-wide**: the same fail-fast helper exists in ~25 test files across 9 repos; needs a managed helper or a fitness test that rejects bare `CreateTopics` in tests |
| 2026-10-04 | delegated agents stopped on "User denied this command" | chained shell commands (`a; b; c \| tail`) are auto-denied for background agents, which cannot re-ask | briefs now say: ONE simple command per terminal call | convention in every delegation brief | guarded by convention only |
| 2026-10-04 | `harness-health` showed scheduled RED for incidents that were already fixed | scheduled workflows only run weekly (Monday), so the last scheduled result stays red until then | dispatched `ci.yml` (workflow_dispatch) on the three repos to re-run the weekly jobs on demand: all green | process rule: after fixing a scheduled-run failure, dispatch the workflow to prove it instead of waiting for the next schedule | guarded by convention |
