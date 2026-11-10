//go:build cgo && linux

package encode

/*
#cgo LDFLAGS: -Wl,-Bstatic -lx264 -Wl,-Bdynamic -lm -lpthread -ldl
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <x264.h>

typedef struct {
	x264_t *h;
	x264_param_t p;
	x264_picture_t pic;
	int64_t pts;
	uint8_t *planes[3];
} lemma_x264;

static int lemma_x264_open(lemma_x264 *e, int w, int h, int fps, int kbps) {
	memset(e, 0, sizeof(*e));
	if (x264_param_default_preset(&e->p, "ultrafast", "zerolatency") < 0) return -1;
	e->p.i_width = w;
	e->p.i_height = h;
	e->p.i_csp = X264_CSP_I420;
	e->p.i_fps_num = fps;
	e->p.i_fps_den = 1;
	e->p.i_threads = 2;
	e->p.i_keyint_max = 300;
	e->p.i_keyint_min = 30;
	e->p.i_bframe = 0;
	e->p.b_repeat_headers = 1;
	e->p.b_annexb = 1;
	e->p.b_aud = 0;
	e->p.i_log_level = X264_LOG_ERROR;
	e->p.rc.i_rc_method = X264_RC_ABR;
	e->p.rc.i_bitrate = kbps;
	e->p.rc.i_vbv_max_bitrate = kbps;
	e->p.rc.i_vbv_buffer_size = kbps * 2 / fps > 50 ? kbps * 2 / fps : 50;
	if (x264_param_apply_profile(&e->p, "baseline") < 0) return -1;
	e->h = x264_encoder_open(&e->p);
	if (!e->h) return -1;
	x264_picture_init(&e->pic);
	e->pic.img.i_csp = X264_CSP_I420;
	e->pic.img.i_plane = 3;
	int cw = w / 2, ch = h / 2;
	e->planes[0] = malloc((size_t)w * h);
	e->planes[1] = malloc((size_t)cw * ch);
	e->planes[2] = malloc((size_t)cw * ch);
	if (!e->planes[0] || !e->planes[1] || !e->planes[2]) return -1;
	e->pic.img.plane[0] = e->planes[0];
	e->pic.img.plane[1] = e->planes[1];
	e->pic.img.plane[2] = e->planes[2];
	e->pic.img.i_stride[0] = w;
	e->pic.img.i_stride[1] = cw;
	e->pic.img.i_stride[2] = cw;
	return 0;
}

// Encodes the picture already copied into planes. Returns the access unit's
// size and points *out at x264's buffer, valid until the next call.
static int lemma_x264_encode(lemma_x264 *e, int force, uint8_t **out) {
	x264_nal_t *nals;
	int n = 0;
	x264_picture_t pout;
	e->pic.i_type = force ? X264_TYPE_IDR : X264_TYPE_AUTO;
	e->pic.i_pts = e->pts++;
	int size = x264_encoder_encode(e->h, &nals, &n, &e->pic, &pout);
	if (size > 0 && n > 0) *out = nals[0].p_payload;
	return size;
}

static int lemma_x264_bitrate(lemma_x264 *e, int kbps, int fps) {
	x264_encoder_parameters(e->h, &e->p);
	e->p.rc.i_bitrate = kbps;
	e->p.rc.i_vbv_max_bitrate = kbps;
	e->p.rc.i_vbv_buffer_size = kbps * 2 / fps > 50 ? kbps * 2 / fps : 50;
	return x264_encoder_reconfig(e->h, &e->p);
}

static void lemma_x264_close(lemma_x264 *e) {
	if (e->h) x264_encoder_close(e->h);
	for (int i = 0; i < 3; i++) free(e->planes[i]);
	e->h = NULL;
}
*/
import "C"

import (
	"errors"
	"unsafe"

	"lemma-streamer/internal/convert"
)

type x264 struct {
	e      *C.lemma_x264
	w, h   int
	fps    int
	planes [3][]byte
}

func newX264(w, h, fps, bps int) (Encoder, error) {
	e := (*C.lemma_x264)(C.calloc(1, C.size_t(unsafe.Sizeof(C.lemma_x264{}))))
	if C.lemma_x264_open(e, C.int(w), C.int(h), C.int(fps), C.int(bps/1000)) != 0 {
		C.lemma_x264_close(e)
		C.free(unsafe.Pointer(e))
		return nil, errors.New("x264 would not open")
	}
	x := &x264{e: e, w: w, h: h, fps: fps}
	x.planes[0] = unsafe.Slice((*byte)(unsafe.Pointer(e.planes[0])), w*h)
	x.planes[1] = unsafe.Slice((*byte)(unsafe.Pointer(e.planes[1])), (w/2)*(h/2))
	x.planes[2] = unsafe.Slice((*byte)(unsafe.Pointer(e.planes[2])), (w/2)*(h/2))
	return x, nil
}

func (x *x264) Encode(f *convert.I420, forceKey bool) ([]byte, error) {
	copyPlanes(f, x.planes, x.w, x.h)
	var out *C.uint8_t
	force := C.int(0)
	if forceKey {
		force = 1
	}
	n := C.lemma_x264_encode(x.e, force, &out)
	if n < 0 {
		return nil, errors.New("x264 encode failed")
	}
	if n == 0 || out == nil {
		return nil, nil
	}
	return C.GoBytes(unsafe.Pointer(out), n), nil
}

func (x *x264) SetBitrate(bps int) { C.lemma_x264_bitrate(x.e, C.int(bps/1000), C.int(x.fps)) }

func (x *x264) Close() {
	if x.e != nil {
		C.lemma_x264_close(x.e)
		C.free(unsafe.Pointer(x.e))
		x.e = nil
	}
}

// copyPlanes copies a converted frame into an encoder's even-sized planes.
func copyPlanes(f *convert.I420, dst [3][]byte, w, h int) {
	for y := 0; y < h; y++ {
		copy(dst[0][y*w:y*w+w], f.Y[y*f.StrideY:y*f.StrideY+w])
	}
	cw, ch := w/2, h/2
	for y := 0; y < ch; y++ {
		copy(dst[1][y*cw:y*cw+cw], f.U[y*f.StrideC:y*f.StrideC+cw])
		copy(dst[2][y*cw:y*cw+cw], f.V[y*f.StrideC:y*f.StrideC+cw])
	}
}
