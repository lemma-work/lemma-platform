// Package inject turns validated input commands into X events with XTest.
//
// It only ever receives what `input.Parse` admitted, and it only ever does
// what a keyboard and a mouse plugged into the machine could do -- plus
// setting the clipboard before a paste and asking for one of the preset
// screen sizes.
package inject

import (
	"bytes"
	"context"
	"fmt"
	"os/exec"
	"strconv"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/jezek/xgb"
	"github.com/jezek/xgb/xproto"
	"github.com/jezek/xgb/xtest"

	"lemma-streamer/internal/input"
)

const (
	xkShiftL   = 0xffe1
	xkShiftR   = 0xffe2
	xkControlL = 0xffe3
	xkReturn   = 0xff0d
	xkTab      = 0xff09
	xkV        = 0x76
)

// Injector holds its own X connection: input must not queue behind a frame
// grab on the capture connection.
type Injector struct {
	mu   sync.Mutex
	c    *xgb.Conn
	root xproto.Window

	minKC, maxKC xproto.Keycode
	perKC        int
	// keysym -> (keycode, level) for what the current keymap already has.
	keymap map[uint32]mapped
	// Keycodes with no symbols at all, borrowed for keysyms the layout lacks.
	spares []xproto.Keycode
	next   int
	// What is held down, so a controller leaving never leaves a key stuck.
	keys    map[uint32]xproto.Keycode
	buttons map[byte]bool
}

type mapped struct {
	kc    xproto.Keycode
	level int
}

func New(display string) (*Injector, error) {
	c, err := xgb.NewConnDisplay(display)
	if err != nil {
		return nil, err
	}
	if err := xtest.Init(c); err != nil {
		c.Close()
		return nil, fmt.Errorf("XTest: %w", err)
	}
	setup := xproto.Setup(c)
	in := &Injector{
		c: c, root: setup.DefaultScreen(c).Root,
		minKC: setup.MinKeycode, maxKC: setup.MaxKeycode,
		keys: map[uint32]xproto.Keycode{}, buttons: map[byte]bool{},
	}
	if err := in.loadKeymap(); err != nil {
		c.Close()
		return nil, err
	}
	return in, nil
}

func (in *Injector) Close() { in.c.Close() }

func (in *Injector) loadKeymap() error {
	n := byte(in.maxKC - in.minKC + 1)
	km, err := xproto.GetKeyboardMapping(in.c, in.minKC, n).Reply()
	if err != nil {
		return fmt.Errorf("keyboard mapping: %w", err)
	}
	in.perKC = int(km.KeysymsPerKeycode)
	in.keymap = map[uint32]mapped{}
	in.spares = nil
	for i := 0; i < int(n); i++ {
		empty := true
		for j := 0; j < in.perKC; j++ {
			ks := uint32(km.Keysyms[i*in.perKC+j])
			if ks == 0 {
				continue
			}
			empty = false
			if j > 1 {
				continue // group 2 levels need a group switch nobody sends
			}
			if _, seen := in.keymap[ks]; !seen {
				in.keymap[ks] = mapped{xproto.Keycode(int(in.minKC) + i), j}
			}
		}
		if empty {
			in.spares = append(in.spares, xproto.Keycode(int(in.minKC)+i))
		}
	}
	return nil
}

func (in *Injector) fake(typ byte, detail byte, x, y int16) {
	xtest.FakeInput(in.c, typ, detail, 0, in.root, x, y, 0)
}

// borrow maps a keysym onto a spare keycode at both levels, so it types the
// same with or without Shift held. Spares rotate, so a key still held keeps
// its mapping until several later borrows have passed.
func (in *Injector) borrow(sym uint32) (xproto.Keycode, bool) {
	if len(in.spares) == 0 {
		return 0, false
	}
	kc := in.spares[in.next%len(in.spares)]
	in.next++
	syms := make([]xproto.Keysym, in.perKC)
	for i := range syms {
		syms[i] = xproto.Keysym(sym)
	}
	if err := xproto.ChangeKeyboardMappingChecked(in.c, 1, kc, byte(in.perKC), syms).Check(); err != nil {
		return 0, false
	}
	// The server needs a moment to tell clients about the new mapping before
	// the key that uses it arrives; without it the first press types the
	// spare's previous symbol.
	in.c.Sync()
	time.Sleep(2 * time.Millisecond)
	return kc, true
}

func (in *Injector) shiftHeld() bool {
	_, l := in.keys[xkShiftL]
	_, r := in.keys[xkShiftR]
	return l || r
}

// keycodeFor finds where a keysym can be pressed so that it types itself
// given the modifiers currently held.
func (in *Injector) keycodeFor(sym uint32) (xproto.Keycode, bool) {
	if m, ok := in.keymap[sym]; ok {
		if m.level == 0 || in.shiftHeld() || isModifierOrFunction(sym) {
			return m.kc, true
		}
	}
	return in.borrow(sym)
}

