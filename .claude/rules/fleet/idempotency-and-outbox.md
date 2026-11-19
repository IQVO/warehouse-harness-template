---
paths:
  - "internal/adapters/inbound/http/**"
  - "internal/adapters/outbound/postgres/**"
  - "migrations/**"
---

# Fleet rule: idempotent writes and the transactional outbox

- Resource-creating POSTs require an `Idempotency-Key` header (the fleet idempotency middleware; the decision record lives in facility-layout): a missing key is a 400 `idempotency-key-required`. Every client in the fleet (UIs, e2e, simulators, agents) must send a fresh key per logical action.
- A request retried under the same `Idempotency-Key` is answered from the store only when the first answer is final for that request. Every problem type a service can answer on an idempotent route has exactly ONE replay class:
  - `FINAL`: the answer is a function of the request alone (malformed body, validation of the request's own fields). Stored and replayed.
  - `OUTCOME`: the command COMMITTED state and then answered an error that must be remembered (yard-management `409 carrier-suspended`: the REJECTED visit is recorded). Stored AND its writes commit with the stored answer.
  - `RETRYABLE`: nothing committed and the answer depends on state that can change (optimistic-lock conflict, a referenced fact not known yet, a state the aggregate is not in yet, a dependency not ready). NOT stored; the key is released and a retry executes again.
- A success (2xx) is stored. A 5xx or a panic is never stored. The middleware knows no other status range and holds no list of slugs. A replay carries `Idempotency-Replayed: true`; the 24-hour window is unchanged and must actually be enforced (purge, and an age check on replay).
- The class is declared on the catalogue entry of each problem type, per repo and per entry: it is NOT derivable from the slug or the status (`carrier-suspended` is RETRYABLE in dock-scheduling and OUTCOME in yard-management). A problem type without a class fails a unit test; there is no runtime default.
- Releasing a RETRYABLE key means the request is forgotten: the same body under the same key runs again, and a different body under the same key is a NEW request, not `idempotency-key-reused`.
- OUTCOME mechanism: the handler marks the capturing writer and the middleware commits the handler's writes together with the key row and the stored answer in ONE transaction; a replay answers from the row and records nothing new. A RETRYABLE answer rolls the whole request back, key row included. Reference test and adoption steps: `docs/idempotency-replay-classes.md` and `templates/idempotency/replay_classes_test.go.tmpl` in warehouse-harness-template.
- Events are published through the transactional outbox: the state change and the outbox row commit in one transaction and a relay publishes afterwards. Never publish to Kafka directly from a request handler.
- Test suites that run against shared infrastructure are idempotent: scope data by a per-run token and never purge tables in a setup step.
