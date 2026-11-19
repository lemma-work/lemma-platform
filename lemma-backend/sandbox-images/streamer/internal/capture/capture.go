//go:build linux

// Package capture reads the X display: its pixels, whether they changed,
// where the pointer is, what the cursor looks like, and what was copied.
//
// Pixels come through MIT-SHM where the server allows it -- one shared
// buffer, no copy through the socket -- and through plain GetImage where it
// does not. x11vnc needed `-noshm` in this container, so the fallback is a
// path that has to work, not a curiosity.
package capture

import (
	"errors"
	"fmt"
	"log"
	"os"
	"sync"
	"sync/atomic"

	"github.com/jezek/xgb"
	"github.com/jezek/xgb/damage"
	"github.com/jezek/xgb/shm"
	"github.com/jezek/xgb/xfixes"
	"github.com/jezek/xgb/xproto"
	"golang.org/x/sys/unix"
)

// Frame is one grab: BGRX pixels, Stride bytes per row. Valid until the next
// Grab on the same Capturer.
type Frame struct {
	W, H, Stride int
	Pix          []byte
}

// Events the display reports, delivered on the capturer's own goroutine.
type Handlers struct {
	Cursor    func(Cursor)
	Clipboard func() // the clipboard owner changed; read it with ReadClipboard
	Resized   func(w, h int)
}

type Capturer struct {
	c    *xgb.Conn
	root xproto.Window

	mu   sync.Mutex
	w, h int

	useShm  bool
	seg     shm.Seg
	shmID   int
	shmBuf  []byte
	shmSize int

	dmg   damage.Damage
	dirty atomic.Bool

	clipboard xproto.Atom
	handlers  Handlers
	cursors   map[uint32]Cursor
}

func Open(display string, h Handlers) (*Capturer, error) {
	c, err := xgb.NewConnDisplay(display)
	if err != nil {
		return nil, err
	}
	setup := xproto.Setup(c)
	screen := setup.DefaultScreen(c)
	cp := &Capturer{
		c: c, root: screen.Root,
		w: int(screen.WidthInPixels), h: int(screen.HeightInPixels),
		handlers: h, cursors: map[uint32]Cursor{},
	}
	if screen.RootDepth != 24 && screen.RootDepth != 32 {
		c.Close()
		return nil, fmt.Errorf("display depth %d is not 24 or 32", screen.RootDepth)
	}
	if err := xfixes.Init(c); err != nil {
		c.Close()
		return nil, fmt.Errorf("XFixes: %w", err)
	}
	if _, err := xfixes.QueryVersion(c, 5, 0).Reply(); err != nil {
		c.Close()
		return nil, fmt.Errorf("XFixes version: %w", err)
	}
	// Damage is an optimisation: without it every tick is treated as a change.
	if err := damage.Init(c); err == nil {
		if _, err := damage.QueryVersion(c, 1, 1).Reply(); err == nil {
			if id, err := damage.NewDamageId(c); err == nil {
				if damage.CreateChecked(c, id, xproto.Drawable(cp.root), damage.ReportLevelNonEmpty).Check() == nil {
					cp.dmg = id
				}
			}
		}
	}
	if cp.dmg == 0 {
		log.Printf("capture: XDamage unavailable; capturing every tick")
	}
	cp.dirty.Store(true)

	// Root resizes (RandR, set-display-size) arrive as ConfigureNotify.
	xproto.ChangeWindowAttributes(c, cp.root, xproto.CwEventMask, []uint32{xproto.EventMaskStructureNotify})
	xfixes.SelectCursorInput(c, cp.root, xfixes.CursorNotifyMaskDisplayCursor)
	if atom, err := xproto.InternAtom(c, false, uint16(len("CLIPBOARD")), "CLIPBOARD").Reply(); err == nil {
		cp.clipboard = atom.Atom
		xfixes.SelectSelectionInput(c, cp.root, atom.Atom, xfixes.SelectionEventMaskSetSelectionOwner)
	}

	// LEMMA_STREAM_NO_SHM forces the fallback, so it can be tested on a
	// server where SHM works.
	if os.Getenv("LEMMA_STREAM_NO_SHM") != "" {
		log.Printf("capture: MIT-SHM disabled by LEMMA_STREAM_NO_SHM; using GetImage")
	} else if err := shm.Init(c); err == nil {
		if err := cp.attachShm(); err != nil {
			log.Printf("capture: MIT-SHM unavailable (%v); using GetImage", err)
		}
	} else {
		log.Printf("capture: MIT-SHM extension absent; using GetImage")
	}
	go cp.events()
	return cp, nil
}

// Mode names the pixel path, for /health.
func (cp *Capturer) Mode() string {
	cp.mu.Lock()
	defer cp.mu.Unlock()
	if cp.useShm {
		return "shm"
	}
	return "getimage"
}

func (cp *Capturer) Size() (int, int) {
	cp.mu.Lock()
	defer cp.mu.Unlock()
	return cp.w, cp.h
}

