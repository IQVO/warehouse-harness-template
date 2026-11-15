# Producer spec breaking-change kit — the fleet standard

A producer's `apis/openapi.yaml` and `apis/asyncapi.yaml` are pinned, byte for
byte, by consumer repos (`contracts/pinned.json`, see
[contract-testing.md](contract-testing.md)). The consumer's `pinned-contracts`
job only notices a change **after** the producer merged it. This kit fails the
**producer's** pull request instead, and names the consumers it would break.

| Spec | Tool | Where |
|---|---|---|
| `apis/openapi*.yaml` | `oasdiff breaking --fail-on ERR`, PR base vs head | CI job `spec-breaking` |
| `apis/asyncapi.yaml` | `scripts/harness/asyncapi_breaking.py` (oasdiff cannot read AsyncAPI) | CI job `spec-breaking` |

## The pitfall that makes a gate vacuous: `--fail-on ERR`

`oasdiff breaking` **prints** breaking changes and still **exits 0** (v1.38.0).
Without `--fail-on ERR` the job is green on a deleted endpoint. Verified against the
real linux v1.38.0 binary: a removed path exits 0 without the flag, 1 with it;
an additive change and an identical spec exit 0 with it. Never drop the flag.

## What `asyncapi_breaking.py` enforces

Exit 1 with one finding per breakage:

- a **channel** removed (matched by Kafka address, else by channel key);
- a **message** removed from the catalog, or removed from / moved off a channel that still exists;
- in the message payload (CloudEvents envelope **and** `data`, nested objects and array items):
  a required field removed or renamed; a required field **no longer required**; an optional field
  **became required**; the type set narrows (`number` → `integer`, `null` dropped, `nullable` dropped,
  a type swap); an `enum` / `const` appears where any value was valid, or loses values; a bound
  tightens (`minimum`, `maximum`, `exclusiveMinimum/Maximum`, `minLength`, `maxLength`, `minItems`,
  `maxItems`, `minProperties`, `maxProperties`, `multipleOf`, `uniqueItems`); a `pattern` or
  `format` appears or changes; `additionalProperties` becomes `false`;
- a payload `$ref` that resolved in the base no longer resolves.

Ownership: a message or channel the spec only **consumes** (message tag `consumed`, a channel whose operations are all
tagged `consumed`, or a 3.0 `action: receive`) documents another context's contract. A change there is printed as a
`::warning::` and never fails the PR (the producer of that contract has its own gate). Ownership is read from the **base**
spec, and anything untagged is owned, so the gate stays strict by default and a retag cannot hide a break.

Additive change passes: new message / channel / optional field, widened type, new enum value,
dropped bound, description edits. A breaking change ships as a **new `.v2` type + dataschema**,
never as a mutation of the pinned one.

Exit 2 = cannot judge (unreadable or unparseable spec): never a pass.

### Shapes it reads

| | Shape | Example |
|---|---|---|
| (a) | AsyncAPI 2.6 channel perspective: own topics under `subscribe:`, consumed under `publish:` | dock-scheduling, inventory-storage |
| (b) | AsyncAPI 2.6 application perspective: own topic under `publish:` | process-path-management |
| (c) | AsyncAPI 3.0: channels keyed by name + `address`, channel-level `messages`, with `operations` | checkout, delivery-promise |
| (d) | channel-level `messages` map, no operations | yard-management, network-inventory-planning |

Envelopes `payload.allOf[$ref CloudEvent, {properties.data: $ref}]`, an `allOf` inside `data`, and
local `$ref` chains are resolved; `allOf` branches are merged (required united, properties merged).

### Decisions where the nine pre-kit copies disagreed

- **required → optional is breaking** (dock-scheduling, yard-management, product-master flagged it;
  facility-layout and order-management tested the opposite): a reader that relied on the field breaks.
