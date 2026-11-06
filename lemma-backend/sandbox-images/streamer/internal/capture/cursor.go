//go:build linux

package capture

import (
	"bytes"
	"image"
	"image/png"

	"github.com/jezek/xgb/xfixes"
)

// Cursor is the pointer's shape, drawn by the viewer itself so moving the
// mouse never waits for a video frame.
type Cursor struct {
	ID         uint32
	W, H       int
	XHot, YHot int
	PNG        []byte
}

// The largest cursor worth sending. X allows bigger; nobody draws one.
const maxCursorSide = 256

func (cp *Capturer) cursor() (Cursor, bool) {
	r, err := xfixes.GetCursorImage(cp.c).Reply()
	if err != nil || r.Width == 0 || r.Height == 0 || r.Width > maxCursorSide || r.Height > maxCursorSide {
		return Cursor{}, false
	}
	cp.mu.Lock()
	cached, ok := cp.cursors[r.CursorSerial]
	cp.mu.Unlock()
	if ok {
		return cached, true
	}
	png, err := EncodeARGB(r.CursorImage, int(r.Width), int(r.Height))
	if err != nil {
		return Cursor{}, false
	}
	c := Cursor{ID: r.CursorSerial, W: int(r.Width), H: int(r.Height), XHot: int(r.Xhot), YHot: int(r.Yhot), PNG: png}
	cp.mu.Lock()
	if len(cp.cursors) > 64 {
		cp.cursors = map[uint32]Cursor{}
	}
	cp.cursors[r.CursorSerial] = c
	cp.mu.Unlock()
	return c, true
}

// EncodeARGB turns XFixes' premultiplied ARGB words into a PNG.
func EncodeARGB(argb []uint32, w, h int) ([]byte, error) {
	img := image.NewRGBA(image.Rect(0, 0, w, h))
	for i := 0; i < w*h && i < len(argb); i++ {
		p := argb[i]
		img.Pix[i*4+0] = byte(p >> 16)
		img.Pix[i*4+1] = byte(p >> 8)
		img.Pix[i*4+2] = byte(p)
		img.Pix[i*4+3] = byte(p >> 24)
	}
	var buf bytes.Buffer
	if err := png.Encode(&buf, img); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}
