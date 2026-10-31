#!/usr/bin/env bash
# scripts/new-service.sh <service-name> <richest-domain-aggregate-pkg> [<event-subdomain> <event-context> [<event-namespace>]]
#
# Instantiates warehouse-harness-template into the CURRENT working
# directory (run this FROM the new repo's checkout, with this template
# repo cloned somewhere else so you can reference/copy from it -- this
# script assumes it is itself being run from a COPY of this template's
# files already placed into the new repo, e.g. via
#   git clone https://github.com/IQVO/warehouse-harness-template <new-repo>
#   cd <new-repo> && rm -rf .git && git init
#   bash scripts/new-service.sh inventory stock
#
# Substitutes:
#   {{SERVICE}}            -> <service-name>          (Postgres user/db, image build tag stem)
#   {{SERVICE_REPO}}        -> current directory's basename (the actual repo/GitHub name)
#   {{RICHEST_AGGREGATE}}   -> <richest-domain-aggregate-pkg>
#   {{EVENT_SUBDOMAIN}}     -> <event-subdomain>  (warehouse: wms | wes;
#                              retailnetwork: the TIER, one of
#                              commerce | logistics | partner | storefront)
#   {{EVENT_CONTEXT}}       -> <event-context>    (bounded-context segment of
#                              the CloudEvents `type`)
#   {{EVENT_NAMESPACE}}     -> <event-namespace>  (warehouse | retailnetwork;
#                              defaults to warehouse when omitted)
#
# CloudEvents (fleet-mandatory, see .claude/rules/integration-events.md):
# when <event-subdomain> and <event-context> are given, the service is
# Kafka-capable and the script generates
# internal/adapters/kafka/cloudevents/{cloudevents.go,cloudevents_test.go}
# from templates/cloudevents/*.tmpl -- the ONLY envelope helper a Kafka
# publisher/consumer may use. Two namespaces exist:
#   warehouse      (default): type com.warehouse.<subdomain>.<context>...,
#                  Source "/warehouse/" + Repo, dataschema urn:warehouse:<repo>:...
#                  e.g. bash scripts/new-service.sh inventory stock wms inventory-storage
#   retailnetwork: type com.retailnetwork.<tier>.<context>..., Source
#                  "/retailnetwork/" + Context, dataschema
#                  urn:retailnetwork:<context>:... (the network uses the
#                  CONTEXT in source and urn, the warehouse fleet the REPO)
#                  e.g. bash scripts/new-service.sh customer-ordering order commerce customer-ordering retailnetwork
# Then run:
#   go get github.com/cloudevents/sdk-go/v2@latest github.com/segmentio/kafka-go
#   go test ./internal/adapters/kafka/cloudevents/...
# Omit both event args for a service with no Kafka I/O (and delete
# .claude/rules/integration-events.md). There is NO envelope toggle to
# configure, ever.
#
# Does NOT and cannot fill in:
#   - .claude/rules/*.md content (domain model, REST routes, event
#     contracts) -- these are placeholders by design; an instantiated repo
#     with no real domain code yet has nothing true to fill them with.
#   - .gremlins.yaml's efficacy/mutant-coverage thresholds -- these MUST
#     be measured against real code (`make mutation-full`) before they
#     mean anything; the placeholders are left in place and `make
#     mutation` will fail loudly with an unhelpful gremlins config error
#     until you replace them, which is the point (better a loud failure
#     than a silently-accepted 0% threshold).
#   - go.mod's module path / real dependency versions -- run `go mod init
#     github.com/IQVO/<repo>` yourself, then `go get` the pinned
#     versions HARNESS.md references (arch-go, testcontainers, gremlins).
#
# After running this script:
#   1. grep -rn '{{' .   -- confirm zero remaining placeholders except the
#      two documented above ({{MEASURED_*}} in .gremlins.yaml).
#   2. Fill in .claude/rules/*.md for real once the domain exists.
#   3. Write real domain/application/adapters code, then:
#        go mod init github.com/IQVO/<repo>
#        go get github.com/arch-go/arch-go@v1.7.0
#        go test ./internal/architecture/... -v   # should PASS trivially on an empty tree
#   4. make mutation-full, read the measured efficacy/mutant-coverage,
#      set .gremlins.yaml's thresholds strictly BELOW those numbers.
#   5. Record "harness-template: v4" in this repo's AGENTS.md, and (Kafka
#      services) the "Events: CloudEvents 1.0 is MANDATORY" section from
#      .claude/rules/integration-events.md in its CLAUDE.md/AGENTS.md.
#   6. lefthook install
#   7. Work through docs/before-first-release.md (generated) BEFORE the first
#      develop -> main PR: Dockerfile/.dockerignore are already generated, the
#      Helm chart and its chart tests are yours to write from the closest
#      sibling (IQVO/pick-face-replenishment, IQVO/task-interleaving).
#
# GS1 identification package (internal/domain/shared/gs1: GTIN, SSCC, mod-10, scan parser):
# shared domain code for contexts that identify products or cartons. It is NOT part of a default
# scaffold (dead code in most services); keep it with
#   WITH_GS1=1 bash scripts/new-service.sh <args>
# An existing service adopts it with `python3 tools/migrate_v3.py --repo <svc> --with-gs1`. Either
# way the copy is byte-identical to the template's and is never edited in the service.
#
# Two sensors ship in internal/architecture/ and bite from the first commit:
#   - TestRuntimeBinaryHasNoInMemoryAdapters: cmd/** never imports
#     internal/adapters/outbound/memory (test doubles only; the fleet runs on
#     real Postgres and Kafka). Skips only while cmd/ does not exist.
#   - TestNoGoFilesAtRepoRoot: no .go file at the module root; the godog
#     runner lives in features/.
set -euo pipefail