- **optional → required is breaking** (wes-work-planning); a brand-new required field is additive.
- A name in `required` that was never declared under `properties` is judged only when the listing changes
  (warehouse-planning's real spec has such names).

### Not judged (by design)

`oneOf` / `anyOf` branches, `$ref`s outside the document, message `contentType` / headers / bindings,
and examples. Add a rule in `asyncapi_breaking.py` (see below) if a real incident needs one.

## Adopting the kit in a producer repo

The rollout script copies four things and inserts one job:

1. `scripts/harness/asyncapi_breaking.py` and `scripts/harness/test_asyncapi_breaking.py` — **verbatim**.
2. `.github/requirements/asyncapi-breaking.txt` — hash-pinned PyYAML, **verbatim**.
3. The job block `templates/ci/spec-breaking.job.yml`, rendered and inserted by
   `python3 scripts/harness/spec_breaking_render.py REPO_DIR` (idempotent).

**Substitution contract.** The block has two placeholders, `ACTIONS_CHECKOUT` and
`ACTIONS_SETUP_PYTHON` (written in double braces in the block file). Each is replaced by the *entire*
`uses:` value, ref and trailing comment, of the commonest SHA-pinned `actions/checkout@<40 hex>` /
`actions/setup-python@<40 hex>` step **already in the target's `ci.yml`**, so the repo's own pin policy
wins and Dependabot keeps them current. No SHA-pinned step → exit 3, never an invented pin
(`--fallback-from FILE` borrows from a file you reviewed). **Insertion point:** directly above
`  trivy-scan:` (above its contiguous `  #` comment block, if any). A `ci.yml` that already contains
`  spec-breaking:` is left byte-identical.

Job behaviour: `pull_request` only; `fetch-depth: 0`; skips itself when the PR touches no
`apis/openapi*.yaml` / `apis/asyncapi.yaml` (diff against the PR **base sha**); the base sha / ref reach
scripts through `env:`, never `${{ }}` inside `run:`; oasdiff is the release tarball
(`oasdiff_1.38.0_linux_amd64.tar.gz`) verified against
`613037fd6a1d3362a1b742d6d86c338c0eb5ab3511f4942ce3948e78720ea2da` — the entry in the release's official
`checksums.txt` (also equal to the GitHub release asset digest and a fresh download). A new spec file
or a deleted one is handled explicitly (new: nothing to compare; deleted: fails).

**Consumer names.** A best-effort step (`continue-on-error`, never fails the job) lists the org's repos and
reads each `contracts/pinned.json@develop` through `gh api` (`GH_TOKEN: ${{ github.token }}`), collecting repos whose
pins name this producer: OpenAPI pinners for oasdiff failures, AsyncAPI pinners for event failures. The
list reaches the gate as `CONSUMERS` (the script also takes `--consumers a,b`). If the lookup fails the
message falls back to a generic sentence.

## Local use

```
python3 scripts/harness/asyncapi_breaking.py BASE.yaml HEAD.yaml [--consumers a,b]
python3 scripts/harness/asyncapi_breaking.py --check apis/asyncapi.yaml   # parse + resolve + self-compare
python3 scripts/harness/asyncapi_breaking.py --self-check
python3 scripts/harness/test_asyncapi_breaking.py
git show origin/develop:apis/asyncapi.yaml > /tmp/base.yaml && python3 scripts/harness/asyncapi_breaking.py /tmp/base.yaml apis/asyncapi.yaml
```

## Extending

1. Add the rule to `narrowing()` (per-field) or `compare_schema()` (structural) in `asyncapi_breaking.py`.
2. Add a red case to `test_asyncapi_breaking.py` first (a breaking edit that must be caught, and the additive
   twin that must stay clean).
3. Prove it on the real specs: every producer's `apis/asyncapi.yaml` compared with itself must give 0 findings
   (`--check`), because a false positive on an unchanged spec blocks every producer PR.
4. Change the **template** only; the rollout re-copies the files verbatim. Never fork the script per repo.
