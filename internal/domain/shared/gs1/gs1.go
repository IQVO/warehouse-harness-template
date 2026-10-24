// Package gs1 holds the GS1 identification value objects every bounded context shares.
//
// RED STUB: signatures only, bodies intentionally unimplemented (TDD red commit).
package gs1

import (
	"errors"
	"time"
)

var (
	ErrInvalidCheckInput    = errors.New("gs1: invalid check digit input")
	ErrInvalidGTIN          = errors.New("gs1: invalid GTIN")
	ErrInvalidSSCC          = errors.New("gs1: invalid SSCC")
	ErrVariableMeasure      = errors.New("gs1: variable measure")
	ErrInvalidScan          = errors.New("gs1: invalid scan")
	ErrUnsupportedSymbology = errors.New("gs1: unsupported symbology")
	ErrUnknownAI            = errors.New("gs1: unknown AI")
	ErrDuplicateAI          = errors.New("gs1: duplicate AI")
	ErrConflictingAIs       = errors.New("gs1: conflicting AIs")
	ErrMissingAI            = errors.New("gs1: missing AI")
)

func Mod10CheckDigit(string) (int, error) { return -1, nil }
func ValidMod10(string) bool              { return true }

type GTIN struct{ v string }

func ParseGTIN(string) (GTIN, error)   { return GTIN{}, nil }
func NewGTIN(string) (GTIN, error)     { return GTIN{}, nil }
func (g GTIN) String() string          { return "?" }
func (g GTIN) IsZero() bool            { return false }
func (g GTIN) IndicatorDigit() int     { return -1 }
func (g GTIN) CheckDigit() int         { return -1 }
func (g GTIN) IsVariableMeasure() bool { return false }
func RejectVariableMeasure(GTIN) error { return nil }

type SSCC struct{ v string }

func ParseSSCC(string) (SSCC, error) { return SSCC{}, nil }
func NewSSCC(string) (SSCC, error)   { return SSCC{}, nil }
func (s SSCC) String() string        { return "?" }
func (s SSCC) IsZero() bool          { return false }
func (s SSCC) ExtensionDigit() int   { return -1 }
func (s SSCC) CheckDigit() int       { return -1 }

type Symbology string

const (
	SymbologyEANUPC Symbology = "]E0"
	SymbologyITF14  Symbology = "]I1"
	SymbologyGS1128 Symbology = "]C1"
)

type Date struct {
	Year  int
	Month time.Month
	Day   int
}

func (d Date) String() string { return "?" }
func (d Date) IsZero() bool   { return false }

type Scan struct {
	Symbology   Symbology
	SSCC        SSCC
	GTIN        GTIN
	ContentGTIN GTIN
	Count       int
	Lot         string
	Expiry      Date
}

func ParseScan(string) (Scan, error) { return Scan{}, nil }