// Dirty reports whether anything was drawn since the last grab.
func (cp *Capturer) Dirty() bool { return cp.dirty.Load() }

func (cp *Capturer) attachShm() error {
	size := cp.w * cp.h * 4
	id, err := unix.SysvShmGet(unix.IPC_PRIVATE, size, unix.IPC_CREAT|0o600)
	if err != nil {
		return err
	}
	buf, err := unix.SysvShmAttach(id, 0, 0)
	if err != nil {
		_, _ = unix.SysvShmCtl(id, unix.IPC_RMID, nil)
		return err
	}
	seg, err := shm.NewSegId(cp.c)
	if err == nil {
		err = shm.AttachChecked(cp.c, seg, uint32(id), false).Check()
	}
	// Marked for removal now: the kernel keeps it until both sides detach,
	// so a crash cannot leak it.
	_, _ = unix.SysvShmCtl(id, unix.IPC_RMID, nil)
	if err != nil {
		_ = unix.SysvShmDetach(buf)
		return err
	}
	cp.useShm, cp.seg, cp.shmID, cp.shmBuf, cp.shmSize = true, seg, id, buf, size
	return nil
}

func (cp *Capturer) detachShm() {
	if cp.shmBuf == nil {
		return
	}
	shm.Detach(cp.c, cp.seg)
	_ = unix.SysvShmDetach(cp.shmBuf)
	cp.shmBuf, cp.shmSize = nil, 0
}

var errSize = errors.New("display size changed during grab")

// Grab reads the whole screen.
func (cp *Capturer) Grab() (Frame, error) {
	cp.mu.Lock()
	defer cp.mu.Unlock()
	if cp.dmg != 0 {
		// Before the grab, so anything drawn during it marks the next one.
		cp.dirty.Store(false)
		damage.Subtract(cp.c, cp.dmg, 0, 0)
	}
	w, h := cp.w, cp.h
	if cp.useShm {
		if cp.shmSize < w*h*4 {
			cp.detachShm()
			cp.useShm = false
			if err := cp.attachShm(); err != nil {
				log.Printf("capture: MIT-SHM resize failed (%v); using GetImage", err)
			}
		}
	}
	if cp.useShm {
		_, err := shm.GetImage(cp.c, xproto.Drawable(cp.root), 0, 0, uint16(w), uint16(h),
			0xffffffff, xproto.ImageFormatZPixmap, cp.seg, 0).Reply()
		if err == nil {
			return Frame{W: w, H: h, Stride: w * 4, Pix: cp.shmBuf[:w*h*4]}, nil
		}
		log.Printf("capture: MIT-SHM grab failed (%v); using GetImage", err)
		cp.detachShm()
		cp.useShm = false
	}
	reply, err := xproto.GetImage(cp.c, xproto.ImageFormatZPixmap, xproto.Drawable(cp.root),
		0, 0, uint16(w), uint16(h), 0xffffffff).Reply()
	if err != nil {
		return Frame{}, err
	}
	if len(reply.Data) < w*h*4 {
		return Frame{}, errSize
	}
	return Frame{W: w, H: h, Stride: w * 4, Pix: reply.Data}, nil
}

// Pointer is where the pointer is now.
func (cp *Capturer) Pointer() (int, int, bool) {
	r, err := xproto.QueryPointer(cp.c, cp.root).Reply()
	if err != nil {
		return 0, 0, false
	}
	return int(r.RootX), int(r.RootY), true
}

func (cp *Capturer) events() {
	for {
		ev, xerr := cp.c.WaitForEvent()
		if ev == nil && xerr == nil {
			return // connection closed
		}
		if xerr != nil {
			continue
		}
		switch e := ev.(type) {
		case damage.NotifyEvent:
			cp.dirty.Store(true)
		case xproto.ConfigureNotifyEvent:
			if e.Window != cp.root {
				continue
			}
			cp.mu.Lock()
			changed := int(e.Width) != cp.w || int(e.Height) != cp.h
			cp.w, cp.h = int(e.Width), int(e.Height)
			cp.mu.Unlock()
			cp.dirty.Store(true)
			if changed && cp.handlers.Resized != nil {
				cp.handlers.Resized(int(e.Width), int(e.Height))
			}
		case xfixes.CursorNotifyEvent:
			if cp.handlers.Cursor != nil {
				if cur, ok := cp.cursor(); ok {
					cp.handlers.Cursor(cur)
				}
			}
		case xfixes.SelectionNotifyEvent:
			if e.Selection == cp.clipboard && cp.handlers.Clipboard != nil {
				cp.handlers.Clipboard()
			}
		}
	}
}

// CurrentCursor reads the cursor now, for a viewer that just joined.
func (cp *Capturer) CurrentCursor() (Cursor, bool) { return cp.cursor() }

func (cp *Capturer) Close() {
	cp.mu.Lock()
	cp.detachShm()
	cp.mu.Unlock()
	cp.c.Close()
}
