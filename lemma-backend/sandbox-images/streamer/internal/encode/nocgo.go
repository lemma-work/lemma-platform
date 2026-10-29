//go:build !cgo || !linux

package encode

func newX264(w, h, fps, bps int) (Encoder, error) { return nil, ErrUnavailable }
func newVP8(w, h, fps, bps int) (Encoder, error)  { return nil, ErrUnavailable }
