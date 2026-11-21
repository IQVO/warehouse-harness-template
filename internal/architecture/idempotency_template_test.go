package architecture

// Template sensor for templates/idempotency (the fleet reference test of the idempotency replay
// classes, docs/idempotency-replay-classes.md). TEMPLATE-ONLY: it runs where
// .github/workflows/ci.yml.template exists (this repo) and skips in a scaffold, like the other
// template-mode sensors. It is NOT in MANAGED and never reaches a service.
//
// Incident: order-sourcing PR #83 rendered the template as an *_integration_test.go over a real
// Postgres; its adapter held the pool, the package's TestPostgresIntegrationTestsUseTestcontainers
// flagged the pgx import (no testcontainers import in the package), and the adopter rewrote the
// adapter to hold closures. The template now asks for closures (replayOps) and names no database
// driver, so a service renders it unchanged and wires its driver in a separate file.
//
//   - TestIdempotencyTemplateNamesNoDatabaseDriver: neither rendered template file imports
//     anything outside the standard library test set, and the Postgres integration sensor
//     (postgresIntegrationViolations, the managed fitness_test.go) finds nothing in them when
//     they are named *_integration_test.go with no testcontainers sibling.
//   - TestIdempotencyTemplateCompilesAndBites: both templates render into a scratch module with a
//     toy in-memory wiring (testdata/idempotency), gofmt/vet/test are green, and the test goes red,
//     naming the right test, when the toy middleware stores a RETRYABLE answer, drops an OUTCOME
//     write, or a catalogue entry has no class. The toy is green under BOTH fixes of the in-flight
//     duplicate (TOY_SHAPE: re-read before an unstored refusal, material-flow-control cab4359; key
//     lock + re-read at the start of the unit, amr-fleet-control b8d0026), and each shape goes red on
//     TestConcurrentDuplicateReplaysTheOriginalsStoredAnswer when its re-read is removed (no-reread).

import (
	"bytes"
	"go/parser"
	"go/token"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

const idempotencyTmplDir = "../../templates/idempotency"

// idempotencyTemplates are the two rendered halves: the verbatim test and the wiring skeleton.
var idempotencyTemplates = []string{"replay_classes_test.go.tmpl", "replay_classes_wiring_test.go.tmpl"}

// idempotencyAllowedImports is every import the rendered template may carry: the standard library
// the assertions need. No database driver, no testcontainers, no module of the service.
var idempotencyAllowedImports = map[string]bool{
	"context": true, "io": true, "net/http": true, "net/http/httptest": true,
	"strings": true, "sync": true, "testing": true, "time": true,
}

func skipOutsideTemplate(t *testing.T) {
	t.Helper()
	if _, err := os.Stat("../../.github/workflows/ci.yml.template"); err != nil {
		t.Skip("template-only sensor: a scaffold has no ci.yml.template")
	}
}

func renderIdempotencyTemplate(t *testing.T, name string) string {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(idempotencyTmplDir, name))
	if err != nil {
		t.Fatalf("read %s: %v", name, err)
	}
	r := strings.NewReplacer("{{PKG}}", "fixture", "{{ROUTE}}", "/things", "{{REUSED_SLUG}}", "idempotency-key-reused")
	out := r.Replace(string(raw))
	if strings.Contains(out, "{{") {
		t.Fatalf("%s keeps a placeholder after rendering PKG/ROUTE/REUSED_SLUG", name)
	}
	return out
}

// idempotencyImportViolations lists every import of src outside the allowed standard-library set.
func idempotencyImportViolations(t *testing.T, name, src string) []string {
	t.Helper()
	f, err := parser.ParseFile(token.NewFileSet(), name, src, parser.ImportsOnly)
	if err != nil {
		t.Fatalf("%s does not parse once rendered: %v", name, err)
	}
	var out []string
	for _, imp := range f.Imports {
		path, _ := strconv.Unquote(imp.Path.Value)
		if !idempotencyAllowedImports[path] {
			out = append(out, name+" imports "+strconv.Quote(path)+": the template must stay driver-free (WHY: a service sensor flags pgx/database/sql in an *_integration_test.go without a testcontainers import in the package; FIX: take the store through the replayOps closures, wire the driver in the adopter's own file)")
		}
	}
	return out
}

func TestIdempotencyTemplateNamesNoDatabaseDriver(t *testing.T) {
	skipOutsideTemplate(t)
	dir := t.TempDir()
	for _, name := range idempotencyTemplates {
		src := renderIdempotencyTemplate(t, name)
		for _, v := range idempotencyImportViolations(t, name, src) {
			t.Error(v)
		}
		// The same file under the integration name, alone in its package: the Postgres sensor of
		// every service (fitness_test.go) must find nothing.
		target := filepath.Join(dir, strings.TrimSuffix(name, ".go.tmpl")+"_integration_test.go")
		if err := os.WriteFile(target, []byte(src), 0o600); err != nil {
			t.Fatal(err)
		}
		for _, v := range postgresIntegrationViolations(t, target) {
			t.Errorf("rendered %s as *_integration_test.go: %s", name, v)
		}
	}
}

