# Idempotency replay classes — the fleet standard

What a service answers when a request is retried under the same `Idempotency-Key`, how that is
classified per problem type, and the reference test every repo copies to prove it. The rule text is
`.claude/rules/fleet/idempotency-and-outbox.md`; the test is
`templates/idempotency/replay_classes_test.go.tmpl` (verbatim, no driver import) plus the wiring
skeleton `templates/idempotency/replay_classes_wiring_test.go.tmpl` (the repo's own half). The fleet decision (owner, 2026-11-18)
supersedes the "every 4xx is stored and replayed" sentence of facility-layout ADR 0019.

## What

A retried request is answered from the store only when the first answer is **final for that
request**. Every problem type a service can answer on an idempotent route has exactly one class,
declared on its catalogue entry:

| Class | Meaning | Stored and replayed | Example |
|---|---|---|---|
| `FINAL` | the answer is a function of the request alone | yes | malformed body, validation of the request's own fields |
| `OUTCOME` | the command COMMITTED state, then answered an error that must be remembered | yes, and its writes commit with the stored answer | yard-management `409 carrier-suspended` (the REJECTED visit is recorded) |
| `RETRYABLE` | nothing committed; the answer depends on state that can change | no, the key is released and a retry executes | optimistic-lock conflict, a referenced fact not known yet, a state the aggregate is not in yet, a dependency not ready |

- A 2xx is stored. A 5xx or a panic is never stored. The middleware has no other status range and no
  list of slugs: it reads the class.
- A replay carries `Idempotency-Replayed: true`. The 24-hour window is unchanged.
- A problem type without a class fails a unit test. There is no runtime default.
- **Releasing a RETRYABLE key means a different body under the same key is a NEW request**, not
  `idempotency-key-reused`: nothing is left to compare against. Callers that reuse a key after a
  refusal on purpose now get their retry executed instead of the refusal back.
- **An in-flight duplicate replays; it never answers its own unstored refusal.** A RETRYABLE (or
  5xx) refusal is not stored and releases the key. So before the middleware answers an UNSTORED
  refusal it re-reads the key. When the lock is taken at the start of the unit, it re-reads under
  that lock before the handler runs. If the key is found, the request replays the stored answer
  (another body gets `idempotency-key-reused`). N concurrent copies of one keyed request are one run
  and N-1 replays. See "In-flight duplicates" below.

## Why

A stored refusal that a retry could pass pins a false answer for 24 hours: a `409
concurrent-modification` or a "destination not registered yet" replayed to a client that did the
right thing and retried. Classifying by status code cannot fix it (`409` covers both
`concurrent-modification` and `carrier-suspended`), and "store only 2xx" loses `OUTCOME`: the
command would run again and record twice. The class is per repo and per entry, not derivable from
the slug: `carrier-suspended` is a pure pre-check (RETRYABLE) in dock-scheduling and an OUTCOME in
yard-management.

## How to adopt in a repo (8 steps)

1. **Add the class to the catalogue entry** of every problem type (a `ReplayClass` field next to the
   status and slug; the repo's catalogue shape decides where: a slice of `{error, problem}`, or a
   status switch plus a slug table). Classify each by the question: *did the command commit state?*
   (OUTCOME) / *is the answer a function of the request alone?* (FINAL) / otherwise RETRYABLE.
2. **Make the class impossible to forget**: copy the template and let its first test fail on an
   unclassified entry. No default class in code.
3. **Make the production error writer read the class.** The writer marks the middleware's capturing
   writer: RETRYABLE calls `DoNotCache()` (shipping-and-tracking `response.go`, `notStoredUnderKey`),
   OUTCOME calls `KeepWrites()` (yard-management `response.go`, `outcomeRefusals`). The middleware
   reads the marks and holds no slug list.
4. **Make the middleware act on the marks**: RETRYABLE rolls the request back, key row included, and
   answers; OUTCOME commits the handler's writes together with the key row and the stored answer;
   2xx stores; 5xx and panics roll back and store nothing. Replays set `Idempotency-Replayed: true`.
   Before answering an unstored refusal (RETRYABLE or 5xx), re-read the key and replay what is found.
   When the key lock is taken at the start of the unit, re-read under it instead. A design that
   claims the key row in the request transaction before the handler already does this (see
   "In-flight duplicates").
5. **Copy the two templates** to the package of the middleware. `replay_classes_test.go.tmpl`:
   substitute `{{PKG}}`, `{{ROUTE}}`, `{{REUSED_SLUG}}` and change nothing else; it imports the
   standard library only. `replay_classes_wiring_test.go.tmpl`: substitute `{{PKG}}` and implement
   `newReplayOps(t)`, which returns the six operations below as **function fields** (`replayOps`).
   Only the wiring file knows the store; over a real Postgres name both `*_integration_test.go` and
   build-tag them like the repo's other integration tests (see "Fitness sensors" below).
6. **Add one HTTP-level test per real RETRYABLE and OUTCOME entry** on the real route and a real store,
   in the style of shipping-and-tracking `TestReturnLabelDestinationRefusalIsNotStoredSoItsKeyRetriesTheWork`
   (integration, key row count 0 after the refusal) and yard-management's OUTCOME test (one visit and
   one event after the refusal and a replay).