if [[ $# -ne 2 && $# -ne 4 && $# -ne 5 ]]; then
  echo "usage: $0 <service-name> <richest-domain-aggregate-pkg> [<event-subdomain> <event-context> [<event-namespace>]]" >&2
  echo "  example (warehouse fleet): $0 inventory stock wms inventory-storage" >&2
  echo "  example (retail network):  $0 customer-ordering order commerce customer-ordering retailnetwork" >&2
  exit 1
fi

SERVICE="$1"
RICHEST_AGGREGATE="$2"
EVENT_SUBDOMAIN="${3:-}"
EVENT_CONTEXT="${4:-}"
EVENT_NAMESPACE="${5:-warehouse}"
SERVICE_REPO="$(basename "$(pwd)")"

# Namespace-specific CloudEvents shape. The warehouse fleet keys source and
# dataschema on the REPO name; the retail network keys them on the CONTEXT and
# names its subdomain segment a Tier. Both keep the Repo and Context constants.
case "${EVENT_NAMESPACE}" in
  warehouse)
    if [[ -n "${EVENT_SUBDOMAIN}" && "${EVENT_SUBDOMAIN}" != "wms" && "${EVENT_SUBDOMAIN}" != "wes" ]]; then
      echo "error: <event-subdomain> must be wms or wes (fleet Event Standard), got '${EVENT_SUBDOMAIN}'" >&2
      exit 1
    fi
    SUBDOMAIN_CONST_NAME="Subdomain"
    SUBDOMAIN_COMMENT="Subdomain is wms or wes (see the fleet Event Standard's subdomain table)."
    SOURCE_SEGMENT="Repo"
    DATASCHEMA_SEGMENT="Repo"
    TYPE_SEGMENT_DOC="subdomain"
    DATASCHEMA_SEGMENT_DOC="repo"
    ;;
  retailnetwork)
    case "${EVENT_SUBDOMAIN}" in
      commerce|logistics|partner|storefront) ;;
      *)
        echo "error: with <event-namespace> 'retailnetwork', <event-subdomain> is the network tier and must be one of commerce|logistics|partner|storefront, got '${EVENT_SUBDOMAIN}'" >&2
        exit 1
        ;;
    esac
    SUBDOMAIN_CONST_NAME="Tier"
    SUBDOMAIN_COMMENT="Tier is the retail-network business-area segment: commerce, logistics, partner or storefront."
    NAMESPACE_SUMMARY="Event types are com.${EVENT_NAMESPACE}.${EVENT_SUBDOMAIN}.${EVENT_CONTEXT}.<entity>.<EventName>, source /${EVENT_NAMESPACE}/${EVENT_CONTEXT}, dataschema urn:${EVENT_NAMESPACE}:${EVENT_CONTEXT}:<stream>:<EventName>:v<version>."
    SOURCE_SEGMENT="Context"
    DATASCHEMA_SEGMENT="Context"
    TYPE_SEGMENT_DOC="tier"
    DATASCHEMA_SEGMENT_DOC="context"
    ;;
  *)
    echo "error: <event-namespace> must be warehouse or retailnetwork, got '${EVENT_NAMESPACE}'" >&2
    exit 1
    ;;
