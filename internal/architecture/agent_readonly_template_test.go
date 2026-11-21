package architecture

// Template sensor for templates/architecture/agent_readonly_* (the fleet's read-only agent surface
// sensor, ported from material-flow-control 2556afc and amr-fleet-control 52797e0). TEMPLATE-ONLY:
// it skips in a scaffold (no .github/workflows/ci.yml.template), like idempotency_template_test.go,
// and it is NOT in MANAGED.
//
//   - TestAgentReadOnlyTemplateCompilesAndBites renders the verbatim pair, a fixture config and a
//     toy service tree (testdata/agentreadonly) into a scratch module: gofmt, vet and test are
//     green. Each of the five gaps of the asrs-control 007bf7b original (G1 a qualifier matched by
//     local name, G2 an alias or dot import, G3 a write method on an allowlisted constructor's
//     value, G4 pgx raw SQL, G5 a driver import), plus a forbidden import, a widened Reads seam and
//     a missing cmd/ directory, planted in the tree turns TestAgentSurfacesAreReadOnly red, saying
//     why.
//   - The same module with the sensor's own code regressed (local-name qualifiers, alias refusal
//     removed, the methods half unplugged, the driver ban emptied) turns the template's bite tests
//     red: the bite fixtures are what keeps the five gaps closed.
//   - TestAgentReadOnlyConfigSkeletonCompiles: the config skeleton compiles with the pair and the
//     bite tests pass on it, so an adopter starts from a sensor that already proves itself.

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

const agentTmplDir = "../../templates/architecture"

var agentTemplates = []string{"agent_readonly_fitness_test.go", "agent_readonly_methods_fitness_test.go"}

