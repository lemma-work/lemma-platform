package input

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"
)

var screen = Screen{W: 1280, H: 800, Presets: ParsePresets(DefaultPresets)}

func TestParse(t *testing.T) {
	long := strings.Repeat("a", MaxTextRunes+1)
	cases := []struct {
		raw  string
		want *Command
	}{
		{`{"t":"m","x":10,"y":20}`, &Command{Kind: Move, X: 10, Y: 20}},
		{`{"t":"m","x":1279,"y":799}`, &Command{Kind: Move, X: 1279, Y: 799}},
		{`{"t":"m","x":1280,"y":0}`, nil},
		{`{"t":"m","x":-1,"y":0}`, nil},
		{`{"t":"m","x":1.5,"y":0}`, nil},
		{`{"t":"m","x":1}`, nil},
		{`{"t":"m","x":1,"y":1,"b":1}`, nil},
		{`{"t":"b","b":1,"d":true}`, &Command{Kind: Button, B: 1, Down: true}},
		{`{"t":"b","b":4,"d":true}`, nil},
		{`{"t":"b","b":2}`, nil},
		{`{"t":"w","dy":1}`, &Command{Kind: Wheel, DY: 1}},
		{`{"t":"w","dx":-3,"dy":2}`, &Command{Kind: Wheel, DX: -3, DY: 2}},
		{`{"t":"w","dy":11}`, nil},
		{`{"t":"w","dx":0,"dy":0}`, nil},
		{`{"t":"w"}`, nil},
		{`{"t":"k","k":97,"d":true}`, &Command{Kind: Key, Sym: 97, Down: true}},
		{`{"t":"k","k":65293,"d":false}`, &Command{Kind: Key, Sym: 0xff0d}},
		{`{"t":"k","k":16777729,"d":true}`, &Command{Kind: Key, Sym: 0x1000201, Down: true}},
		{`{"t":"k","k":31,"d":true}`, nil},
		{`{"t":"k","k":4294967295,"d":true}`, nil},
		{`{"t":"k","k":-1,"d":true}`, nil},
		{`{"t":"txt","s":"héllo\n"}`, &Command{Kind: Text, S: "héllo\n"}},
		{`{"t":"txt","s":"bell\u0007"}`, nil},
		{`{"t":"txt","s":""}`, nil},
		{`{"t":"txt","s":"` + long + `"}`, nil},
		{`{"t":"paste","s":"anything \u0007 at all"}`, &Command{Kind: Paste, S: "anything \a at all"}},
		{`{"t":"size","w":1440,"h":900}`, &Command{Kind: Size, W: 1440, H: 900}},
		{`{"t":"size","w":1441,"h":900}`, nil},
		{`{"t":"exec","s":"rm -rf /"}`, nil},
		{`{"t":"m","x":1,"y":1,"cmd":"x"}`, nil},
		{`{"t":"m","x":1,"y":1}{"t":"k","k":97,"d":true}`, nil},
		{`{"t":"m","x":1,"y":1} trailing`, nil},
		{`[1,2]`, nil},
		{`"m"`, nil},
		{``, nil},
		{`{"x":1,"y":1}`, nil},
		{`{"t":"m","x":1,"y":1,"pad":"` + strings.Repeat("a", 200) + `"}`, nil},
		{`{"t":"m","x":1,"y":1}` + strings.Repeat(" ", 200), nil},
	}
	for _, tc := range cases {
		got, err := Parse([]byte(tc.raw), screen)
		if tc.want == nil {
			if err == nil {
				t.Errorf("%s: accepted %+v", tc.raw, got)
			} else if !errors.Is(err, ErrRejected) {
				t.Errorf("%s: wrong error type %v", tc.raw, err)
			}
			continue
		}
		if err != nil || got != *tc.want {
			t.Errorf("%s: got %+v %v, want %+v", tc.raw, got, err, *tc.want)
		}
	}
}

func TestParsePresets(t *testing.T) {
	got := ParsePresets("1280x800, 10x10,abc,1440x900,5000x10")
	if len(got) != 2 || got[0] != (Preset{1280, 800}) || got[1] != (Preset{1440, 900}) {
		t.Fatalf("got %v", got)
	}
}

func TestLimiter(t *testing.T) {
	now := time.Unix(0, 0)
	l := NewLimiter(func() time.Time { return now })
	allowed := 0
	for i := 0; i < 2000; i++ {
		if l.Allow(Command{Kind: Move}) {
			allowed++
		}
	}
	if allowed != MessagesPerSecond {
		t.Fatalf("burst allowed %d", allowed)
	}
	now = now.Add(100 * time.Millisecond)
	if !l.Allow(Command{Kind: Move}) {
		t.Fatal("refill did not happen")
	}
	w := NewLimiter(func() time.Time { return now })
	notches := 0
	for i := 0; i < 100; i++ {
		if w.Allow(Command{Kind: Wheel, DY: 10}) {
			notches += 10
		}
	}
	if notches > WheelPerSecond {
		t.Fatalf("wheel allowed %d notches", notches)
	}
}

// FuzzParse: arbitrary bytes and mutations of valid messages must never panic,
// and anything accepted must be inside the closed set and its ranges.
func FuzzParse(f *testing.F) {
	for _, s := range []string{
		`{"t":"m","x":10,"y":20}`, `{"t":"b","b":1,"d":true}`, `{"t":"w","dy":-1}`,
		`{"t":"k","k":97,"d":true}`, `{"t":"txt","s":"hi"}`, `{"t":"paste","s":"x"}`,
		`{"t":"size","w":1280,"h":800}`, `{}`, `null`, "\xff\xfe",
	} {
		f.Add([]byte(s))
	}
	f.Fuzz(func(t *testing.T, raw []byte) {
		c, err := Parse(raw, screen)
		if err != nil {
			return
		}
		switch c.Kind {
		case Move:
			if c.X < 0 || c.Y < 0 || c.X >= screen.W || c.Y >= screen.H {
				t.Fatalf("move out of bounds: %+v", c)
			}
		case Button:
			if c.B < 1 || c.B > 3 {
				t.Fatalf("button %d", c.B)
			}
		case Wheel:
			if c.DX == 0 && c.DY == 0 || abs(c.DX) > MaxWheelStep || abs(c.DY) > MaxWheelStep {
				t.Fatalf("wheel %+v", c)
			}
		case Key:
			if !ValidKeysym(c.Sym) {
				t.Fatalf("keysym %x", c.Sym)
			}
		case Text:
			if len(raw) > MaxTextFrame {
				t.Fatal("text frame over cap")
			}
		case Paste:
			if len(c.S) > MaxPasteBytes {
				t.Fatal("paste over cap")
			}
		case Size:
			ok := false
			for _, p := range screen.Presets {
				ok = ok || (p.W == c.W && p.H == c.H)
			}
			if !ok {
				t.Fatalf("size %dx%d", c.W, c.H)
			}
		default:
			t.Fatalf("kind %q accepted", c.Kind)
		}
		if c.Kind != Text && c.Kind != Paste && len(raw) > MaxSmallFrame {
			t.Fatalf("%s frame of %d bytes accepted", c.Kind, len(raw))
		}
		if !json.Valid(raw) {
			t.Fatal("invalid JSON accepted")
		}
	})
}