esac

echo "==> instantiating warehouse-harness-template"
echo "    SERVICE            = ${SERVICE}"
echo "    SERVICE_REPO       = ${SERVICE_REPO}  (from current directory name)"
echo "    RICHEST_AGGREGATE  = ${RICHEST_AGGREGATE}"
echo

# ci.yml ships as ci.yml.template (NOT .yml) in this template repo so
# GitHub Actions never tries to execute it here -- a template with no real
# service code would fail almost every job (no Dockerfile, no
# apis/openapi.yaml, no charts, no web/), which is a meaningless CI signal
# for a template rather than the actual thing worth verifying (that
# instantiation + the fitness tests work, which scripts/new-service.sh and
# `go test ./internal/architecture/... -v` already prove). Activate it now.
if [[ -f .github/workflows/ci.yml.template ]]; then
  mv .github/workflows/ci.yml.template .github/workflows/ci.yml
  echo "  activated .github/workflows/ci.yml (was ci.yml.template)"
fi

# selftest.yml is the TEMPLATE's own gate (hook, harness and scaffold self-tests). A service that
# keeps it runs template-only jobs and trips repo_lint R5 on its {{PLACEHOLDERS}} comment
# (inbound-receiving, product-master and slotting-optimization inherited it), so drop it here.
if [[ -f .github/workflows/selftest.yml ]]; then
  rm .github/workflows/selftest.yml
  echo "  removed .github/workflows/selftest.yml (template-only gate)"
fi

# GS1 package: only on request (see the header comment). The template ships it so that its own
# `make check` covers it; a scaffold drops it unless WITH_GS1=1.
gs1_dir="internal/domain/shared/gs1"
if [[ "${WITH_GS1:-0}" == "1" ]]; then
  echo "  kept ${gs1_dir} (WITH_GS1=1; byte-identical to the template, do not edit)"
elif [[ -d "${gs1_dir}" ]]; then
  rm -r "${gs1_dir}"
  rmdir internal/domain/shared internal/domain 2>/dev/null || true
  echo "  dropped ${gs1_dir} (WITH_GS1=1 keeps it)"
fi

# CloudEvents 1.0 helper (fleet-mandatory envelope). Generated only for a
# Kafka-capable service; nothing here offers a flat/dual alternative.
if [[ -n "${EVENT_SUBDOMAIN}" ]]; then
  ce_dir="internal/adapters/kafka/cloudevents"
  mkdir -p "${ce_dir}"
  # {{NAMESPACE_SUMMARY}} documents the full type/source/dataschema prefixes in
  # the generated helper; the warehouse namespace has no such line (its output
  # stays byte-identical to the pre-namespace template), so it is deleted there.
  namespace_sed=(-e "s|{{NAMESPACE_SUMMARY}}|${NAMESPACE_SUMMARY:-}|g")
  if [[ "${EVENT_NAMESPACE}" == "warehouse" ]]; then
    namespace_sed=(-e "/{{NAMESPACE_SUMMARY}}/d")
  fi
  for f in cloudevents.go cloudevents_test.go; do
    sed \
      -e "s|{{SERVICE_REPO}}|${SERVICE_REPO}|g" \
      -e "s|{{EVENT_NAMESPACE}}|${EVENT_NAMESPACE}|g" \
      -e "s|{{EVENT_SUBDOMAIN}}|${EVENT_SUBDOMAIN}|g" \
      -e "s|{{EVENT_CONTEXT}}|${EVENT_CONTEXT}|g" \
      -e "s|{{SUBDOMAIN_CONST_NAME}}|${SUBDOMAIN_CONST_NAME}|g" \
      -e "s|{{SUBDOMAIN_COMMENT}}|${SUBDOMAIN_COMMENT}|g" \
      -e "s|{{SOURCE_SEGMENT}}|${SOURCE_SEGMENT}|g" \
      -e "s|{{DATASCHEMA_SEGMENT}}|${DATASCHEMA_SEGMENT}|g" \
      -e "s|{{TYPE_SEGMENT_DOC}}|${TYPE_SEGMENT_DOC}|g" \
      -e "s|{{DATASCHEMA_SEGMENT_DOC}}|${DATASCHEMA_SEGMENT_DOC}|g" \
      "${namespace_sed[@]}" \
      "templates/cloudevents/${f}.tmpl" > "${ce_dir}/${f}"
    echo "  generated ${ce_dir}/${f} (CloudEvents type prefix com.${EVENT_NAMESPACE}.${EVENT_SUBDOMAIN}.${EVENT_CONTEXT})"
  done
  gofmt -w "${ce_dir}" 2>/dev/null || true
