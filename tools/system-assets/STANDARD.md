# System Assets & Interaction Standard — v0.4 (DRAFT)

Status: proposed, 2026-11-27. Not yet adopted by any repo.
Scope: every repo in warehouse-systems (wms, wes, retail-network, storefront,
operator-experience).

Changes in v0.2: Part 0 (root `application.yaml`); MCP Server Card governed by
the bex.co article; MCP version == Helm chart version (A.3.1 MC-5).
Changes in v0.4: Part E (every contract change yields a new `system/<version>/`
folder); builder + standard move into warehouse-harness-template
`tools/system-assets/`; the business overlay lives in each repo as
`system/interactions.overlay.yaml`; the skill `system-assets-release` ships to
every application repo.
Changes in v0.3 (first adoption, warehouse-planning): `business.triggers[]`
replaces `trigger` + `actor` (one use case is reached from REST, MCP and A2A);
trigger kind `api` carries `via`; subscriptions name the producer op, the
consumer op, a `types` list and `useCases[]` or a read-model `projection`;
primitives manifest is identified by `apiVersion`, not a `$schema` URL.
Distribution (owner decision 2026-11-27): the documents of Parts 0, A and B
ship inside the per-release folder of Part D in `system/`; the codebase is
not modified, so the in-repo paths of 0.2 / A.2 describe the bundle layout.

Parts (D and E, at the end, cover the release folder and the change rule):

- Part 0 — Application descriptor: WHAT the repo is (application, subdomain,
  bounded context, description, tags), at the repo root.
- Part A — System Assets: the machine-readable contracts a context EXPOSES.
- Part B — Interactions: the calls a context MAKES, each described by
  technology + operation + business context.

Keywords MUST / SHOULD / MAY per RFC 2119.

---

## Part 0 — Application descriptor (`application.yaml`, repo root)

### 0.1 Purpose

Every repo MUST have `application.yaml` at its root. It is the single
identity record of the repo: which application it is, which subdomain it
belongs to, which bounded context it implements, what that context is for,
and its tags. Every other file in this standard references it instead of
repeating context/subdomain/owner.

### 0.2 Shape

```yaml
apiVersion: warehouse/application/v1

application:
  id: fulfillment-execution                 # == repo name == EventCatalog service id (without -api)
  name: Fulfillment Execution
  kind: bounded-context                     # bounded-context | experience | agent | platform | test-double
  description: >-
    Backend service of the fulfillment-execution bounded context: REST API,
    MCP server, A2A agent and Kafka publisher.
  repository: https://github.com/IQVO/fulfillment-execution
  owners: [claudioed]                       # EventCatalog users/ ids
  lifecycle: production                     # experimental | production | deprecated

subdomain:
  id: wes                                   # EventCatalog domain id
  name: WES
  summary: >-
    Warehouse Execution System: planning, releasing and executing work on the floor.

boundedContext:                             # REQUIRED when kind = bounded-context, FORBIDDEN otherwise
  id: fulfillment-execution
  name: Fulfillment Execution
  classification: Core                      # Core | Supporting | Generic (DDD strategic)
  tier: WES
  purpose: >-
    Pick, pack and SLAM task lifecycle with pull-based claimNext dispatch and
    lease semantics. The execution side of the WES: work is claimed by
    stations and associates, never pushed.
  publishes:                                # Kafka topics this context OWNS
    - warehouse.fulfillment.events
    - warehouse.fulfillment.analytics

tags: [task, package, station, rebin, promise-feedback]

assets: apis/system-assets.yaml             # Part A
interactions: apis/interactions.yaml        # Part B
```

### 0.3 Where the values come from (ecosystem)

The ecosystem repo (EventCatalog, `IQVO/ecosystem`, origin/develop) already
holds this data; bootstrap every `application.yaml` from it:

| field                                   | ecosystem source                                                   |
|-----------------------------------------|--------------------------------------------------------------------|
| `subdomain.id/name/summary`             | `catalog.contexts.mjs` `SUBDOMAINS.<id>` (and `domains/<id>/index.mdx`) |
| `boundedContext.classification/tier/purpose/publishes` | `catalog.contexts.mjs` `CONTEXTS.<ctx>` (`classification`, `tier`, `purpose`, `topics`) |
| `application.owners`                    | service `owners` (`users/*.mdx`)                                   |
| `tags`                                  | `badges[].content` of `domains/<sd>/services/<ctx>/index.mdx` and `<ctx>-api/index.mdx` |

Tag normalization (deterministic, scripted):

1. Union the badges of the events service and the `-api` service.
2. Kebab-case (split camelCase/PascalCase: `StorageJob` -> `storage-job`),
   singularize the last word (`Tasks` -> `task`; protected: `asrs`,
   `analytics`, `status`, `process`, `logistics`).
3. DROP relationship and technical badges: any bounded-context id (self or
   another — those are edges, they belong in Part B), `consumed`,
   `published`, any `consumed-*` / `published-*`, `analytics`, `system`,
   `health`, `stateless`, and the classification words (`core`,
   `supporting`, `generic` — already in `boundedContext.classification`).
4. Dedupe on the hyphen-less form, preferring the hyphenated spelling
   (`storagejob` + `storage-job` -> `storage-job`); keep first-seen order.

Script: `derive_tags.py` (beside this file) writes `application-tags.json`,
the normalized output for all 44 catalogued applications (192 tags).
Known source defect: some AsyncAPI specs carry squashed lower-case aggregate
tags with no hyphenated twin (`equipmentunit`, `locationslot`,
`outboundload`, `shippingpackage`, `deliverychargeschedule`, ...). Per P-3
these are fixed in the producer spec (`EquipmentUnit`), not hand-split here.

Example (real ecosystem data, 2026-11-27):
- fulfillment-execution badges `fulfillment-execution, task, package,
  promise-feedback, analytics, consumed` + `Tasks, Stations, Packages, Rebin,
  System` -> `[task, package, promise-feedback, station, rebin]`.
- inventory-storage badges `inventory-storage, stock, reservation, bin,
  product, product-master, fulfillment-execution, inbound-receiving` +
  `Stock, Reservations, Inventory, Bins, Products, Health, Transfers,
  Reports` -> `[stock, reservation, bin, product, inventory, transfer, report]`.

Non-context applications (operator-experience domain: `warehouse-ops-agent`,
`warehouse-console`; floor client, BFFs, emulators, simulators) use
`kind: agent | experience | test-double`, omit `boundedContext`, and keep
`subdomain` pointing to the EventCatalog domain they are catalogued in.

### 0.4 Rules

- P-1 `application.id` == repo name; `boundedContext.id` == `application.id`
  for `kind: bounded-context` (one context per repo).
- P-2 `subdomain.id` MUST be an existing EventCatalog domain id
  (`wms`, `wes`, `retail-network`, `storefront`, `operator-experience`).
- P-3 `tags` MUST be a subset of the normalized ecosystem badges for that
  application (CI fetches ecosystem origin/develop and checks). A new tag is
  added by adding the tag to the spec (OpenAPI/AsyncAPI `tags`), which the
  catalogue turns into a badge — never only in `application.yaml`.
- P-4 Ownership flip: after bootstrap, `application.yaml` is the source of
  truth for `classification`, `tier`, `purpose` and `publishes`;
  `scripts/sync-specs.sh` in ecosystem pulls it with the specs and
  `catalog.contexts.mjs` is generated from it (no two hand-edited copies).
- P-5 `boundedContext.publishes` MUST equal the channels the context sends to
  in its AsyncAPI.

---

## Part A — System Assets

### A.1 Definition

A **system asset** is a versioned, machine-readable contract document that
describes a surface of a bounded context that another system can invoke or
subscribe to. Exactly four asset kinds exist:

| kind      | describes                         | external standard (verified 2026-11-27)                               | fleet today       | target            |
|-----------|-----------------------------------|------------------------------------------------------------------------|-------------------|-------------------|
| `openapi` | synchronous REST API              | OpenAPI Specification — latest 3.2.0 (2025-09-19)                      | 3.0.3             | 3.1.x (3.2 when spectral/oasdiff support it) |
| `asyncapi`| published/consumed Kafka events   | AsyncAPI — latest 3.1.0 (2026-01-31); messages are CloudEvents 1.0     | 2.6.0             | 3.0.0+            |
| `a2a`     | A2A agent surface (skills)        | A2A Protocol 1.0.0 `AgentCard`, served at `/.well-known/agent-card.json`| live cards only  | 1.0 card, committed snapshot |
| `mcp`     | MCP server surface (tools/resources/prompts) | MCP Server Card per bex.co / SEP-2127 + fleet primitives manifest (A.3) | `tool_registry.golden` TSV | server card + primitives manifest |

Anything else (Helm charts, ADRs, EventCatalog pages, READMEs, k6 scripts) is
NOT a system asset. EventCatalog pages are DERIVED from system assets, never
the source of truth.

### A.2 Location and classification

Every context repo MUST have:

```
application.yaml              # Part 0 (repo root)
apis/
  system-assets.yaml          # the classification manifest (A.4)
  openapi.yaml                # kind: openapi   (if the context has REST)
  asyncapi.yaml               # kind: asyncapi  (if it publishes or consumes events)
  a2a/agent-card.json         # kind: a2a       (if it has an A2A server)
  mcp/server-card.json        # kind: mcp       (if it has an MCP server)
  mcp/primitives.json         #   "    "
  interactions.yaml           # Part B
```

Classification lives ONLY in `apis/system-assets.yaml`. Specs are NOT tagged
in-band (no `x-asset-class`): A2A cards are proto-defined and MCP schemas are
closed, so in-band markers would break strict validators, and a second copy of
the classification would drift.

Asset identity: `<context>/<kind>`, e.g. `inventory-storage/openapi`.
Asset version: SemVer, equal to `info.version` (OpenAPI/AsyncAPI), `version`
(AgentCard, Server Card). Breaking change => major bump (oasdiff /
asyncapi_breaking.py already gate this).

### A.3 The MCP asset (why it is two documents)

Research result: MCP has no OpenAPI-equivalent contract file.

- `server.json` (MCP Registry, schema `2025-12-11`) describes a registry
  entry/package; meant for publishing to a registry. Not used here.
- **MCP Server Card** (SEP-2127, Draft) describes identity + remote
  transport endpoints + supported protocol versions. It DELIBERATELY
  EXCLUDES tools/resources/prompts — primitives are discovered at runtime
  via `tools/list` / `server/discover`.

GOVERNING DOCUMENT (owner decision 2026-11-27): every MCP server's card
follows https://bex.co/blog/2026/07/08/mcp-server-cards-well-known-discovery
(SEP-2127 well-known discovery). Where the later upstream
`ext-server-card` repo differs (it moved cards to `<mcp-url>/server-card`
and an AI Catalog), the bex.co layout WINS for this fleet.

Therefore the `mcp` asset is:

1. **Server Card** — `apis/mcp/server-card.json`, served live (A.3.1).
2. `apis/mcp/primitives.json` — FLEET EXTENSION. A generated snapshot of the
   real handler's `tools/list`, `resources/list`, `prompts/list` results,
   objects verbatim in MCP spec shape (`name`, `title`, `description`,
   `inputSchema`, `outputSchema`, `annotations`, `_meta`, including
   `com.warehouse/authority`), sorted by name. Top level:
   `{apiVersion: warehouse/mcp-primitives/v1, server: <card name>, tools,
   resources, resourceTemplates, prompts}`.
   It is generated by a test over `wireSession` and golden-compared, so it
   cannot drift from runtime; it supersedes `tool_registry.golden`.

Runtime remains authoritative (upstream rule); `primitives.json` is the design
-time contract used for mapping, review and breaking-change detection.

### A.3.1 MCP Server Card rules (all MCP servers)

