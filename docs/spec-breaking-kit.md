# Producer spec breaking-change kit — the fleet standard

A producer's `apis/openapi.yaml` and `apis/asyncapi.yaml` are pinned, byte for
byte, by consumer repos (`contracts/pinned.json`, see
[contract-testing.md](contract-testing.md)). The consumer's `pinned-contracts`
job only notices a change **after** the producer merged it. This kit fails the
**producer's** pull request instead, and names the consumers it would break.

| Spec | Tool | Where |
|---|---|---|
| `apis/openapi*.yaml` | `oasdiff breaking --fail-on ERR`, PR base vs head; one severity override (a response enum value added is a warning, see below) | CI job `spec-breaking` |
| `apis/asyncapi.yaml` | `scripts/harness/asyncapi_breaking.py` (oasdiff cannot read AsyncAPI) | CI job `spec-breaking` |

## The pitfall that makes a gate vacuous: `--fail-on ERR`

`oasdiff breaking` **prints** breaking changes and still **exits 0** (v1.38.0).
Without `--fail-on ERR` the job is green on a deleted endpoint. Verified against the
real linux v1.38.0 binary: a removed path exits 0 without the flag, 1 with it;
an additive change and an identical spec exit 0 with it. Never drop the flag.

## REST: a response enum value added is a warning

**Decision (fleet owner).** A new enum value on a **response property** is a `::warning::` on the
producer's PR, not a failure. oasdiff v1.38.0 rates that check, `response-property-enum-value-added`,
as `error`; the kit lowers it to `warn`. It is the only check the kit re-rates.

**Why.** One rule for REST and events: `asyncapi_breaking.py` already passes a new enum value as
additive (see below). A reader of a fleet contract **must tolerate unknown enum values** (map them to
an "unknown" case, never reject the message or the response). With that reader rule a new value is
additive on both sides, and the REST gate no longer fails a change the event gate accepts.

**What still fails.** Everything else keeps oasdiff's own level:

| Change | oasdiff check | Gate |
|---|---|---|
| enum value **added** to a response property | `response-property-enum-value-added` | **warning** (oasdiff: error) |
| enum value **removed** from a request property / parameter / body | `request-property-enum-value-removed`, `request-parameter-enum-value-removed`, `request-body-enum-value-removed` | error |
| a request field restricted to an enum | `request-property-became-enum`, `request-parameter-became-enum`, `request-body-became-enum` | error |
| a response property's enum dropped altogether | `response-property-became-not-enum` | error |
| enum value added to a response **body that is itself an enum** (no property) | `response-mediatype-enum-value-added` | error (outside the decision: unchanged) |
| a removed path, a removed required response property, a new required request property, … | every other `error` check | error |

Not an error before or after, and not changed by the kit: oasdiff rates an enum value **removed from a
response property** (`response-property-enum-value-removed`) as `info`, which `oasdiff breaking` does not
report. The event gate is stricter there (an enum that loses values fails). `oasdiff checks changelog`
lists every check with its level.

**Mechanism.** The oasdiff step writes the override itself and passes it to the one oasdiff call:

```
printf 'response-property-enum-value-added\twarn\n' > "$RUNNER_TEMP/oasdiff-severity-levels.txt"
oasdiff breaking --fail-on ERR --severity-levels "$RUNNER_TEMP/oasdiff-severity-levels.txt" \
  "$RUNNER_TEMP/base-openapi.yaml" "$spec" > "$RUNNER_TEMP/oasdiff-breaking.txt" 2>&1 || rc=$?
```

The file format is one `<check-id><TAB><level>` line per check. There is **no managed severity file in
the repo** on purpose: a PR cannot weaken the gate by editing one, only by editing the job block, which
the kit owns (`spec_breaking_render.py --update` puts it back). `--fail-on ERR` stays, with its reason.
The report is printed to the log as before. When it lists `[response-property-enum-value-added]`, the
step also emits one annotation per spec, `::warning::<spec> adds enum value(s) to a response property
… REST clients must tolerate unknown enum values.`, from an `if grep …` that cannot fail the step. A
missing or unreadable severity file makes oasdiff exit 106, which fails the step: the gate fails closed.

**Proof (real oasdiff v1.38.0).** yard-management, `origin/main:apis/openapi.yaml` vs
`origin/develop:apis/openapi.yaml` (the diff a develop → main release PR carries):