else
  echo "  no <event-subdomain>/<event-context>: skipping CloudEvents helper (service has no Kafka I/O)"
fi

# Packaging (fleet-mandatory before the first release): the Dockerfile and
# .dockerignore are name-independent and generated verbatim from
# templates/packaging/. The Helm chart is NOT generated (it is shaped by the
# consumer groups / MCP / analytics / frontend the service has); instead the
# "Before the first release" checklist is generated into docs/ and names every
# packaging item with the sensor or CI job that guards it. Existing files are
# never overwritten.
pkg_dir="templates/packaging"
if [[ -d "${pkg_dir}" ]]; then
  for pair in "Dockerfile.tmpl:Dockerfile" "dockerignore.tmpl:.dockerignore"; do
    src="${pkg_dir}/${pair%%:*}"
    dst="${pair##*:}"
    if [[ -e "${dst}" ]]; then
      echo "  kept existing ${dst}"
    else
      cp "${src}" "${dst}"
      echo "  generated ${dst}"
    fi
  done
  mkdir -p docs
  checklist="docs/before-first-release.md"
  if [[ -e "${checklist}" ]]; then
    echo "  kept existing ${checklist}"
  else
    sed \
      -e "s/{{SERVICE_REPO}}/${SERVICE_REPO}/g" \
      -e "s/{{SERVICE}}/${SERVICE}/g" \
      "${pkg_dir}/before-first-release.md.tmpl" > "${checklist}"
    echo "  generated ${checklist} (packaging checklist: chart, chart tests, secrets)"
  fi
else
  echo "::warning:: ${pkg_dir} not found, skipping Dockerfile/.dockerignore/checklist"
fi

# .gremlins.yaml's {{MEASURED_*}} placeholders are deliberately NOT
# substituted here -- see the header comment above.
files_to_substitute=(
  Makefile
  .github/workflows/ci.yml
  internal/architecture/architecture_test.go
)

for f in "${files_to_substitute[@]}"; do
  if [[ ! -f "$f" ]]; then
    echo "::warning:: ${f} not found, skipping"
    continue
  fi
  sed -i.bak \
    -e "s/{{SERVICE_REPO}}/${SERVICE_REPO}/g" \
    -e "s/{{SERVICE}}/${SERVICE}/g" \
    -e "s/{{RICHEST_AGGREGATE}}/${RICHEST_AGGREGATE}/g" \
    "$f"
  rm -f "${f}.bak"
  echo "  substituted ${f}"
done

echo
echo "==> remaining placeholders (expected: only .gremlins.yaml's {{MEASURED_*}}, and any .claude/rules/*.md skeleton markers):"
grep -rn '{{' . --include='*.yaml' --include='*.yml' --include='Makefile' --include='*.go' --include='*.md' 2>/dev/null || echo "  (none found)"

echo
echo "==> next steps: see this script's own header comment, or HARNESS.md's"
echo "    'Instantiating this template' section."
