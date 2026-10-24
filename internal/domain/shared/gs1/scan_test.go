package gs1_test

import (
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/claudioed/warehouse-harness-template/internal/domain/shared/gs1"
)

const gsep = "\x1d" // ASCII GS, the FNC1 separator a scanner emits after a variable-length AI

func mustGTIN(t testing.TB, s string) gs1.GTIN {
	t.Helper()
	g, err := gs1.ParseGTIN(s)
	if err != nil {
		t.Fatalf("fixture GTIN %q: %v", s, err)
	}
	return g
}

func mustSSCC(t testing.TB, s string) gs1.SSCC {
	t.Helper()
	v, err := gs1.ParseSSCC(s)
	if err != nil {
		t.Fatalf("fixture SSCC %q: %v", s, err)
	}
	return v
}

func TestParseScanGS1128(t *testing.T) {
	const sscc = "376104250021234569"
	tests := []struct {
		name string
		in   string
		want func(t *testing.T) gs1.Scan
	}{
		{"case gtin only", "]C1" + "01" + "13041090004824", func(t *testing.T) gs1.Scan {
			return gs1.Scan{Symbology: gs1.SymbologyGS1128, GTIN: mustGTIN(t, "13041090004824")}
		}},
		{"gtin expiry lot (lot last, no separator needed)", "]C1" + "01" + "13041090004824" + "17" + "261231" + "10" + "LOT-42", func(t *testing.T) gs1.Scan {
			return gs1.Scan{Symbology: gs1.SymbologyGS1128, GTIN: mustGTIN(t, "13041090004824"),
				Expiry: gs1.Date{Year: 2026, Month: time.December, Day: 31}, Lot: "LOT-42"}
		}},
		{"gtin lot separator expiry", "]C1" + "01" + "03041090004827" + "10" + "AB12" + gsep + "17" + "270630", func(t *testing.T) gs1.Scan {
			return gs1.Scan{Symbology: gs1.SymbologyGS1128, GTIN: mustGTIN(t, "03041090004827"),
				Lot: "AB12", Expiry: gs1.Date{Year: 2027, Month: time.June, Day: 30}}
		}},
		{"sscc only", "]C1" + "00" + sscc, func(t *testing.T) gs1.Scan {
			return gs1.Scan{Symbology: gs1.SymbologyGS1128, SSCC: mustSSCC(t, sscc)}
		}},
		{"sscc with content and count", "]C1" + "00" + sscc + "02" + "03041090004827" + "37" + "48", func(t *testing.T) gs1.Scan {
			return gs1.Scan{Symbology: gs1.SymbologyGS1128, SSCC: mustSSCC(t, sscc),
				ContentGTIN: mustGTIN(t, "03041090004827"), Count: 48}
		}},
		{"pallet of cases: content before count before sscc", "]C1" + "02" + "23041090004821" + "37" + "12" + gsep + "00" + sscc, func(t *testing.T) gs1.Scan {
			return gs1.Scan{Symbology: gs1.SymbologyGS1128, SSCC: mustSSCC(t, sscc),
				ContentGTIN: mustGTIN(t, "23041090004821"), Count: 12}
		}},
		{"sscc, content, count, lot, expiry", "]C1" + "00" + sscc + "02" + "13041090004824" + "37" + "20" + gsep + "10" + "L9" + gsep + "17" + "260100", func(t *testing.T) gs1.Scan {
			return gs1.Scan{Symbology: gs1.SymbologyGS1128, SSCC: mustSSCC(t, sscc),
				ContentGTIN: mustGTIN(t, "13041090004824"), Count: 20, Lot: "L9",
				Expiry: gs1.Date{Year: 2026, Month: time.January, Day: 31}} // DD=00 means last day of the month
		}},
		{"trailing separator tolerated", "]C1" + "01" + "13041090004824" + "10" + "LOT" + gsep, func(t *testing.T) gs1.Scan {
			return gs1.Scan{Symbology: gs1.SymbologyGS1128, GTIN: mustGTIN(t, "13041090004824"), Lot: "LOT"}
		}},
		{"lot without separator absorbs trailing digits (documented)", "]C1" + "01" + "13041090004824" + "10" + "ABC17261231", func(t *testing.T) gs1.Scan {
			return gs1.Scan{Symbology: gs1.SymbologyGS1128, GTIN: mustGTIN(t, "13041090004824"), Lot: "ABC17261231"}
		}},
		{"lot of twenty characters", "]C1" + "01" + "13041090004824" + "10" + strings.Repeat("A", 20), func(t *testing.T) gs1.Scan {
			return gs1.Scan{Symbology: gs1.SymbologyGS1128, GTIN: mustGTIN(t, "13041090004824"), Lot: strings.Repeat("A", 20)}
		}},
		{"count of eight digits with leading zeros", "]C1" + "00" + sscc + "02" + "03041090004827" + "37" + "00000012", func(t *testing.T) gs1.Scan {
			return gs1.Scan{Symbology: gs1.SymbologyGS1128, SSCC: mustSSCC(t, sscc),
				ContentGTIN: mustGTIN(t, "03041090004827"), Count: 12}
		}},
		{"leap day expiry", "]C1" + "01" + "13041090004824" + "17" + "280229", func(t *testing.T) gs1.Scan {
			return gs1.Scan{Symbology: gs1.SymbologyGS1128, GTIN: mustGTIN(t, "13041090004824"),
				Expiry: gs1.Date{Year: 2028, Month: time.February, Day: 29}}
		}},
		{"february DD=00 in a leap year is the 29th", "]C1" + "01" + "13041090004824" + "17" + "280200", func(t *testing.T) gs1.Scan {
			return gs1.Scan{Symbology: gs1.SymbologyGS1128, GTIN: mustGTIN(t, "13041090004824"),
				Expiry: gs1.Date{Year: 2028, Month: time.February, Day: 29}}
		}},
		{"element string without symbology identifier", "01" + "13041090004824" + "10" + "X", func(t *testing.T) gs1.Scan {
			return gs1.Scan{GTIN: mustGTIN(t, "13041090004824"), Lot: "X"}
		}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, err := gs1.ParseScan(tc.in)
			if err != nil {
				t.Fatalf("ParseScan(%q) error = %v", tc.in, err)
			}
			if want := tc.want(t); got != want {
				t.Fatalf("ParseScan(%q)\n got  %+v\n want %+v", tc.in, got, want)
			}
		})
	}
}