7. **Change the contract text**: spec descriptions, ADRs and READMEs that say "4xx are replayed" now
   say which refusals are (FINAL, OUTCOME) and which release the key (RETRYABLE); list the RETRYABLE
   problem types so clients know to retry the same key. Check deterministic-key callers (a key derived
   from an id): they now retry through a RETRYABLE refusal instead of being stuck for the window.
8. **Apply the same logic to every other write surface that stores answers**: MCP and A2A write tools
   with their own store, and the retention of the store itself (below). Then run the repo's gates.

### The wiring (what the template asks of a repo)

`newReplayOps(t) replayOps` returns one clean store's six operations as closures. The template's
`closureAdapter` forwards to them, so its assertions never see the store and it names no driver.

| Field (closure) | Provides |
|---|---|
| `Catalogue func() []problemEntry` | every problem type the idempotent routes can answer, from the production catalogue, with its declared class |
| `Middleware func(t, next)` | the production middleware from the repo's constructor, over the clean store, wrapping `next` |
| `Refuse func(w, r, e)` | answer `e` through the production error writer, so the class marking runs |
| `Write func(ctx, tag) error` | one write through the request's context: the same unit of work the middleware opened |
| `Committed func(t, tag) int` | committed writes tagged `tag`, counted from outside any request |
| `KeyRows func(t, key) int` | rows the store holds for `key` (0 after a release) |

Before (the first template, PR 66) the repo implemented a `repoAdapter` struct with six methods and
held its pool in a field, so the file that carried the assertions imported `pgxpool`. After, the
struct is gone: the closures capture the pool, the file that carries the assertions imports the
standard library only, and the one wiring file is the only place a driver can appear:

```go
// replay_classes_wiring_test.go (the repo's file; the verbatim test is untouched)
func newReplayOps(t *testing.T) replayOps {
	pool := pgtest.NewPool(t) // the package's shared testcontainers helper; no driver type is named
	count := func(t *testing.T, q string, args ...any) int { /* QueryRow(...).Scan(&n) */ }
	return replayOps{
		Middleware: func(_ *testing.T, next http.Handler) http.Handler { return RequireIdempotencyKey(pool, BindTx)(next) },
		Committed:  func(t *testing.T, tag string) int { return count(t, `SELECT count(*) FROM replay_probe WHERE tag = $1`, tag) },
		// Catalogue, Refuse, Write, KeyRows likewise
	}
}
```

### Fitness sensors

Services carry `TestPostgresIntegrationTestsUseTestcontainers` (managed, `internal/architecture`):
a `*_integration_test.go` that imports pgx or `database/sql` needs the testcontainers postgres module
imported in a `.go` file of the same package, and no `DATABASE_URL` read or `t.Skip` tied to it.
The rendered `replay_classes_test.go.tmpl` never trips it (no driver import). The wiring file must
satisfy it like any other Postgres test: reuse the package's shared container helper and take the
pool from it by type inference, so no driver package is named in the file (order-sourcing PR 83
works this way); if the package has no helper, add one first. A service whose store sits behind a
port needs no driver and no container at all. Do not move the assertions into a file named
otherwise to avoid the sensor; the fix is the closure shape, not the file name.

The tests prove: every entry has a class; per entry, the same key twice replays (FINAL, OUTCOME) or
executes twice (RETRYABLE, any 5xx); FINAL replays with the header and the same key with another body
is the reuse conflict; OUTCOME commits its write once and a replay records nothing new; RETRYABLE
releases the key (no row), the retry runs and then succeeds and is stored, and another body under the
released key is a new request; 2xx is stored; 5xx and a panic are not; and an in-flight duplicate
replays the original's stored 2xx instead of answering its own RETRYABLE re-run
(`TestConcurrentDuplicateReplaysTheOriginalsStoredAnswer`, below). That test calls the closures from
several goroutines, so the store behind them must be safe for concurrent use: a real Postgres is, and
an in-memory fake needs a mutex.

### In-flight duplicates (the concurrent-duplicate case)

**Defect** (fleet ledger P3-R15b / P4-R16, found in the asrs-control middleware lineage). A copy of a
keyed request (same key, same body) passes the first key lookup while the original is still in
flight. It waits, either on the key or on an aggregate lock the use case takes (a `FOR UPDATE` row,
a flow lock, a unique index). Then the use case runs again against the state the original just
committed. That re-run is a RETRYABLE refusal (`container-busy`, `concurrent-modification`,
`not-in-clarification`). The refusal is not stored and the key is released, so the copy answers 409
or 422 instead of the original's 201. The client did the right thing and still gets a refusal for a
command that succeeded.

**Rule.** Before answering an unstored refusal, the middleware re-reads the key. Found: replay the
stored answer (another body: `idempotency-key-reused`). When the key lock is taken at the start of
the unit, the re-read happens under that lock, before the handler runs. N copies are one run and N-1
replays.

**Reference fixes (patterns to copy).**

