// Package encode wraps the two video encoders a browser can play without a
// plug-in: H.264 constrained baseline (what Safari and iOS want) and VP8
// (what every WebRTC stack must accept).
//
// In-process, so the streamer owns the pacing: it can drop a frame instead of
// queueing it, ask for a keyframe the moment a viewer reports loss, and move
// the bitrate when the network estimate moves. A piped ffmpeg could do none
// of those, and queued up to seconds behind the screen under load.
package encode

import (
	"errors"

	"lemma-streamer/internal/convert"
)

// Codec names, as negotiated.
const (
	H264 = "h264"
	VP8  = "vp8"
)

// Encoder turns I420 frames into one access unit each. Not safe for
// concurrent use: the stream drives it from one goroutine.
type Encoder interface {
	// Encode returns the encoded frame; a nil slice means the encoder chose
	// to emit nothing for this input.
	Encode(frame *convert.I420, forceKey bool) ([]byte, error)
	SetBitrate(bps int)
	Close()
}

// ErrUnavailable is what a build without cgo answers, so the pure packages
// still build and test anywhere.
var ErrUnavailable = errors.New("encoders are not built into this binary (cgo disabled)")

// New opens an encoder for frames of w x h. Odd sizes are cropped to even,
// which 4:2:0 requires; the lost column or row is never the one anybody reads.
func New(codec string, w, h, fps, bps int) (Encoder, error) {
	w, h = w&^1, h&^1
	if w < 2 || h < 2 {
		return nil, errors.New("frame too small to encode")
	}
	switch codec {
	case H264:
		return newX264(w, h, fps, bps)
	case VP8:
		return newVP8(w, h, fps, bps)
	}
	return nil, errors.New("unknown codec " + codec)
}