func isModifierOrFunction(sym uint32) bool { return sym >= 0xfe00 && sym <= 0xffff }

// Apply performs one command.
func (in *Injector) Apply(cmd input.Command) error {
	in.mu.Lock()
	defer in.mu.Unlock()
	switch cmd.Kind {
	case input.Move:
		in.fake(xproto.MotionNotify, 0, int16(cmd.X), int16(cmd.Y))
	case input.Button:
		b := byte(cmd.B)
		if cmd.Down {
			in.buttons[b] = true
			in.fake(xproto.ButtonPress, b, 0, 0)
		} else {
			delete(in.buttons, b)
			in.fake(xproto.ButtonRelease, b, 0, 0)
		}
	case input.Wheel:
		in.wheel(cmd.DY, 4, 5)
		in.wheel(cmd.DX, 6, 7)
	case input.Key:
		in.key(cmd.Sym, cmd.Down)
	case input.Text:
		in.text(cmd.S)
	case input.Paste:
		if err := setClipboard(cmd.S); err != nil {
			return err
		}
		in.key(xkControlL, true)
		in.key(xkV, true)
		in.key(xkV, false)
		in.key(xkControlL, false)
	case input.Size:
		in.c.Sync()
		in.mu.Unlock()
		err := resize(cmd.W, cmd.H)
		in.mu.Lock()
		return err
	}
	in.c.Sync()
	return nil
}

func (in *Injector) wheel(n int, neg, pos byte) {
	b := pos
	if n < 0 {
		b, n = neg, -n
	}
	for i := 0; i < n; i++ {
		in.fake(xproto.ButtonPress, b, 0, 0)
		in.fake(xproto.ButtonRelease, b, 0, 0)
	}
}

func (in *Injector) key(sym uint32, down bool) {
	if !down {
		kc, held := in.keys[sym]
		if !held {
			return
		}
		delete(in.keys, sym)
		in.fake(xproto.KeyRelease, byte(kc), 0, 0)
		return
	}
	if _, held := in.keys[sym]; held {
		// Auto-repeat from the viewer: press again on the same keycode.
		in.fake(xproto.KeyPress, byte(in.keys[sym]), 0, 0)
		return
	}
	kc, ok := in.keycodeFor(sym)
	if !ok {
		return
	}
	in.keys[sym] = kc
	in.fake(xproto.KeyPress, byte(kc), 0, 0)
}

// text types a string character by character, on borrowed keycodes where the
// layout lacks the character -- which is how IME output and non-US text get
// in without a keyboard layout that has them.
func (in *Injector) text(s string) {
	for _, r := range s {
		sym := RuneKeysym(r)
		var kc xproto.Keycode
		if m, ok := in.keymap[sym]; ok && m.level == 0 && !in.shiftHeld() {
			kc = m.kc
		} else if m, ok := in.keymap[sym]; ok && isModifierOrFunction(sym) {
			kc = m.kc
		} else if b, ok := in.borrow(sym); ok {
			kc = b
		} else {
			continue
		}
		in.fake(xproto.KeyPress, byte(kc), 0, 0)
		in.fake(xproto.KeyRelease, byte(kc), 0, 0)
		in.c.Sync()
	}
}

// RuneKeysym is the X keysym for a character.
func RuneKeysym(r rune) uint32 {
	switch {
	case r == '\n':
		return xkReturn
	case r == '\t':
		return xkTab
	case r >= 0x20 && r <= 0x7e, r >= 0xa0 && r <= 0xff:
		return uint32(r)
	}
	return 0x1000000 | uint32(r)
}

// ReleaseAll lets go of everything a controller left held.
func (in *Injector) ReleaseAll() {
	in.mu.Lock()
	defer in.mu.Unlock()
	for sym, kc := range in.keys {
		in.fake(xproto.KeyRelease, byte(kc), 0, 0)
		delete(in.keys, sym)
	}
	for b := range in.buttons {
		in.fake(xproto.ButtonRelease, b, 0, 0)
		delete(in.buttons, b)
	}
	in.c.Sync()
}

func setClipboard(s string) error {
	if !utf8.ValidString(s) {
		return fmt.Errorf("clipboard text is not utf-8")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	// xclip stays behind as the selection owner; no pipes are attached to its
	// output, so waiting for it returns as soon as it has read stdin.
	cmd := exec.CommandContext(ctx, "xclip", "-i", "-selection", "clipboard")
	cmd.Stdin = bytes.NewReader([]byte(s))
	return cmd.Run()
}

func resize(w, h int) error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	return exec.CommandContext(ctx, "set-display-size", strconv.Itoa(w), strconv.Itoa(h)).Run()
}