func TestParseScanRetailAndTransportSymbologies(t *testing.T) {
	tests := []struct {
		name  string
		in    string
		sym   gs1.Symbology
		gtin  string
		fixed string
	}{
		{"ean-13", "]E0" + "5901234123457", gs1.SymbologyEANUPC, "05901234123457", ""},
		{"ean-8", "]E0" + "96385074", gs1.SymbologyEANUPC, "00000096385074", ""},
		{"upc-a as 12 digits", "]E0" + "036000291452", gs1.SymbologyEANUPC, "00036000291452", ""},
		{"upc-a reported as 13 digits", "]E0" + "0036000291452", gs1.SymbologyEANUPC, "00036000291452", ""},
		{"itf-14 case", "]I1" + "13041090004824", gs1.SymbologyITF14, "13041090004824", ""},
		{"itf-14 each-indicator", "]I1" + "03041090004827", gs1.SymbologyITF14, "03041090004827", ""},
		{"bare ean-13 (wedge strips the identifier)", "5901234123457", "", "05901234123457", ""},
		{"bare ean-8", "96385074", "", "00000096385074", ""},
		{"bare gtin-14", "13041090004824", "", "13041090004824", ""},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, err := gs1.ParseScan(tc.in)
			if err != nil {
				t.Fatalf("ParseScan(%q) error = %v", tc.in, err)
			}
			want := gs1.Scan{Symbology: tc.sym, GTIN: mustGTIN(t, tc.gtin)}
			if got != want {
				t.Fatalf("ParseScan(%q)\n got  %+v\n want %+v", tc.in, got, want)
			}
		})
	}
}