Shape — exactly the bex.co / SEP-2127 field set, nothing else:

```json
{
  "name": "com.warehouse.wms/inventory-storage",
  "version": "2.0.7",
  "title": "Inventory & Storage MCP",
  "description": "Read stock availability, bin occupancy and containers for the inventory-storage context.",
  "websiteUrl": "https://<docs-site>/inventory-storage",
  "repository": { "url": "https://github.com/IQVO/inventory-storage", "source": "github" },
  "remotes": [
    {
      "type": "streamable-http",
      "url": "https://<agent-router-host>/mcp/inventory-storage",
      "protocolVersions": ["2026-07-28"]
    }
  ],
  "icons": []
}
```

- MC-1 Discovery paths (RFC 8615, plain unauthenticated `GET`,
  `Content-Type: application/json`, cacheable):
  - each `cmd/mcp` binary serves its own card at `/.well-known/mcp-server-card`;
  - the Agent Router (many servers, one origin) exposes each one at
    `/.well-known/mcp-server-card/{server-name}`, `{server-name}` == `name`.
- MC-2 `name` = `com.warehouse.<wms|wes|wcs|network>/<context>` (reverse-DNS,
  exactly one `/`). `description` states what the server does in domain
  language, never implementation.
- MC-3 NO `tools`, `resources`, `prompts`, credentials or internal
  hostnames in the card. Capability surface lives only in `primitives.json`
  and at runtime.
- MC-4 `remotes[].url` is the PUBLIC Agent Router URL (ADR 0015), never the
  in-cluster Service DNS; `protocolVersions` lists exactly the versions the
  SDK negotiates (today `2026-07-28`).
- MC-5 **VERSION == CHART VERSION.** The card `version`, the MCP
  `initialize` `serverInfo.version` (`mcp.Implementation.Version`) and the
  Helm chart `version` MUST be the same string. Today the release job stamps
  `helm package --version` from the git tag (`v2.0.7` -> `2.0.7`), so all
  three equal the release tag without `v`. Mechanism:
  - the chart renders `MCP_SERVER_VERSION: {{ .Chart.Version | quote }}`
    into the MCP Deployment env;
  - the binary builds the card and `mcp.Implementation` from that env var
    and REFUSES TO BOOT if it is empty (no hardcoded `"1.0.0"`);
  - the committed `apis/mcp/server-card.json` carries the same version as
    `charts/<context>/Chart.yaml` (the dev placeholder, e.g. `0.1.0`); CI
    asserts that equality, and the release job rewrites it with the
    release version and attaches it to the GitHub release.
  - Contract test: `helm template` -> boot -> `GET /.well-known/mcp-server-card`
    -> assert `version == .Chart.Version` and the body equals the committed
    file except `version`.
- MC-6 Card is ON by default (`mcp.serverCard.enabled: true`): every server
  is internal behind the Agent Router. Per the bex.co guidance, any
  externally exposed deployment flips it deliberately; it is never removed
  from the code.

### A.4 `apis/system-assets.yaml`

```yaml
apiVersion: warehouse/system-assets/v1
application: ../application.yaml    # identity (context, subdomain, owners) lives there only
assets:
  - id: inventory-storage/openapi
    kind: openapi
    path: openapi.yaml
    spec: { name: openapi, version: 3.0.3 }
    version: 2.4.0
    exposure: kong                  # gateway route (ADR 0014)
  - id: inventory-storage/asyncapi
    kind: asyncapi
    path: asyncapi.yaml
    spec: { name: asyncapi, version: 2.6.0 }
    version: 1.9.0
    exposure: kafka
  - id: inventory-storage/mcp
    kind: mcp
    documents:
      serverCard: mcp/server-card.json
      primitives: mcp/primitives.json
    spec: { name: mcp, version: "2026-07-28", serverCard: sep-2127 (bex.co layout) }
    version: chart                  # MUST be the literal `chart`: resolved to .Chart.Version (MC-5)
    exposure: agent-router          # ADR 0015
  - id: inventory-storage/a2a
    kind: a2a
    path: a2a/agent-card.json
    spec: { name: a2a, version: 1.0.0 }
    version: 1.1.0
    exposure: agent-router
```

