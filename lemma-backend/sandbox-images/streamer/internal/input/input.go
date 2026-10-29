// Package input is the whole vocabulary a controlling viewer may speak.
//
// It is a closed set on purpose. The CDP relay this design replaced leaked its
// input boundary because its vocabulary was Chrome's, and Chrome's grows. This
// one is ours: a pointer, buttons, a wheel, keys, typed text, a paste and a
// request for one of a few screen sizes. Nothing here runs a command, opens a
// file or reaches the page's script. Adding a message is a code review.
//
// Parsing is strict: one JSON object, no unknown fields, no trailing bytes,
// every value range-checked. Anything else is dropped and counted.
package input

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

// Kinds of command.
const (
	Move   = "m"
	Button = "b"
	Wheel  = "w"
	Key    = "k"
	Text   = "txt"
	Paste  = "paste"
	Size   = "size"
)

// Frame size ceilings. The ordinary messages are tiny; only text and paste
// carry a payload, and each has its own bound.
const (
	MaxSmallFrame = 128
	MaxTextFrame  = 4 << 10
	MaxPasteBytes = 1 << 20
	MaxPasteFrame = MaxPasteBytes*2 + 64 // JSON escaping can double a string
	MaxTextRunes  = 256
	MaxWheelStep  = 10
)

// Command is one validated message.
type Command struct {
	Kind string
	X, Y int
	B    int
	Down bool
	DX   int
	DY   int
	Sym  uint32
	S    string
	W, H int
}

// Screen is what the pointer must stay inside and which sizes may be asked for.
type Screen struct {
	W, H    int
	Presets []Preset
}

type Preset struct{ W, H int }

var ErrRejected = errors.New("input rejected")

// ParsePresets reads `1280x800,1440x900`. Malformed entries are skipped.
func ParsePresets(s string) []Preset {
	var out []Preset
	for _, part := range strings.Split(s, ",") {
		w, h, ok := strings.Cut(strings.TrimSpace(part), "x")
		if !ok {
			continue
		}
		wi, err1 := strconv.Atoi(w)
		hi, err2 := strconv.Atoi(h)
		if err1 != nil || err2 != nil || wi < 320 || hi < 240 || wi > 4096 || hi > 4096 {
			continue
		}
		out = append(out, Preset{wi, hi})
	}
	return out
}

const DefaultPresets = "1280x800,1440x900,820x1180,390x844"

// wire is every field any message may carry. Unknown fields fail decoding;
// fields that belong to another kind are refused after it.
type wire struct {
	T  *string `json:"t"`
	X  *int    `json:"x"`
	Y  *int    `json:"y"`
	B  *int    `json:"b"`
	D  *bool   `json:"d"`
	DX *int    `json:"dx"`
	DY *int    `json:"dy"`
	K  *uint32 `json:"k"`
	S  *string `json:"s"`
	W  *int    `json:"w"`
	H  *int    `json:"h"`
}

func reject(why string) error { return fmt.Errorf("%w: %s", ErrRejected, why) }

