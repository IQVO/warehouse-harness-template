# Idempotency replay classes — the fleet standard

What a service answers when a request is retried under the same `Idempotency-Key`, how that is
classified per problem type, and the reference test every repo copies to prove it. The rule text is
`.claude/rules/fleet/idempotency-and-outbox.md`; the test is
`templates/idempotency/replay_classes_test.go.tmpl`. The fleet decision (owner, 2026-11-18)
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
5. **Copy `templates/idempotency/replay_classes_test.go.tmpl`** to the package of the middleware,
   substitute `{{PKG}}`, `{{ROUTE}}`, `{{REUSED_SLUG}}` and implement `repoAdapter` (the six methods
   below).
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

### The adapter (what the template asks of a repo)

| Method | Provides |
|---|---|
| `Catalogue() []problemEntry` | every problem type the idempotent routes can answer, from the production catalogue, with its declared class |
| `Middleware(t, next)` | the production middleware from the repo's constructor, over the adapter's clean store, wrapping `next` |
| `Refuse(w, r, e)` | answer `e` through the production error writer, so the class marking runs |
| `Write(ctx, tag)` | one write through the request's context: the same unit of work the middleware opened |
| `Committed(t, tag)` | committed writes tagged `tag`, counted from outside any request |
| `KeyRows(t, key)` | rows the store holds for `key` (0 after a release) |

The tests prove: every entry has a class; per entry, the same key twice replays (FINAL, OUTCOME) or
executes twice (RETRYABLE, any 5xx); FINAL replays with the header and the same key with another body
is the reuse conflict; OUTCOME commits its write once and a replay records nothing new; RETRYABLE
releases the key (no row), the retry runs and then succeeds and is stored, and another body under the
released key is a new request; 2xx is stored; 5xx and a panic are not. Concurrency is not covered by
the template (a concurrent duplicate waiting on the key and replaying is the store's own test).

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

The template is not compiled by this repo's `selftest` (neither are the other `templates/*` files).
The proof when it changes: render it into a scratch module with the placeholders substituted and a
toy in-memory adapter, `gofmt -l`, `go vet`, `go test -race` green, then red with a middleware that
stores a RETRYABLE answer, one that drops an OUTCOME write, and an unclassified entry.