Rules:

- A-1 One asset per kind per context (one MCP server per context, ADR-0008).
- A-2 Every file listed MUST exist and validate against its upstream schema
  (spectral / `asyncapi validate` / A2A card validator / Server Card
  `schema.json` / fleet primitives schema).
- A-3 Served artifacts (agent card, server card) MUST equal the committed
  file at the released tag (CI contract test boots the server); for the MCP
  server card the only allowed difference is `version`, which MUST be the
  chart version (MC-5).
- A-4 Operation identifiers MUST be present and stable, because Part B
  references them:
  - openapi: every operation has `operationId` (camelCase).
  - asyncapi: every operation has an id (`operations.<id>` in 3.x,
    `operationId` in 2.6) and every message names its CloudEvents `type`.
  - mcp: tool `name` (snake_case), resource `uri`, prompt `name`.
  - a2a: skill `id` (snake_case).
- A-5 Consumers pin producer assets as they already do
  (`internal/adapters/outbound/<x>/testdata/<producer>.<kind>.yaml|json`);
  the pin MUST be a copy of a released producer tag.

---

## Part B — Interaction Standard

### B.1 Definition

An **interaction** is one outbound call from a caller context to an operation
of a system asset. It is always described by three facets:

```
technology  — HOW the call is made      (rest | event.publish | mcp | a2a)
operation   — WHAT is invoked           (asset + operation id + effect)
business    — WHY it is invoked          (capability, process, use case, trigger, actor)
```

Interactions are declared by the CALLER in `apis/interactions.yaml`. The
caller owns the "why"; the producer only owns the "what" (its asset).

### B.2 Facet 1 — technology

| technology      | meaning                                  | transport / wire                                   | target asset kind |
|-----------------|------------------------------------------|----------------------------------------------------|-------------------|
| `rest`          | synchronous HTTP request to a REST API   | HTTP/1.1+JSON via Kong                             | `openapi`         |
| `event.publish` | publish a domain event                   | Kafka, CloudEvents 1.0 structured, via outbox      | own `asyncapi`    |
| `mcp`           | invoke an MCP primitive (tool/resource/prompt) | MCP Streamable HTTP, stateless, via Agent Router | `mcp`         |
| `a2a`           | delegate a task to another agent         | A2A 1.0 (JSON-RPC binding) via Agent Router        | `a2a`             |

Notes:

- `event.publish` targets the CALLER's OWN asyncapi (a context only publishes
  to channels it owns). Audience is open: subscribers are declared on the
  consumer side as `subscriptions` (B.6), never listed by the producer.
- Interaction style is derived, not declared: rest/mcp/a2a = request-reply
  (a2a MAY be long-running task), event.publish = fire-and-forget.

### B.3 Facet 2 — operation

```yaml
operation:
  asset: inventory-storage/openapi   # target asset id (A.2)
  id: reserveStock                   # operationId | asyncapi op id | tool name | skill id
  effect: write                      # read | write
  # technology-specific locator (informative, validated against the asset):
  rest:  { method: POST, path: /v1/reservations }
  event: { channel: wms.inventory.stock-reserved.v1, type: com.warehouse.wms.inventory.reservation.StockReserved }
  mcp:   { primitive: tool, authority: L1 }       # tool | resource | prompt
  a2a:   { method: SendMessage, authority: L3 }   # SendMessage | SendStreamingMessage
```

Rules:

- O-1 `asset` + `id` MUST resolve in the caller's pinned copy of the
  producer asset (A-5) — CI fails on dangling references.
- O-2 `effect` MUST match the asset: OpenAPI GET/HEAD = read; MCP
  `readOnlyHint: true` = read; event.publish = write (state change fact).
- O-3 For mcp/a2a, `authority` MUST equal the producer-declared
  `com.warehouse/authority` / `authority:L<n>` tag (write => L3).