// Parse validates one text frame against the closed set.
func Parse(raw []byte, screen Screen) (Command, error) {
	if len(raw) > MaxPasteFrame {
		return Command{}, reject("frame too large")
	}
	if !utf8.Valid(raw) {
		return Command{}, reject("not utf-8")
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	var w wire
	if err := dec.Decode(&w); err != nil {
		return Command{}, reject("not a known object")
	}
	if dec.More() {
		return Command{}, reject("trailing data")
	}
	if _, err := dec.Token(); err == nil {
		return Command{}, reject("trailing data")
	}
	if w.T == nil {
		return Command{}, reject("no type")
	}
	kind := *w.T
	limit := MaxSmallFrame
	switch kind {
	case Text:
		limit = MaxTextFrame
	case Paste:
		limit = MaxPasteFrame
	}
	if len(raw) > limit {
		return Command{}, reject("frame too large for " + kind)
	}

	has := map[string]bool{
		"x": w.X != nil, "y": w.Y != nil, "b": w.B != nil, "d": w.D != nil,
		"dx": w.DX != nil, "dy": w.DY != nil, "k": w.K != nil, "s": w.S != nil,
		"w": w.W != nil, "h": w.H != nil,
	}
	only := func(fields ...string) error {
		allowed := map[string]bool{}
		for _, f := range fields {
			allowed[f] = true
			if !has[f] {
				return reject(kind + " needs " + f)
			}
		}
		for f, present := range has {
			if present && !allowed[f] {
				return reject(kind + " does not take " + f)
			}
		}
		return nil
	}

	switch kind {
	case Move:
		if err := only("x", "y"); err != nil {
			return Command{}, err
		}
		if *w.X < 0 || *w.Y < 0 || *w.X >= screen.W || *w.Y >= screen.H {
			return Command{}, reject("pointer off screen")
		}
		return Command{Kind: Move, X: *w.X, Y: *w.Y}, nil
	case Button:
		if err := only("b", "d"); err != nil {
			return Command{}, err
		}
		if *w.B < 1 || *w.B > 3 {
			return Command{}, reject("button out of range")
		}
		return Command{Kind: Button, B: *w.B, Down: *w.D}, nil
	case Wheel:
		// Either axis may be omitted, but not both.
		for f, present := range has {
			if present && f != "dx" && f != "dy" {
				return Command{}, reject("w does not take " + f)
			}
		}
		dx, dy := 0, 0
		if w.DX != nil {
			dx = *w.DX
		}
		if w.DY != nil {
			dy = *w.DY
		}
		if dx < -MaxWheelStep || dx > MaxWheelStep || dy < -MaxWheelStep || dy > MaxWheelStep || (dx == 0 && dy == 0) {
			return Command{}, reject("wheel step out of range")
		}
		return Command{Kind: Wheel, DX: dx, DY: dy}, nil
	case Key:
		if err := only("k", "d"); err != nil {
			return Command{}, err
		}
		if !ValidKeysym(*w.K) {
			return Command{}, reject("keysym outside the allowed ranges")
		}
		return Command{Kind: Key, Sym: *w.K, Down: *w.D}, nil
	case Text:
		if err := only("s"); err != nil {
			return Command{}, err
		}
		s := *w.S
		if s == "" || utf8.RuneCountInString(s) > MaxTextRunes {
			return Command{}, reject("text empty or too long")
		}
		for _, r := range s {
			if r == '\n' || r == '\t' {
				continue
			}
			if unicode.IsControl(r) || r == utf8.RuneError {
				return Command{}, reject("control character in text")
			}
		}
		return Command{Kind: Text, S: s}, nil
	case Paste:
		if err := only("s"); err != nil {
			return Command{}, err
		}
		if *w.S == "" || len(*w.S) > MaxPasteBytes {
			return Command{}, reject("paste empty or too large")
		}
		return Command{Kind: Paste, S: *w.S}, nil
	case Size:
		if err := only("w", "h"); err != nil {
			return Command{}, err
		}
		for _, p := range screen.Presets {
			if p.W == *w.W && p.H == *w.H {
				return Command{Kind: Size, W: p.W, H: p.H}, nil
			}
		}
		return Command{}, reject("size is not a preset")
	}
	return Command{}, reject("unknown type")
}

// ValidKeysym admits printable Latin-1, the function/modifier/keypad/TTY
// block, and Unicode keysyms. Everything else -- vendor keysyms, the 3270
// block, "VoidSymbol" games -- has no business coming from a viewer.
func ValidKeysym(k uint32) bool {
	switch {
	case k >= 0x20 && k <= 0x7e:
		return true
	case k >= 0xa0 && k <= 0xff:
		return true
	case k >= 0xfe00 && k <= 0xffff:
		return k != 0xffffff
	case k >= 0x1000100 && k <= 0x110ffff:
		return true
	}
	return false
}