func TestParseScanRejects(t *testing.T) {
	const sscc = "376104250021234569"
	tests := []struct {
		name string
		in   string
		want error
	}{
		{"empty", "", gs1.ErrInvalidScan},
		{"only symbology identifier", "]C1", gs1.ErrInvalidScan},
		{"only gs", "]C1" + gsep, gs1.ErrInvalidScan},
		{"unsupported symbology", "]Q3" + "01" + "13041090004824", gs1.ErrUnsupportedSymbology},
		{"unsupported data matrix", "]d2" + "01" + "13041090004824", gs1.ErrUnsupportedSymbology},
		{"unknown AI", "]C1" + "99" + "ABC", gs1.ErrUnknownAI},
		{"unknown AI after valid", "]C1" + "01" + "13041090004824" + "21" + "SER1", gs1.ErrUnknownAI},
		{"leading separator", "]C1" + gsep + "01" + "13041090004824", gs1.ErrInvalidScan},
		{"separator after fixed length AI", "]C1" + "01" + "13041090004824" + gsep + "17" + "261231", gs1.ErrInvalidScan},
		{"AI 01 and AI 02 together", "]C1" + "01" + "13041090004824" + "02" + "03041090004827" + "37" + "48", gs1.ErrConflictingAIs},
		{"AI 01 and AI 02 together with sscc", "]C1" + "00" + sscc + "01" + "13041090004824" + "02" + "03041090004827" + "37" + "48", gs1.ErrConflictingAIs},
		{"AI 02 without 00 and 37", "]C1" + "02" + "03041090004827", gs1.ErrMissingAI},
		{"AI 02 with 37 but no sscc", "]C1" + "02" + "03041090004827" + "37" + "48", gs1.ErrMissingAI},
		{"AI 02 with sscc but no count", "]C1" + "00" + sscc + "02" + "03041090004827", gs1.ErrMissingAI},
		{"AI 37 alone", "]C1" + "37" + "48", gs1.ErrMissingAI},
		{"lot alone identifies nothing", "]C1" + "10" + "LOT", gs1.ErrMissingAI},
		{"expiry alone identifies nothing", "]C1" + "17" + "261231", gs1.ErrMissingAI},
		{"duplicate AI 10", "]C1" + "10" + "A" + gsep + "10" + "B", gs1.ErrDuplicateAI},
		{"duplicate AI 01", "]C1" + "01" + "13041090004824" + "01" + "03041090004827", gs1.ErrDuplicateAI},
		{"gtin bad check digit", "]C1" + "01" + "13041090004825", gs1.ErrInvalidScan},
		{"sscc bad check digit", "]C1" + "00" + "376104250021234568", gs1.ErrInvalidScan},
		{"content gtin bad check digit", "]C1" + "00" + sscc + "02" + "03041090004828" + "37" + "48", gs1.ErrInvalidScan},
		{"truncated gtin", "]C1" + "01" + "130410900", gs1.ErrInvalidScan},
		{"truncated sscc", "]C1" + "00" + "3761042500", gs1.ErrInvalidScan},
		{"non digit in gtin", "]C1" + "01" + "1304109000482A", gs1.ErrInvalidScan},
		{"lot too long", "]C1" + "01" + "13041090004824" + "10" + strings.Repeat("A", 21), gs1.ErrInvalidScan},
		{"empty lot", "]C1" + "01" + "13041090004824" + "10" + gsep + "17" + "261231", gs1.ErrInvalidScan},
		{"lot with space", "]C1" + "01" + "13041090004824" + "10" + "AB CD", gs1.ErrInvalidScan},
		{"lot with hash", "]C1" + "01" + "13041090004824" + "10" + "AB#CD", gs1.ErrInvalidScan},
		{"lot with non ascii", "]C1" + "01" + "13041090004824" + "10" + "LÓT", gs1.ErrInvalidScan},
		{"count zero", "]C1" + "00" + sscc + "02" + "03041090004827" + "37" + "0", gs1.ErrInvalidScan},
		{"count nine digits", "]C1" + "00" + sscc + "02" + "03041090004827" + "37" + "123456789", gs1.ErrInvalidScan},
		{"count non digit", "]C1" + "00" + sscc + "02" + "03041090004827" + "37" + "4X", gs1.ErrInvalidScan},
		{"expiry month 13", "]C1" + "01" + "13041090004824" + "17" + "261301", gs1.ErrInvalidScan},
		{"expiry month 00", "]C1" + "01" + "13041090004824" + "17" + "260001", gs1.ErrInvalidScan},
		{"expiry day 32", "]C1" + "01" + "13041090004824" + "17" + "261232", gs1.ErrInvalidScan},
		{"expiry 30 february", "]C1" + "01" + "13041090004824" + "17" + "260230", gs1.ErrInvalidScan},
		{"expiry 29 february non leap", "]C1" + "01" + "13041090004824" + "17" + "270229", gs1.ErrInvalidScan},
		{"expiry short", "]C1" + "01" + "13041090004824" + "17" + "2612", gs1.ErrInvalidScan},
		{"expiry non digit", "]C1" + "01" + "13041090004824" + "17" + "26AB31", gs1.ErrInvalidScan},
		{"ean with sscc length", "]E0" + sscc, gs1.ErrInvalidScan},
		{"ean bad check digit", "]E0" + "5901234123458", gs1.ErrInvalidScan},
		{"ean letters", "]E0" + "590123412345X", gs1.ErrInvalidScan},
		{"itf-14 thirteen digits", "]I1" + "5901234123457", gs1.ErrInvalidScan},
		{"itf-14 bad check digit", "]I1" + "13041090004825", gs1.ErrInvalidScan},
		{"gibberish", "hello world", gs1.ErrInvalidScan},
		{"nul byte", "]C1" + "01" + "13041090004824" + "10" + "A\x00B", gs1.ErrInvalidScan},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, err := gs1.ParseScan(tc.in)
			if !errors.Is(err, tc.want) {
				t.Fatalf("ParseScan(%q) error = %v, want %v", tc.in, err, tc.want)
			}
			if !errors.Is(err, gs1.ErrInvalidScan) {
				t.Errorf("every parse error must wrap ErrInvalidScan, got %v", err)
			}
			if got != (gs1.Scan{}) {
				t.Errorf("ParseScan(%q) returned a partial scan %+v with an error", tc.in, got)
			}
		})
	}
}