- O-4 Only the matching locator block is present (rest XOR event XOR mcp XOR a2a).

### B.4 Facet 3 — business context

The reason the call exists, in ubiquitous language of the CALLER.

```yaml
business:
  capability: inventory.allocation          # business capability (domain.capability)
  process: wave-release                     # business process / EventStorming flow
  useCase: ReleaseWave                      # application service in the caller (hexagon inbound port)
  triggers:                                 # every entry point that runs this use case
    - kind: api                             # api | event | schedule | agent
      via: rest                             # api only: rest | mcp | a2a (inbound surface of THIS context)
      ref: releaseWave                      # operationId | tool name | skill id | CE type | cron
      actor: human:wave-planner             # human:<role> | system | agent:<agent-name>
    - kind: event
      ref: com.warehouse.wes.fulfillment.wave.WavePlanned
      actor: system
  purpose: Reserve stock for every pick line before the wave is released to the floor.
  criticality: blocking                     # blocking | degradable | best-effort
  onFailure: reject-wave                    # short domain consequence
```

Rules:

- C-1 `capability`, `process`, `useCase`, `triggers[]` (each with `kind`,
  `ref`, `actor`), `purpose` are REQUIRED. `api` triggers MUST resolve in this
  repo's own openapi operationIds / primitives tools / agent-card skills. Names come from the caller's domain model, not from the
  target's API.
- C-2 `useCase` MUST name a real application service / use-case type in the
  caller (fitness test greps `internal/application`).
- C-3 `trigger.kind: agent` (an LLM decided to call) MUST use `mcp` or `a2a`
  and MUST NOT be a write unless the call goes through a human-approved act
  slice (`trigger.kind: user`, actor `human:<role>`).
- C-4 `kind: event` → `ref` MUST be a CloudEvents type that appears in the
  caller's `subscriptions`.
- C-5 `criticality: blocking` on `rest`/`mcp`/`a2a` documents a runtime
  coupling; contexts whose AGENTS.md forbid sibling calls
  (process-path-management, labor-performance) MUST have zero such entries.

### B.5 `apis/interactions.yaml`

```yaml
apiVersion: warehouse/interactions/v1
context: fulfillment-execution
interactions:
  - id: release-wave-reserve-stock          # kebab-case, unique in file
    technology: rest
    operation:
      asset: inventory-storage/openapi
      id: reserveStock
      effect: write
      rest: { method: POST, path: /v1/reservations }
    business:
      capability: fulfillment.wave-management
      process: wave-release
      useCase: ReleaseWave
      triggers: [{ kind: api, via: rest, ref: releaseWave, actor: "human:wave-planner" }]
      purpose: Hold stock for each pick line before releasing the wave.
      criticality: blocking
      onFailure: reject-wave

  - id: release-wave-publish-wave-released
    technology: event.publish
    operation:
      asset: fulfillment-execution/asyncapi
      id: publishWaveReleased
      effect: write
      event: { channel: wes.fulfillment.wave.v1, type: com.warehouse.wes.fulfillment.wave.WaveReleased }
    business:
      capability: fulfillment.wave-management
      process: wave-release
      useCase: ReleaseWave
      triggers: [{ kind: api, via: rest, ref: releaseWave, actor: "human:wave-planner" }]
      purpose: Tell the floor and labour planning that the wave is executable.
      criticality: best-effort
      onFailure: outbox-retry

  - id: diagnose-short-pick-check-availability
    technology: mcp
    operation:
      asset: inventory-storage/mcp
      id: check_availability
      effect: read
      mcp: { primitive: tool, authority: L1 }
    business:
      capability: fulfillment.exception-handling
      process: short-pick-resolution
      useCase: DiagnoseShortPick
      triggers: [{ kind: agent, ref: diagnose_short_pick, actor: "agent:warehouse-ops-agent" }]
      purpose: Check whether stock exists in another bin before raising a short pick.
      criticality: degradable
      onFailure: escalate-to-supervisor

subscriptions:
  - id: consume-inventory-storage-stock-events
    operation:
      asset: inventory-storage/asyncapi     # resolved in the PINNED producer copy (A-5)
      id: publishStockEvents                # producer's operation on the channel
      consumerOperation: consumeStockEvents # this repo's own asyncapi operation
      event:
        channel: warehouse.inventory.events
        types: [com.warehouse.wms.inventory.stock.StockAdjusted]
    business:
      capability: fulfillment.wave-management
      process: wave-planning
      useCases: [RefreshPickableStock]      # or `projection: <Port>` for a pure read model
      purpose: Keep the local pickable-stock projection current.
```