| Command | Exit | Summary |
|---|---|---|
| `oasdiff breaking --fail-on ERR BASE HEAD` | 1 | `35 changes: 32 error, 3 warning, 0 info` (all 35 are `response-property-enum-value-added`) |
| `oasdiff breaking --fail-on ERR --severity-levels FILE BASE HEAD` | 0 | `35 changes: 0 error, 35 warning, 0 info` |
| the second command, `/healthz` deleted from HEAD | 1 | `36 changes: 1 error, 35 warning, 0 info` (`api-path-removed-without-deprecation` is the error) |

`test_spec_breaking_render.py` runs the step's own script: always against a stub `oasdiff` (the
arguments, the file it wrote, the exit code, the annotation), and against a real v1.38.0 when one is on
`PATH` (enum value added: exit 0; removed path, removed request enum value, both at once: exit 1). The
selftest downloads no oasdiff, so run the real half locally before changing the step.

**Rolling it to a repo that already has the job.** The block changed, the files did not:

```
python3 scripts/harness/spec_breaking_render.py REPO_DIR --update --dry-run   # prints the diff
python3 scripts/harness/spec_breaking_render.py REPO_DIR --update
```

The diff must touch only the `spec-breaking` block of `.github/workflows/ci.yml`.

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
   `python3 scripts/harness/spec_breaking_render.py REPO_DIR` (idempotent). A repo that already has
   the job takes a newer block with `--update` (below).

**Substitution contract.** The block has two placeholders, `ACTIONS_CHECKOUT` and
`ACTIONS_SETUP_PYTHON` (written in double braces in the block file). Each is replaced by the *entire*
`uses:` value, ref and trailing comment, of the commonest SHA-pinned `actions/checkout@<40 hex>` /
`actions/setup-python@<40 hex>` step **already in the target's `ci.yml`**, so the repo's own pin policy
wins and Dependabot keeps them current. No SHA-pinned step → exit 3, never an invented pin
(`--fallback-from FILE` borrows from a file you reviewed). **Insertion point:** directly above
`  trivy-scan:` (above its contiguous `  #` comment block, if any). A `ci.yml` that already contains
`  spec-breaking:` is left byte-identical, unless `--update` is given.

**`--update`.** `spec_breaking_render.py REPO_DIR --update [--dry-run]` replaces the existing block with
the freshly rendered one, under the same substitution contract (the pins come from the target's own
`ci.yml`), and leaves every other byte alone. The block runs from the top of the contiguous `  #` comment
lines directly above `  spec-breaking:` to the last line of the job body (indented four spaces or more)
before `  trivy-scan:`; the blank line(s) and the comment block above `  trivy-scan:` are kept. Exit 3,
nothing written, when the boundaries are ambiguous: no single `  trivy-scan:` after the job, another job
or a top-level key between the two, or `  spec-breaking:` twice. A block that is already current is left
byte-identical (`already current, unchanged`); `--dry-run` prints the unified diff; a `ci.yml` without the
job gets it inserted. The template's own `ci.yml.template` is kept in step the same way
(`--workflow .github/workflows/ci.yml.template --update`), and a test fails when it drifts.

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

The REST half, exactly as the job runs it (oasdiff v1.38.0 on `PATH`):

```
printf 'response-property-enum-value-added\twarn\n' > /tmp/oasdiff-severity-levels.txt
git show origin/develop:apis/openapi.yaml > /tmp/base-openapi.yaml
oasdiff breaking --fail-on ERR --severity-levels /tmp/oasdiff-severity-levels.txt /tmp/base-openapi.yaml apis/openapi.yaml
```

The job block and its renderer:

```
python3 scripts/harness/spec_breaking_render.py REPO_DIR [--update] [--dry-run] [--workflow REL_PATH] [--fallback-from FILE]
python3 scripts/harness/test_spec_breaking_render.py
```

## Extending

1. Add the rule to `narrowing()` (per-field) or `compare_schema()` (structural) in `asyncapi_breaking.py`.
2. Add a red case to `test_asyncapi_breaking.py` first (a breaking edit that must be caught, and the additive
   twin that must stay clean).
3. Prove it on the real specs: every producer's `apis/asyncapi.yaml` compared with itself must give 0 findings
   (`--check`), because a false positive on an unchanged spec blocks every producer PR.
4. Change the **template** only; the rollout re-copies the files verbatim. Never fork the script per repo.

A change to the job block (`templates/ci/spec-breaking.job.yml`) follows the same order: a red case in
`test_spec_breaking_render.py`, the block, `ci.yml.template` re-rendered with `--update`, then
`--update` in each adopting repo. A new severity override is a fleet decision recorded in this file, one
line in the step's `printf`, never a file in a repo.
