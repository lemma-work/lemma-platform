// Package convert turns the X server's BGRA framebuffer into I420, the planar
// 4:2:0 layout both encoders take.
//
// Pure Go rather than libswscale: the streamer then links nothing but the two
// codecs, which matters because the E2B template and the Docker image are
// different distributions with different libswscale sonames. The cost is a
// few milliseconds a frame, split across cores by row.
package convert

import (
	"runtime"
	"sync"
)

// I420 is one frame: a full-size luma plane and two half-size chroma planes.
type I420 struct {
	W, H             int
	Y, U, V          []byte
	StrideY, StrideC int
}

// NewI420 allocates a frame. Chroma is rounded up so odd sizes keep their
// last column and row.
func NewI420(w, h int) *I420 {
	cw, ch := (w+1)/2, (h+1)/2
	return &I420{
		W: w, H: h,
		Y: make([]byte, w*h), U: make([]byte, cw*ch), V: make([]byte, cw*ch),
		StrideY: w, StrideC: cw,
	}
}

// BT.601 limited range, in 8.8 fixed point -- what every browser decoder
// assumes when a stream carries no colour description.
func luma(r, g, b int32) byte    { return byte((66*r+129*g+25*b+128)>>8 + 16) }
func chromaU(r, g, b int32) byte { return byte((-38*r-74*g+112*b+128)>>8 + 128) }
func chromaV(r, g, b int32) byte { return byte((112*r-94*g-18*b+128)>>8 + 128) }

// BGRAToI420 converts src (stride bytes per row, 4 bytes per pixel, B G R X)
// into dst, which must be NewI420(w, h) sized.
func BGRAToI420(src []byte, stride int, dst *I420) {
	pairs := (dst.H + 1) / 2
	workers := runtime.GOMAXPROCS(0)
	if workers > 4 {
		workers = 4
	}
	if workers < 1 || pairs < 64 {
		workers = 1
	}
	if workers == 1 {
		rows(src, stride, dst, 0, pairs)
		return
	}
	var wg sync.WaitGroup
	per := (pairs + workers - 1) / workers
	for start := 0; start < pairs; start += per {
		end := start + per
		if end > pairs {
			end = pairs
		}
		wg.Add(1)
		go func(a, b int) {
			defer wg.Done()
			rows(src, stride, dst, a, b)
		}(start, end)
	}
	wg.Wait()
}

// rows converts row pairs [from, to): each pair yields two luma rows and one
// chroma row, the chroma sample being the 2x2 average.
func rows(src []byte, stride int, dst *I420, from, to int) {
	w, h := dst.W, dst.H
	cw := dst.StrideC
	for p := from; p < to; p++ {
		y0 := p * 2
		y1 := y0 + 1
		if y1 >= h {
			y1 = y0
		}
		r0 := src[y0*stride : y0*stride+w*4]
		r1 := src[y1*stride : y1*stride+w*4]
		l0 := dst.Y[y0*w : y0*w+w]
		var l1 []byte
		if y1 != y0 {
			l1 = dst.Y[y1*w : y1*w+w]
		}
		u := dst.U[p*cw : p*cw+cw]
		v := dst.V[p*cw : p*cw+cw]
		for cx := 0; cx < cw; cx++ {
			x0 := cx * 2
			x1 := x0 + 1
			if x1 >= w {
				x1 = x0
			}
			i00, i01 := x0*4, x1*4
			b00, g00, rr00 := int32(r0[i00]), int32(r0[i00+1]), int32(r0[i00+2])
			b01, g01, rr01 := int32(r0[i01]), int32(r0[i01+1]), int32(r0[i01+2])
			b10, g10, rr10 := int32(r1[i00]), int32(r1[i00+1]), int32(r1[i00+2])
			b11, g11, rr11 := int32(r1[i01]), int32(r1[i01+1]), int32(r1[i01+2])
			l0[x0] = luma(rr00, g00, b00)
			if x1 != x0 {
				l0[x1] = luma(rr01, g01, b01)
			}
			if l1 != nil {
				l1[x0] = luma(rr10, g10, b10)
				if x1 != x0 {
					l1[x1] = luma(rr11, g11, b11)
				}
			}
			r := (rr00 + rr01 + rr10 + rr11 + 2) >> 2
			g := (g00 + g01 + g10 + g11 + 2) >> 2
			b := (b00 + b01 + b10 + b11 + 2) >> 2
			u[cx] = chromaU(r, g, b)
			v[cx] = chromaV(r, g, b)
		}
	}
}