| Shape | Where the re-read sits | Reference |
|---|---|---|
| Re-read before an unstored refusal | `finishRefused`: if the answer is not stored (RETRYABLE or 5xx), `Store.Lookup` the key first; found -> `replay`, else flush the refusal. This is the same as a re-read under the lock, because the refusal already rolled back its writes and hooks. | material-flow-control `cab4359` (branch `feature/material-flow-control-core`), `internal/adapters/inbound/http/idempotency.go` |
| Re-read at the start of the unit | first statement inside `UoW.Do`: `Store.Lookup` under the serial/key lock; found -> lost race -> replay, before the use case runs | amr-fleet-control `b8d0026` (branch `feature/amr-fleet-control-core`), `internal/adapters/inbound/http/idempotency.go` |
| Key claimed first | the key row (or an advisory key lock + re-read) is claimed inside the request transaction before the handler; a copy blocks on the key and replays | yard-management `claim()`; the claim-first stores (order-management lineage) need no change |

Use both shapes when the lock is taken inside the use case and the lookup sits outside the unit (the
asrs-control lineage). A middleware that stores every 4xx is immune only by accident: it becomes
vulnerable when it adopts the replay classes, so adopt the re-read in the same change.

**Test.** `TestConcurrentDuplicateReplaysTheOriginalsStoredAnswer` gates the original inside the
handler, after its lookup and before its commit. It then sends three copies, the last one with
another body. Each copy that reaches the handler waits until the original has answered, then
refuses with the catalogue's first RETRYABLE entry, which is what a re-run against the original's
committed state does. A design that blocks copies on the key never lets them into the handler; the
test then releases the original after a short grace. The test asserts that every same-body copy
replays the stored 201 with `Idempotency-Replayed: true`, that the other-body copy answers
`idempotency-key-reused`, that the original's write is committed once, and that the key has one row.
**Mutant it must kill:** remove the re-read (in `finishRefused`, or at the start of the unit). The
copies then answer their own RETRYABLE refusal unreplayed. The selftest runs this mutant over both
fix shapes (`no-reread` below).

## Per-design notes (phase 0, 40 repos read on 2026-11-18)

- **Key row in the business transaction** (most repos): releasing is the rollback the 5xx branch
  already does, so RETRYABLE is easy. The hard part is OUTCOME: the middleware opens the unit of
  work first and `UnitOfWork.Do` joins it, so a use case that commits and then returns the refusal
  has its writes rolled back when the middleware rolls back on an error status. yard-management
  carried this defect (turn-away recorded in the answer, rolled back in the database): `KeepWrites`
  makes the middleware commit the writes with the key row. The adapter must prove it from a second
  connection (`Committed`), not from inside the request.
- **Savepoint designs** (shipping-and-tracking): the handler runs behind a savepoint so a failed
  statement does not poison the transaction. A RETRYABLE answer still rolls back the whole
  transaction, key row included. A 2xx behind a failed release must not be stored.
- **Stores behind a port** (key written apart from the business transaction): releasing is an explicit
  `Delete`/`Release` on the port, and it must happen on every RETRYABLE path, including the one where
  the use case returned after committing nothing. OUTCOME needs the stored answer and the writes to
  be atomic by other means, or the repo records the outcome idempotently so a retry cannot double it;
  write that decision in the repo's ADR.
- **MCP and A2A write tools with their own store** (dock-scheduling `mcp:` keys, yard-management's
  tools): the same class logic applies per tool; a tool that stores a failure (dock-scheduling stores
  422) must classify it. They do not inherit the REST middleware.
- **Retention must actually be enforced.** The 24-hour window is a rule, not a comment: purge old
  rows and refuse to replay a row older than the window (several repos keep rows forever or have no
  age check on replay). A stored answer that outlives its window is the same defect as a wrong
  stored refusal.
- **Spec text that says 4xx are replayed must change** (OpenAPI descriptions of the header, ADRs,
  README, runbooks, consumer clients that treat a 409 or 422 as terminal).

## Gate

`selftest` compiles and runs both templates (`internal/architecture/idempotency_template_test.go`,
template-only: it skips in a scaffold). `go test ./internal/architecture/` renders them into a
scratch module with `{{PKG}}`, `{{ROUTE}}`, `{{REUSED_SLUG}}` substituted and a toy in-memory
middleware (`internal/architecture/testdata/idempotency/`): the wiring skeleton compiles against the
verbatim test, `gofmt -l` is clean, `go vet` and `go test` are green, and the run goes red naming the
right test when the toy stores a RETRYABLE answer, drops an OUTCOME write, or has an unclassified
entry. The toy is green under both in-flight fixes (`TOY_SHAPE` unset: re-read before an unstored
refusal; `TOY_SHAPE=lock-at-begin`: key lock and re-read at the start of the unit). With
`TOY_MUTATION=no-reread`, each shape fails `TestConcurrentDuplicateReplaysTheOriginalsStoredAnswer`. A second test asserts the rendered files import the standard library only and that the
Postgres sensor above finds nothing in them when they are named `*_integration_test.go`.
Change the template, and that test is the proof.