### B.6 Subscriptions (event consumption)

Consuming an event is not an invocation, so it is not an interaction; it is
declared under `subscriptions` with operation + business facets (no
technology field — it is always Kafka/CloudEvents). Every type MUST exist in
both the pinned producer copy and the consumer's own asyncapi. A subscription
that only feeds a read model names the port as `projection` instead of
`useCases` (C-2 checks it in `internal/application/ports`).

AsyncAPI 2.6 caveat (found on adoption): producers disagree on
`publish`/`subscribe` for their own channels (workforce-management uses
`subscribe`, labor-performance and process-path-management use `publish`).
Resolvers accept either verb on 2.6; AsyncAPI 3 `send`/`receive` removes
the ambiguity and is the target. This keeps
"producers do not know their consumers" while still making every edge of the
architecture map explicit, and it is what `x-no-consumer-reason` is checked
against.

### B.7 Derived architecture map

The fleet map is a GRAPH built only from these files (generator in
warehouse-docs, like `agent-catalogue.json`):

- nodes: contexts, assets, operations
- edges: interaction (caller → operation, labelled technology + business.process),
  subscription (operation → consumer), exposure (asset → gateway)

Generated outputs: `system-assets.json` (catalogue), `interactions.json`
(edges), EventCatalog pages, and drift checks (dangling operation, unused
operation, operation with `x-no-consumer-reason` that now has a consumer).

### B.8 Validation gates (per repo CI)

1. Schema-validate `system-assets.yaml` and `interactions.yaml`.
2. Every asset validates against its upstream spec (A-2).
3. Every interaction/subscription resolves against a pinned producer asset (O-1).
4. effect/authority match (O-2, O-3).
5. `useCase` exists in code (C-2); agent-write rule (C-3); sibling-call bans (C-5).
6. Code-to-declaration fitness: every outbound adapter call site (REST client
   method, outbox event type, MCP client tool name, A2A client skill) has a
   declared interaction, and vice versa — so the map cannot lie.

---

## Part D — Release folder (`system/<version>/`)

Every release of an application is described by ONE folder of plain files
(no archive) in the repo's root `system/` folder, named `<version>`
(release tag without `v` = Helm chart version), e.g. `system/2.0.5/`. Built by
`build_system_bundle.py` (beside this file, in warehouse-harness-template `tools/system-assets/`); application code is never modified, only `system/` is written.

Layout of `system/<version>/`:

```
application.yaml        Part 0, generated from ecosystem + released chart
system-assets.yaml      Part A, with a `release` block (tag, commit, chart, images, drift)
interactions.yaml       Part B, business overlay validated against this release
openapi/openapi*.yaml   from the git tag (openapi.yaml, openapi-reports.yaml, ...)
asyncapi/asyncapi.yaml  from the git tag
mcp/server-card.json    composed (bex.co / SEP-2127); version == chart version
mcp/primitives.json     live tools/resources/prompts lists of the deployed release
a2a/agent-card.json     live /.well-known/agent-card.json of the deployed release
SHA256SUMS
```

One folder per asset kind (`openapi/`, `asyncapi/`, `mcp/`, `a2a/`); the
descriptors (`application.yaml`, `system-assets.yaml`, `interactions.yaml`,
`SHA256SUMS`) sit at the top of the version folder.