func TestDateString(t *testing.T) {
	d := gs1.Date{Year: 2026, Month: time.March, Day: 9}
	if d.String() != "2026-03-09" {
		t.Errorf("Date.String() = %q", d.String())
	}
	if (gs1.Date{}).String() != "" || !(gs1.Date{}).IsZero() {
		t.Error("zero Date must be empty and IsZero")
	}
}

func TestScanErrorsAreDistinct(t *testing.T) {
	all := []error{gs1.ErrUnsupportedSymbology, gs1.ErrUnknownAI, gs1.ErrDuplicateAI, gs1.ErrConflictingAIs, gs1.ErrMissingAI}
	for i, a := range all {
		for j, b := range all {
			if i != j && errors.Is(a, b) {
				t.Errorf("%v must not match %v", a, b)
			}
		}
	}
}

// FuzzParseScan: whatever a scanner (or an attacker) sends, the parser must not panic, and a
// successful parse must satisfy the structural rules the callers rely on.
func FuzzParseScan(f *testing.F) {
	for _, seed := range []string{
		"", "]C1", "]E0", "]I1", "]C1" + gsep,
		"]C101130410900048241726123110LOT-42",
		"]C1" + "01" + "03041090004827" + "10" + "AB12" + gsep + "17" + "270630",
		"]C100376104250021234569020304109000482737" + "48",
		"]C100376104250021234569" + "02" + "13041090004824" + "37" + "20" + gsep + "10" + "L9" + gsep + "17" + "260100",
		"]E05901234123457", "]E096385074", "]I113041090004824", "13041090004824", "96385074",
		"]C10113041090004824" + "02" + "03041090004827", "99", "]Z0", "\x00", "]C1" + strings.Repeat("1", 64),
	} {
		f.Add(seed)
	}
	f.Fuzz(func(t *testing.T, in string) {
		got, err := gs1.ParseScan(in)
		if err != nil {
			if !errors.Is(err, gs1.ErrInvalidScan) {
				t.Fatalf("error %v does not wrap ErrInvalidScan", err)
			}
			if got != (gs1.Scan{}) {
				t.Fatalf("partial scan %+v returned with error %v", got, err)
			}
			return
		}
		assertScanInvariants(t, got)
	})
}

func assertScanInvariants(t *testing.T, s gs1.Scan) {
	t.Helper()
	if s.SSCC.IsZero() && s.GTIN.IsZero() && s.ContentGTIN.IsZero() {
		t.Fatalf("a successful scan identifies nothing: %+v", s)
	}
	if !s.GTIN.IsZero() && !s.ContentGTIN.IsZero() {
		t.Fatalf("AI 01 and AI 02 together: %+v", s)
	}
	if !s.ContentGTIN.IsZero() && (s.SSCC.IsZero() || s.Count < 1) {
		t.Fatalf("AI 02 without AI 00 and AI 37: %+v", s)
	}
	if s.Count < 0 || s.Count > 99999999 {
		t.Fatalf("count out of range: %d", s.Count)
	}
	if len(s.Lot) > 20 {
		t.Fatalf("lot longer than 20: %q", s.Lot)
	}
	for _, g := range []gs1.GTIN{s.GTIN, s.ContentGTIN} {
		if !g.IsZero() && (len(g.String()) != 14 || !gs1.ValidMod10(g.String())) {
			t.Fatalf("invalid GTIN accepted: %q", g)
		}
	}
	if !s.SSCC.IsZero() && (len(s.SSCC.String()) != 18 || !gs1.ValidMod10(s.SSCC.String())) {
		t.Fatalf("invalid SSCC accepted: %q", s.SSCC)
	}
}
