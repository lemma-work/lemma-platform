//go:build cgo && linux

package encode

import (
	"bytes"
	"testing"

	"lemma-streamer/internal/convert"
)

func frame(w, h, shift int) *convert.I420 {
	f := convert.NewI420(w, h)
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			f.Y[y*w+x] = byte((x + y + shift) % 220)
		}
	}
	for i := range f.U {
		f.U[i], f.V[i] = 128, 128
	}
	return f
}

// nalTypes lists the NAL unit types in an Annex-B access unit.
func nalTypes(au []byte) []byte {
	var types []byte
	for i := 0; i+3 < len(au); i++ {
		if au[i] == 0 && au[i+1] == 0 && au[i+2] == 1 {
			types = append(types, au[i+3]&0x1f)
			i += 3
		}
	}
	return types
}

func TestH264KeyframesOnDemand(t *testing.T) {
	enc, err := New(H264, 641, 361, 30, 1_000_000) // odd: cropped to even
	if err != nil {
		t.Fatal(err)
	}
	defer enc.Close()
	first, err := enc.Encode(frame(641, 361, 0), false)
	if err != nil || !bytes.HasPrefix(first, []byte{0, 0, 0, 1}) {
		t.Fatalf("first AU %x… err %v", first[:min(8, len(first))], err)
	}
	if !bytes.Contains(nalTypes(first), []byte{7}) || !bytes.Contains(nalTypes(first), []byte{5}) {
		t.Fatalf("first AU has NALs %v, want SPS and IDR", nalTypes(first))
	}
	p, _ := enc.Encode(frame(641, 361, 3), false)
	if bytes.Contains(nalTypes(p), []byte{5}) {
		t.Fatal("second frame was an IDR unasked")
	}
	enc.SetBitrate(500_000)
	k, _ := enc.Encode(frame(641, 361, 6), true)
	if !bytes.Contains(nalTypes(k), []byte{5}) {
		t.Fatalf("forced frame NALs %v, want IDR", nalTypes(k))
	}
	// Constrained baseline: profile_idc 66 with constraint_set1.
	sps := bytes.Index(first, []byte{0, 0, 1, 0x67})
	if sps < 0 || first[sps+4] != 66 {
		t.Fatalf("profile_idc %d, want 66 (baseline)", first[sps+4])
	}
}

func TestVP8KeyframesOnDemand(t *testing.T) {
	enc, err := New(VP8, 320, 240, 30, 800_000)
	if err != nil {
		t.Fatal(err)
	}
	defer enc.Close()
	isKey := func(b []byte) bool { return len(b) > 0 && b[0]&1 == 0 }
	first, err := enc.Encode(frame(320, 240, 0), false)
	if err != nil || !isKey(first) {
		t.Fatalf("first frame not a keyframe (err %v)", err)
	}
	p, _ := enc.Encode(frame(320, 240, 2), false)
	if isKey(p) {
		t.Fatal("second frame was a keyframe unasked")
	}
	enc.SetBitrate(300_000)
	k, _ := enc.Encode(frame(320, 240, 4), true)
	if !isKey(k) {
		t.Fatal("forced frame not a keyframe")
	}
}