// The driver check above must bite: the adapter shape before this fix (a pool held by the adapter)
// is reported by both the import check and the Postgres sensor.
func TestIdempotencyTemplateDriverCheckBites(t *testing.T) {
	old := "package fixture_test\n\nimport (\n\t\"testing\"\n\n\t\"github.com/jackc/pgx/v5/pgxpool\"\n)\n\ntype repoAdapter struct{ pool *pgxpool.Pool }\n\nvar _ = testing.Short\n"
	if len(idempotencyImportViolations(t, "old.go", old)) == 0 {
		t.Error("an adapter importing pgxpool passed the template import check")
	}
	path := filepath.Join(t.TempDir(), "old_integration_test.go")
	if err := os.WriteFile(path, []byte(old), 0o600); err != nil {
		t.Fatal(err)
	}
	if len(postgresIntegrationViolations(t, path)) == 0 {
		t.Error("the Postgres sensor did not flag a pgx adapter without a testcontainers sibling")
	}
}

// writeFixtureModule renders both templates and the toy wiring into a scratch module.
func writeFixtureModule(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	pkg := filepath.Join(dir, "fixture")
	if err := os.MkdirAll(pkg, 0o750); err != nil {
		t.Fatal(err)
	}
	files := map[string]string{
		"go.mod":                         "module example.com/replayfixture\n\ngo 1.22\n",
		"fixture/replay_classes_test.go": renderIdempotencyTemplate(t, "replay_classes_test.go.tmpl"),
	}
	for dst, src := range map[string]string{
		"fixture/toy_store.go":       "testdata/idempotency/toy_store.go.txt",
		"fixture/toy_wiring_test.go": "testdata/idempotency/toy_wiring_test.go.txt",
	} {
		b, err := os.ReadFile(src)
		if err != nil {
			t.Fatalf("read %s: %v", src, err)
		}
		files[dst] = string(b)
	}
	for name, body := range files {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

func goIn(t *testing.T, dir string, env []string, args ...string) (string, error) {
	t.Helper()
	cmd := exec.Command("go", args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), append([]string{"GOFLAGS=-mod=mod", "GOTOOLCHAIN=local", "GOWORK=off"}, env...)...)
	var out bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &out
	err := cmd.Run()
	return out.String(), err
}

func TestIdempotencyTemplateCompilesAndBites(t *testing.T) {
	skipOutsideTemplate(t)
	if _, err := exec.LookPath("go"); err != nil {
		t.Skip("no go binary on PATH")
	}
	dir := writeFixtureModule(t)

	// The wiring skeleton must satisfy the contract the verbatim test declares (compiles with it).
	skel := t.TempDir()
	for name, body := range map[string]string{
		"go.mod":                          "module example.com/skeleton\n\ngo 1.22\n",
		"s/replay_classes_test.go":        renderIdempotencyTemplate(t, "replay_classes_test.go.tmpl"),
		"s/replay_classes_wiring_test.go": renderIdempotencyTemplate(t, "replay_classes_wiring_test.go.tmpl"),
	} {
		p := filepath.Join(skel, name)
		if err := os.MkdirAll(filepath.Dir(p), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if out, err := goIn(t, skel, nil, "vet", "./..."); err != nil {
		t.Fatalf("the rendered template + wiring skeleton do not compile together:\n%s", out)
	}

	cmd := exec.Command("gofmt", "-l", "fixture")
	cmd.Dir = dir
	if out, err := cmd.CombinedOutput(); err != nil || len(bytes.TrimSpace(out)) != 0 {
		t.Fatalf("rendered fixture is not gofmt-clean (err=%v): %s", err, out)
	}
	if out, err := goIn(t, dir, nil, "vet", "./..."); err != nil {
		t.Fatalf("go vet of the rendered template over the toy wiring:\n%s", out)
	}
	// Both fixes of the in-flight duplicate are green (the default shape re-reads before an unstored
	// refusal; lock-at-begin re-reads under the key lock, so a copy never reaches the handler).
	for _, shape := range []string{"", "lock-at-begin"} {
		if out, err := goIn(t, dir, []string{"TOY_SHAPE=" + shape}, "test", "./...", "-count=1"); err != nil {
			t.Fatalf("the rendered template is red over a correct toy middleware (TOY_SHAPE=%q):\n%s", shape, out)
		}
	}

	const inFlight = "TestConcurrentDuplicateReplaysTheOriginalsStoredAnswer"
	for _, tc := range []struct{ mutation, shape, wantFail string }{
		{"store-retryable", "", "TestRetryableRefusalReleasesTheKeySoTheRetryExecutes"},
		{"store-retryable", "", "TestEveryProblemTypeIsReplayedOrReExecutedByItsClass"},
		{"drop-outcome", "", "TestOutcomeRefusalCommitsItsWritesOnceAndIsReplayed"},
		{"unclassified", "", "TestEveryProblemTypeDeclaresAReplayClass"},
		{"no-reread", "", inFlight},              // cab4359 shape without its re-read in finishRefused
		{"no-reread", "lock-at-begin", inFlight}, // b8d0026 shape without its re-read under the lock
	} {
		t.Run(tc.mutation+"/"+tc.shape+"/"+tc.wantFail, func(t *testing.T) {
			out, err := goIn(t, dir, []string{"TOY_MUTATION=" + tc.mutation, "TOY_SHAPE=" + tc.shape}, "test", "./...", "-count=1")
			if err == nil {
				t.Fatalf("a toy middleware that %s passed the template:\n%s", tc.mutation, out)
			}
			if !strings.Contains(out, "--- FAIL: "+tc.wantFail) {
				t.Fatalf("the toy that %s did not fail %s:\n%s", tc.mutation, tc.wantFail, out)
			}
		})
	}
}