// writeAgentModule renders the pair plus config into a scratch module holding the toy tree; extra
// maps a module-relative path to a file body added (or replaced) on top.
func writeAgentModule(t *testing.T, config string, extra map[string]string) string {
	t.Helper()
	dir := t.TempDir()
	files := map[string]string{
		"go.mod": "module example.com/agentfixture\n\ngo 1.23\n",
		"internal/architecture/agent_readonly_config_test.go": config,
		"internal/architecture/modulepath_test.go":            readAgentFile(t, "testdata/agentreadonly/modulepath_test.go.txt"),
	}
	for _, name := range agentTemplates {
		files["internal/architecture/"+name] = readAgentFile(t, filepath.Join(agentTmplDir, name+".tmpl"))
	}
	tree := "testdata/agentreadonly/tree"
	err := filepath.WalkDir(tree, func(path string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		rel, _ := filepath.Rel(tree, path)
		files[strings.TrimSuffix(rel, ".txt")] = readAgentFile(t, path)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	for name, body := range extra {
		files[name] = body
	}
	for name, body := range files {
		if body == "" { // an empty body deletes the file (a missing surface directory)
			continue
		}
		p := filepath.Join(dir, name)
		if err := os.MkdirAll(filepath.Dir(p), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

func readAgentFile(t *testing.T, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if strings.Contains(string(b), "{{") {
		t.Fatalf("%s carries a {{placeholder}}: the agent read-only templates are verbatim", path)
	}
	return string(b)
}

func agentGo(t *testing.T, dir string, args ...string) (string, error) {
	t.Helper()
	return goIn(t, dir, nil, append(args, "./internal/architecture/")...)
}

// fixtureFile is a planted source file importing the toy module's packages.
func fixtureFile(pkg, imports, body string) string {
	return "package " + pkg + "\n\nimport (\n" + imports + ")\n\n" + body
}

const (
	fxUsecases = "\t\"example.com/agentfixture/internal/application/usecases\"\n"
	fxPostgres = "\t\"example.com/agentfixture/internal/adapters/outbound/postgres\"\n"
)

func TestAgentReadOnlyTemplateCompilesAndBites(t *testing.T) {
	skipOutsideTemplate(t)
	config := readAgentFile(t, "testdata/agentreadonly/config_test.go.txt")
	dir := writeAgentModule(t, config, nil)
	gofmt := exec.Command("gofmt", "-l", "internal/architecture")
	gofmt.Dir = dir
	if out, err := gofmt.CombinedOutput(); err != nil || strings.TrimSpace(string(out)) != "" {
		t.Fatalf("the rendered agent read-only templates are not gofmt-clean (err=%v): %s", err, out)
	}
	if out, err := agentGo(t, dir, "vet"); err != nil {
		t.Fatalf("go vet of the rendered templates over the fixture config:\n%s", out)
	}
	if out, err := agentGo(t, dir, "test", "-count=1"); err != nil {
		t.Fatalf("the rendered sensor is red over a read-only toy service:\n%s", out)
	}

	for _, tc := range []struct {
		gap, file, body, want string
	}{
		{"G1 aliased write use case", "cmd/mcp/extra.go", fixtureFile("main", "\tuc \"example.com/agentfixture/internal/application/usecases\"\n", "var _ = uc.NewService\n"), "references uc.NewService (usecases)"},
		{"G2 dot import", "internal/adapters/inbound/a2a/extra.go", fixtureFile("a2a", "\t. \"example.com/agentfixture/internal/application/usecases\"\n", "var _ = NewService\n"), "dot-imports example.com/agentfixture/internal/application/usecases"},
		{"G3 write method on an allowlisted constructor's value", "cmd/a2a/extra.go", fixtureFile("main", fxPostgres, "func f() { _ = postgres.NewRobots(nil).Save(nil, nil) }\n"), "selects postgres.NewRobots(nil).Save"},
		{"G3 repo write verb on the reader", "cmd/mcp/extra.go", fixtureFile("main", fxPostgres+fxUsecases, "func f() { _ = usecases.NewReader(postgres.NewRobots(nil)).RequestLeg }\n"), "RequestLeg is a write method"},
		{"G4 raw SQL through the read pool", "cmd/mcp/extra.go", fixtureFile("main", fxPostgres, "func f() { p, _ := postgres.NewReadOnlyPool(nil, \"\"); _, _ = p.Query(nil, \"DELETE FROM robots\") }\n"), "selects p.Query"},
		{"G5 driver import", "internal/adapters/inbound/mcp/extra.go", fixtureFile("mcp", "\t_ \"github.com/jackc/pgx/v5/pgxpool\"\n", ""), "a raw database driver"},
		{"forbidden import", "internal/adapters/inbound/mcp/extra.go", fixtureFile("mcp", "\t_ \"github.com/segmentio/kafka-go\"\n", ""), "imports github.com/segmentio/kafka-go: Kafka"},
		{"sibling adapter", "cmd/a2a/extra.go", fixtureFile("main", "\t_ \"example.com/agentfixture/internal/adapters/inbound/mcp\"\n", ""), "the agent adapters are siblings"},
		{"widened Reads seam", "internal/adapters/inbound/a2a/agent.go", "package a2a\n\ntype Reads interface {\n\tFleet()\n\tRobot()\n\tCancelLeg()\n}\n", "Reads declares CancelLeg,Fleet,Robot"},
		{"a missing cmd/ directory", "cmd/a2a/main.go", "", "the a2a surface needs both"},
	} {
		t.Run(tc.gap, func(t *testing.T) {
			out, err := agentGo(t, writeAgentModule(t, config, map[string]string{tc.file: tc.body}), "test", "-count=1", "-run", "TestAgentSurfacesAreReadOnly")
			if err == nil {
				t.Fatalf("the sensor passed a tree with %s:\n%s", tc.gap, out)
			}
			if !strings.Contains(out, tc.want) {
				t.Fatalf("the sensor failed on %s without saying %q:\n%s", tc.gap, tc.want, out)
			}
		})
	}
}

// The bite tests must catch a regression of the sensor itself: each mutant re-opens one gap.
func TestAgentReadOnlyTemplateBitesCatchSensorRegressions(t *testing.T) {
	skipOutsideTemplate(t)
	config := readAgentFile(t, "testdata/agentreadonly/config_test.go.txt")
	engine := "internal/architecture/agent_readonly_fitness_test.go"
	methods := "internal/architecture/agent_readonly_methods_fitness_test.go"
	for _, m := range []struct {
		gap, file, from, to, wantFail string
	}{
		{"G1 local-name qualifiers", engine, "\t\t\tkey = guardedKey(path)\n", "\t\t\tkey, _ = pkg.Name, path\n", "TestAgentReadOnlySensorBites"},
		{"G2 alias and dot imports accepted", engine, "\tkey := guardedKey(path)\n\tswitch {", "\tkey := guardedKey(path)\n\t_ = local\n\tswitch {\n\tcase true:\n\t\treturn \"\"", "TestAgentReadOnlySensorBites"},
		{"G3/G4 the methods half unplugged", engine, "\tout = append(out, agentWriteMethodViolations(f, names)...)\n", "", "TestAgentReadOnlySensorRefusesWriteMethodsAndRawDrivers"},
		{"G4 raw-SQL verbs dropped", methods, "\t\"Query\", \"QueryRow\", \"Acquire\",", "\t\"Acquire\",", "TestAgentReadOnlySensorRefusesWriteMethodsAndRawDrivers"},
		{"G5 driver ban emptied", methods, "{\"github.com/jackc/pgx\", \"database/sql\"}", "{}", "TestAgentReadOnlySensorRefusesWriteMethodsAndRawDrivers"},
	} {
		t.Run(m.gap, func(t *testing.T) {
			src := readAgentFile(t, filepath.Join(agentTmplDir, filepath.Base(m.file)+".tmpl"))
			if !strings.Contains(src, m.from) {
				t.Fatalf("mutant %q: %q is not in %s any more; update this selftest with the template", m.gap, m.from, m.file)
			}
			dir := writeAgentModule(t, config, map[string]string{m.file: strings.Replace(src, m.from, m.to, 1)})
			out, err := agentGo(t, dir, "test", "-count=1", "-run", "Bites|RefusesWriteMethods")
			if err == nil || !strings.Contains(out, "--- FAIL: "+m.wantFail) {
				t.Fatalf("the sensor regressed (%s) and %s stayed green (err=%v):\n%s", m.gap, m.wantFail, err, out)
			}
		})
	}
}

func TestAgentReadOnlyConfigSkeletonCompiles(t *testing.T) {
	skipOutsideTemplate(t)
	dir := writeAgentModule(t, readAgentFile(t, filepath.Join(agentTmplDir, "agent_readonly_config_test.go.tmpl")), nil)
	if out, err := agentGo(t, dir, "vet"); err != nil {
		t.Fatalf("the config skeleton does not compile with the verbatim pair:\n%s", out)
	}
	if out, err := agentGo(t, dir, "test", "-count=1", "-run", "Bites|RefusesWriteMethods|MustBeBuilt"); err != nil {
		t.Fatalf("the bite tests are red on the config skeleton:\n%s", out)
	}
}
