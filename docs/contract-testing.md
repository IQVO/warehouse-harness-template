# Consumer contract testing — the fleet standard

How a warehouse-systems consumer repo proves it can still talk to its producers,
and how CI proves those proofs don't go stale. The pattern is proven in
**shopping-cart** (which pins delivery-promise's `apis/openapi.yaml`); this kit
makes it a copy-paste standard for every sibling repo.

A consumer's contract test validates against a **copy** of the producer's spec.
A copy nobody refreshes keeps the test green while the producer moves on: on
2026-11-01 vendor-connectivity changed every collection to `{ items, nextCursor }`
and shopping-cart's test, still pinned to the 2026-10-24 copy, stayed green
while the poller would have read `200` and zero purchase orders. The kit has two
halves for that reason:

1. **The tests** (archetypes R1–R4 for REST, E1–E4 for events) prove the
   consumer and the pinned copy agree.
2. **The drift gate** (`scripts/harness/pinned_contract_gate.py`, CI job
   `pinned-contracts`) proves the pinned copy and the producer's `develop`
   agree — byte for byte.

## Adopting the kit (copy these four things from this template repo)

1. `scripts/harness/pinned_contract_gate.py` and
   `scripts/harness/test_pinned_contract_gate.py` — copied **verbatim**;
   do not fork them per repo.
2. `contracts/pinned.json` — create it from `contracts/pinned.example.json`
   (this template ships the example only; the template itself pins nothing).
   A registry with `"pins": []` is valid and passes the gate.
3. The `pinned-contracts` CI job — paste the job block from
   [the CI section below](#ci-the-pinned-contracts-job) into
   `.github/workflows/ci.yml`.
4. The archetype tests for each producer adapter / subscription — start from
   `templates/contracts/rest_consumer_contract_test.go.tmpl` (R1–R4) and
   `templates/contracts/event_consumer_contract_test.go.tmpl` (E1–E4), adapted
   to the repo's real gateway, DTOs and decoder.

Also copy the producer's file into the repo as the pin itself (see the registry
format below) and record the pin's provenance in the adapter's
`testdata/README.md`, like shopping-cart's
`internal/adapters/outbound/deliverypromise/testdata/README.md` does.

## The pin registry: `contracts/pinned.json`

One entry per pinned producer file:

```json
{
  "_doc": "Every contract copy this repo pins from a producer, and the producer branch it must equal. scripts/harness/pinned_contract_gate.py fails when a copy differs from the producer's develop. Refresh = copy the file, never edit it.",
  "pins": [
    {
      "local": "internal/adapters/outbound/deliverypromise/testdata/delivery-promise.openapi.yaml",
      "repo": "IQVO/delivery-promise",
      "path": "apis/openapi.yaml",
      "ref": "develop"
    }
  ]
}
```

| Field  | Meaning |
|---|---|
| `local` | Path of the byte-copy inside the consumer repo. Convention: `internal/adapters/outbound/<producer>/testdata/<producer>.openapi.yaml` / `<producer>.asyncapi.yaml`. |
| `repo`  | The producer's GitHub repo, always `IQVO/<producer>`. |
| `path`  | The pinned file in the producer repo: `apis/openapi.yaml` (REST) or `apis/asyncapi.yaml` (events). |
| `ref`   | The producer branch the copy must equal — `develop` across the fleet. |

**Pins are byte-copies.** The gate compares bytes, so a "small local fix" to a
pin is indistinguishable from drift and fails CI. The refresh procedure is
always the same:

```bash
gh api -H 'Accept: application/vnd.github.raw' \
  repos/IQVO/<producer>/contents/apis/openapi.yaml?ref=develop \
  > internal/adapters/outbound/<producer>/testdata/<producer>.openapi.yaml
```

…then run the archetype tests and **fix what they report** — that is the whole
point: the refresh is where you learn the producer changed. Update the
provenance note in the adapter's `testdata/README.md` (and the ADR, if one
covers the integration) in the same commit.

## Drift policy

- The CI job `pinned-contracts` runs `python3 scripts/harness/pinned_contract_gate.py`
  on **every push and every PR**.