Build rules:
- D-1 Inputs are pinned: `git show v<version>:<path>`, the OCI chart
  `oci://ghcr.io/iqvo/<app> --version <version>` (must exist, MC-5), and the
  live cluster ONLY after every pod behind `<app>-mcp` / `<app>-a2a` is proven
  to run the tag's commit.
- D-2 Business facets come from the repo's own `system/interactions.overlay.yaml`
  (the caller owns the "why"); every reference resolves against the release
  or the build fails, and every published and consumed event type must be mapped.
- D-3 Runtime facts that break the standard are recorded verbatim in
  `system-assets.yaml` `release.drift` (never rewritten into the artifacts);
  `--strict` turns drift into a build failure.
- D-4 Deterministic: same release + same overlay = byte-identical files. A
  rebuild replaces the whole folder (staged then swapped), so no stale file
  from an earlier build survives.

- D-5 Surfaces are optional per release: an `mcp` or `a2a` asset exists iff its Service
  (`<app>-mcp`, `<app>-a2a`) is deployed (charts default both to disabled and the platform
  values switch them on); a context with neither ships openapi + asyncapi only. The REST
  Service `<app>` is always checked to run the release commit.
- D-6 Producer contracts resolve in this order: the copy the repo pinned under an adapter
  `testdata/<producer>.<kind>.yaml` at the tag (A-5), else the producer's own contract at ITS
  latest GitHub release (local clone). MCP tools and A2A skills have no pinned copy anywhere:
  they are read live from the producer's Services at build time.
- D-7 Outbound `rest` interactions must match the producer operation (`operationId`, HTTP method
  and path, `effect` read for GET/HEAD else write); `mcp` effect must match the tool's
  `readOnlyHint`; `a2a` skill ids must be in the producer's agent card.

## Part E — Change rule: a contract change always yields a new asset

A **contract change** is any change to what a context exposes or how it
integrates: `apis/openapi*.yaml`, `apis/asyncapi.yaml` (channels, event types,
payloads), an MCP tool / resource / prompt (name, input or output schema,
annotations, authority), an A2A skill or card, a new or removed outbound call,
a new or removed consumed event.

- E-1 A contract change is only complete when the release that carries it has
  its folder `system/<version>/` on `develop`. The sequence is fixed:
  merge the change -> release (tag + chart) -> deploy -> build the folder
  (`build_system_bundle.py`) -> PR `system/<version>/` into develop.
- E-2 Never edit a committed `system/<version>/` by hand and never rebuild an
  old version over a newer deployment: a folder describes exactly one release.
  A wrong folder is fixed by rebuilding the SAME version from the same tag.
- E-3 A new or removed integration (call, published event, subscription) also
  updates `system/interactions.overlay.yaml` in the same change as the code;
  the builder's coverage check fails on an unmapped published or consumed
  event type.
- E-4 A release without a contract change still gets a folder, so the
  version history has no gaps and `release.drift` is recorded per version.
- E-5 `scripts/harness-health.py` flags a repo whose latest GitHub release has
  no `system/<version>/` folder on develop (only repos that already carry a
  `system/` folder are checked).

## Sources

- MCP Server Card governing doc (owner decision) — https://bex.co/blog/2026/07/08/mcp-server-cards-well-known-discovery
- EventCatalog data for Part 0 — IQVO/ecosystem origin/develop: `catalog.contexts.mjs`, `domains/**/index.mdx` badges

- OpenAPI 3.2.0 — https://spec.openapis.org/oas/v3.2.0.html
- AsyncAPI 3.0.0 / 3.1.0 — https://www.asyncapi.com/docs/reference/specification/v3.0.0 , https://github.com/asyncapi/spec/releases
- A2A 1.0.0 AgentCard & well-known URI — https://a2a-protocol.org/latest/specification
- MCP Server Card (SEP-2127) — https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/seps/2127-mcp-server-cards.md , https://github.com/modelcontextprotocol/ext-server-card
- MCP Registry server.json — https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/server-json/generic-server-json.md
- CloudEvents fleet standard — warehouse-docs `agents/fleet/cloudevents.md`
