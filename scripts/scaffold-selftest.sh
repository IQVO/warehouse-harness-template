#!/usr/bin/env bash
# scripts/scaffold-selftest.sh
#
# Proves that a NEW scaffold built from this template enforces what it claims to:
#   1. new-service.sh runs on a copy of the template and leaves no stray placeholder in
#      the packaging files it generates (Dockerfile, .dockerignore, docs/before-first-release.md);
#   2. the scaffold's architecture package is green on the empty tree;
#   3. TestRuntimeBinaryHasNoInMemoryAdapters goes red on a cmd/ binary that imports the
#      memory adapters and green again when the import is replaced by a real adapter;
#   4. TestNoGoFilesAtRepoRoot goes red on a .go file at the root and green once it is gone;
#   5. a cmd/ directory with no binary is an error, not a silent pass.
#
# Offline: it uses the two-argument form of new-service.sh for the sensor cases
# (no CloudEvents helper, so no `go get`) and only greps the generated helper in
# the namespace cases. Run from the template root:  bash scripts/scaffold-selftest.sh
set -euo pipefail

template_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
dest="$work/selftest-svc"

fail() { echo "SELFTEST FAIL: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

arch() { go test ./internal/architecture/ -count=1 -timeout 300s "$@"; }

# expect_red <run-regex> <needle>: the sensor must fail AND say why.
expect_red() {
  local out
  if out="$(arch -run "$1" 2>&1)"; then
    echo "$out"
    fail "$1 passed but should have failed"
  fi
  grep -qF -- "$2" <<<"$out" || { echo "$out"; fail "$1 failed without saying '$2'"; }
  echo "$out" | grep -E 'ARCHITECTURE VIOLATION|WHAT:|^--- FAIL|^FAIL' || true
}

expect_green() { arch -run "$1" -v | grep -E '^(--- |ok|PASS)' ; }

step "copy the template into $dest"
mkdir -p "$dest"
(cd "$template_root" && tar --exclude=.git -cf - .) | (cd "$dest" && tar -xf -)
cd "$dest"

step "scripts/new-service.sh selftest-svc stock"
bash scripts/new-service.sh selftest stock >"$work/new-service.log" 2>&1 || { cat "$work/new-service.log"; fail "new-service.sh failed"; }
grep -E 'generated|kept|substituted' "$work/new-service.log"

[[ ! -e internal/domain/shared/gs1 ]] || fail "a default scaffold must not carry internal/domain/shared/gs1"
[[ ! -e internal/domain ]] || fail "dropping gs1 must leave no empty internal/domain behind"

for f in Dockerfile .dockerignore docs/before-first-release.md; do
  [[ -f "$f" ]] || fail "new-service.sh did not generate $f"
done
if grep -n '{{' Dockerfile .dockerignore docs/before-first-release.md; then
  fail "a placeholder is left in a generated packaging file"
fi
grep -q 'charts/selftest-svc/' docs/before-first-release.md || fail "the checklist does not name charts/selftest-svc/"
grep -q 'selftest-svc-${CHART_VERSION}.tgz' .github/workflows/ci.yml || fail "ci.yml release job does not use the service's chart name"
if grep -q 'inventory-storage-\${CHART_VERSION}' .github/workflows/ci.yml; then fail "ci.yml still packages inventory-storage-*.tgz"; fi
grep -q 'for d in cmd/\*/' Dockerfile || fail "the Dockerfile does not build every cmd/*"

step "green on the empty scaffold"
arch -run 'TestRuntimeBinary|TestNoGoFilesAtRepoRoot' -v | grep -E '^(--- |ok|PASS)|SKIP'

step "sensor 1 red: cmd/api imports the in-memory adapters"
mkdir -p cmd/api
cat >cmd/api/main.go <<'EOF'
package main

import (
	_ "github.com/IQVO/selftest-svc/internal/adapters/outbound/memory"
)

func main() {}
EOF
expect_red '^TestRuntimeBinaryHasNoInMemoryAdapters$' 'never imports the in-memory adapters'

step "sensor 1 green: cmd/api imports a real adapter"
cat >cmd/api/main.go <<'EOF'
package main

import (
	_ "github.com/IQVO/selftest-svc/internal/adapters/outbound/postgres"
)

func main() {}
EOF
expect_green '^TestRuntimeBinaryHasNoInMemoryAdapters$'

step "sensor 1: a cmd/ with no binary is an error"
rm cmd/api/main.go
expect_red '^TestRuntimeBinaryHasNoInMemoryAdapters$' 'would pass vacuously'
rm -r cmd

step "sensor 2 red: a .go file at the repository root"
printf 'package features_test\n' >features_test.go
expect_red '^TestNoGoFilesAtRepoRoot$' 'features_test.go sits next to go.mod'

step "sensor 2 green: the file moved into features/"
mkdir -p features
mv features_test.go features/features_test.go
expect_green '^TestNoGoFilesAtRepoRoot$'

step "WITH_GS1=1 keeps the GS1 package, and it builds, tests and stays byte-identical in the scaffold"
gs1_dest="$work/selftest-gs1"
mkdir -p "$gs1_dest"
(cd "$template_root" && tar --exclude=.git -cf - .) | (cd "$gs1_dest" && tar -xf -)
(cd "$gs1_dest" && WITH_GS1=1 bash scripts/new-service.sh selftest-gs1 stock >"$work/new-service-gs1.log" 2>&1) \
  || { cat "$work/new-service-gs1.log"; fail "new-service.sh with WITH_GS1=1 failed"; }
[[ -d "$gs1_dest/internal/domain/shared/gs1" ]] || fail "WITH_GS1=1 did not keep the gs1 package"
(cd "$gs1_dest" && go vet ./internal/domain/... && go test ./internal/domain/... -count=1) \
  || fail "the gs1 package does not build/test inside a scaffold"
diff -r "$template_root/internal/domain/shared/gs1" "$gs1_dest/internal/domain/shared/gs1" >/dev/null \
  || fail "the scaffolded gs1 package differs from the template's"

step "warehouse namespace (default): the CloudEvents helper keeps the warehouse prefixes"
wh_dest="$work/selftest-wh"
mkdir -p "$wh_dest"
(cd "$template_root" && tar --exclude=.git -cf - .) | (cd "$wh_dest" && tar -xf -)
(cd "$wh_dest" && bash scripts/new-service.sh selftest-wh stock wms inventory-storage >"$work/new-service-wh.log" 2>&1) \
  || { cat "$work/new-service-wh.log"; fail "new-service.sh with the warehouse namespace failed"; }
wh_ce="$wh_dest/internal/adapters/kafka/cloudevents/cloudevents.go"
[[ -f "$wh_ce" ]] || fail "warehouse scaffold: no cloudevents.go generated"
grep -q 'com.warehouse.wms.' "$wh_ce" || fail "warehouse scaffold: type prefix is not com.warehouse.wms."
grep -q '"/warehouse/" + Repo' "$wh_ce" || fail "warehouse scaffold: Source is not /warehouse/<repo>"
grep -q 'urn:warehouse:' "$wh_ce" || fail "warehouse scaffold: dataschema is not urn:warehouse:"
grep -q 'Subdomain = "wms"' "$wh_ce" || fail "warehouse scaffold: the constant is not Subdomain"
if grep -rn '{{' "$wh_dest/internal/adapters/kafka/cloudevents/"; then
  fail "warehouse scaffold: a placeholder is left in the CloudEvents helper"
fi

step "retailnetwork namespace: the CloudEvents helper uses the network prefixes"
rn_dest="$work/selftest-rn"
mkdir -p "$rn_dest"
(cd "$template_root" && tar --exclude=.git -cf - .) | (cd "$rn_dest" && tar -xf -)
(cd "$rn_dest" && bash scripts/new-service.sh selftest-rn order storefront checkout retailnetwork >"$work/new-service-rn.log" 2>&1) \
  || { cat "$work/new-service-rn.log"; fail "new-service.sh with the retailnetwork namespace failed"; }
rn_ce_dir="$rn_dest/internal/adapters/kafka/cloudevents"
rn_ce="$rn_ce_dir/cloudevents.go"
[[ -f "$rn_ce" ]] || fail "retailnetwork scaffold: no cloudevents.go generated"
grep -q 'com.retailnetwork.storefront.' "$rn_ce" || fail "retailnetwork scaffold: type prefix is not com.retailnetwork.storefront."
grep -q '/retailnetwork/' "$rn_ce" || fail "retailnetwork scaffold: Source is not /retailnetwork/<context>"
grep -q 'Source  = "/retailnetwork/" + Context' "$rn_ce" || fail "retailnetwork scaffold: Source must key on Context, not Repo"
grep -q 'urn:retailnetwork:' "$rn_ce" || fail "retailnetwork scaffold: dataschema is not urn:retailnetwork:"
grep -q 'Tier = "storefront"' "$rn_ce" || fail "retailnetwork scaffold: the constant is not Tier"
if grep -q 'com.warehouse' "$rn_ce" "$rn_ce_dir/cloudevents_test.go"; then
  fail "retailnetwork scaffold still mentions com.warehouse"
fi
if grep -rn '{{' "$rn_ce_dir"; then
  fail "retailnetwork scaffold: a placeholder is left in the CloudEvents helper"
fi

step "retailnetwork namespace: an invalid tier is rejected with a clear error"
bad_dest="$work/selftest-rn-bad"
mkdir -p "$bad_dest"
(cd "$template_root" && tar --exclude=.git -cf - .) | (cd "$bad_dest" && tar -xf -)
if (cd "$bad_dest" && bash scripts/new-service.sh selftest-rn-bad order bogus checkout retailnetwork >"$work/new-service-rn-bad.log" 2>&1); then
  fail "new-service.sh accepted 'bogus' as a retailnetwork tier"
fi
grep -q 'commerce|logistics|partner|storefront' "$work/new-service-rn-bad.log" \
  || { cat "$work/new-service-rn-bad.log"; fail "the invalid-tier rejection does not name the valid tiers"; }
[[ ! -e "$bad_dest/internal/adapters/kafka/cloudevents" ]] || fail "a rejected tier still generated the CloudEvents helper"

echo
echo "PASS: scaffold self-test (new-service.sh output, both sensors red then green, both CloudEvents namespaces)"