- A stale pin **fails CI**. A producer that cannot be read also fails CI
  (exit 2) — a gate that cannot look must say so, never pass vacuously.
- When the gate goes red, the consumer refreshes the pin **within 2 business
  days**: copy the producer's file, fix what the contract tests report, merge.
- Nobody edits a pin to make the gate green. If the producer's change is
  genuinely breaking and unfixable in 2 days, that is a coordination
  conversation with the producer's owners (an ADR on both sides), not a local
  edit — the byte-copy rule has no exceptions.

## REST consumer test archetypes (R1–R4)

Plain unit tests living **next to the gateway** they guard
(`internal/adapters/outbound/<producer>/`), one set per producer adapter,
driven by the pinned copy in `testdata/`. Skeleton:
`templates/contracts/rest_consumer_contract_test.go.tmpl`.

- **R1 `TestPinnedSpecCoversEveryCallSite`** — every `(method, path)` the
  gateway issues exists in the pinned spec. A typo'd route or a call site added
  without a contract fails here first.
- **R2 `TestExamplesSatisfyPinnedSpec`** — every testdata fixture (the
  producer's inline examples, or derived ones — see
  [fixture fallback](#fixture-fallback-when-the-spec-has-no-example)) validates
  against the pinned schema with kin-openapi.
- **R3 `TestGatewayDecodesSpecExamples`** — the fixtures are served via
  `httptest` **through the real gateway**; success fixtures decode into the
  DTOs the domain reads; error `problem+json` fixtures map to the gateway's
  sentinel errors (`errors.Is`), not to a generic 500.
- **R4 `TestFieldsWeReadAreSpecified`** — reflect over the gateway's DTO
  `json` tags: each tag must exist in the pinned schema with a compatible
  type. A field optional in the spec must be tolerated by the DTO (pointer /
  `omitempty` / zero-value handling) — the gateway may not crash because the
  producer legitimately omitted an optional field.

The live exemplars: shopping-cart's
`internal/adapters/outbound/deliverypromise/spec_test.go` (R1/R4-style key and
route assertions) and `gateway_test.go` (R3: status/problem+json tables mapped
to `ports.ErrQuoteRejected` / `ports.ErrDeliveryPromiseUnavailable`).

## Event consumer test archetypes (E1–E4)

Plain unit tests per **subscribed producer**, living next to the Kafka reader /
CloudEvents decoder that handles those events. Specs are parsed with
`gopkg.in/yaml.v3` (already in every fleet go.mod) — **no other new
dependencies**. Skeleton: `templates/contracts/event_consumer_contract_test.go.tmpl`.

- **E1 `TestPinnedAsyncAPICoversSubscriptions`** — every topic the repo's Kafka
  reader subscribes to appears as a **produced** channel in the producer's
  pinned asyncapi (given the reading convention below: for a 2.6 spec, produced
  channels are the ones documented under `subscribe:`).
- **E2 `TestGoldenEventsDecode`** — every message type documented on a
  subscribed topic decodes through the repo's **real** CloudEvents
  decoder/handler and lands in the read model / policy input it feeds.
- **E3 `TestCloudEventsEnvelopeInvariants`** — fixtures carry the mandatory
  envelope: `specversion` 1.0, `id`, `source`, `type`, `subject`, `time`,
  `datacontenttype`, `dataschema` (`urn:warehouse:<producer>:...` — the
  retail-network variant is `urn:retailnetwork:<context>:...`); the Kafka
  header `content-type: application/cloudevents+json`.
- **E4 `TestUnknownAttributesTolerated`** — extra `data` keys the repo does not
  know must not break the decoder (forward compatibility); a `dataschema`
  version the repo does not implement routes to the repo's **documented DLQ
  policy** if it has one (skip-and-commit and dead-letter-at-once are both
  legitimate; "undefined panic" is not).

## CRITICAL CONVENTION: the fleet's asyncapi files are written channel-perspective

> **Read specs accordingly — this inverts the AsyncAPI 2.6 app perspective.**
>
> - A producer's **OWN topics** are documented under **`subscribe:`** — "this
>   is what you can subscribe to"; the app itself publishes, written by the
>   outbox relay. When you pin a producer's asyncapi to find the channels it
>   produces, look under `subscribe:`.
> - Topics the service **CONSUMES from others** are documented under
>   **`publish:`**, tagged `consumed` — "other apps publish here".
>
> Example (inventory-storage, 2.6.0): its own `wms.inventory-storage.events`
> channel is a `subscribe:` operation (`consumeInventoryStorageEvents` —
> addressed to downstream consumers), while product-master's topic it reads is
> a `publish:` operation tagged `product-master`/consumed.

The retail-network specs (shopping-cart, pricing, delivery-promise, ...) are
**AsyncAPI 3.0**, where the same channel-perspective split is expressed as
operations: the producer's own topics are `action: send` operations, consumed
topics are `action: receive` operations tagged `consumed`. Same reading rule:
to find what a producer produces, look at its `send` (3.0) / `subscribe:` (2.6)
operations — never assume the 2.6 app-perspective meaning you may know from
other AsyncAPI codebases.

## Dependencies

- `github.com/getkin/kin-openapi` — REST archetypes R2/R4 (schema validation
  and traversal). **Test-only**: `go get github.com/getkin/kin-openapi@latest`
  and keep it out of the production import graph.
- `gopkg.in/yaml.v3` — event archetypes (parsing the pinned asyncapi). Already
  present in every fleet repo; no other new dependencies are allowed for this
  kit.

## Fixture fallback (when the spec has no example)

When a producer spec carries no examples for an operation, derive the fixture
from the schema: every `required` key present, realistic values (an IANA time
zone for a time-zone field, a real-shaped id for an id field), optional fields
omitted in some fixtures so R4's tolerance check has something to chew on.
Mark it in the PR body: **`derived from schema; producer example backfill
pending`** — and ask the producer to backfill inline examples, so the next
refresh can validate against the producer's own examples instead.

## CI: the `pinned-contracts` job

The exact job block (from shopping-cart's `.github/workflows/ci.yml`,
genericized — no service-specific parts; `GH_TOKEN: ${{ github.token }}` is
what makes the `gh` CLI calls in the gate work):

```yaml
  # ============================================================
  # CLASSIFICATION: CONTRACT PINS (compared with the producer's develop)
  # ============================================================
  # The consumer contract test validates against a COPY of the producer's spec. A
  # copy nobody refreshes keeps that test green while the producer moves on. This
  # job fails when a pin in contracts/pinned.json differs from the producer's
  # develop. The producer repo is public; a producer that cannot be read fails the
  # job (exit 2), never passes it.
  pinned-contracts:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
      - name: Pins equal the producer's develop
        env:
          GH_TOKEN: ${{ github.token }}
        run: python3 scripts/harness/pinned_contract_gate.py
      - name: The gate's own tests
        run: python3 scripts/harness/test_pinned_contract_gate.py
```

A repo that has not pinned anything yet can guard the job with
`if: hashFiles('contracts/pinned.json') != ''` so scaffolds stay green until
they take their first pin; the moment `contracts/pinned.json` exists (even with
`"pins": []`) the gate runs.

## Banned-edge rule (who may pin what)

**process-path-management and labor-performance ban ALL sibling REST/MCP
calls.** They may pin **event specs only** (`apis/asyncapi.yaml`) — never REST
specs (`apis/openapi.yaml`): a REST pin for them would document an integration
that is not allowed to exist. Every other repo pins per its real integrations.

## What lives where (summary)

| Path | What |
|---|---|
| `contracts/pinned.json` | The pin registry (per repo; example in this template). |
| `scripts/harness/pinned_contract_gate.py` | The drift gate (verbatim copy, per repo). |
| `scripts/harness/test_pinned_contract_gate.py` | The gate's self-test (verbatim copy, per repo). |
| `internal/adapters/outbound/<producer>/testdata/` | The pinned byte-copies + fixtures + provenance README. |
| `internal/adapters/outbound/<producer>/*_test.go` | R1–R4 per REST producer; E1–E4 per subscribed topic. |
| `templates/contracts/*.go.tmpl` (this repo) | The archetype skeletons adopters copy. |
