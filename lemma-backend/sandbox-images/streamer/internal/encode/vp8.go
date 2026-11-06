//go:build cgo && linux

package encode

/*
#cgo LDFLAGS: -Wl,-Bstatic -lvpx -Wl,-Bdynamic -lm -lpthread
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <vpx/vpx_encoder.h>
#include <vpx/vp8cx.h>

typedef struct {
	vpx_codec_ctx_t ctx;
	vpx_codec_enc_cfg_t cfg;
	vpx_image_t img;
	uint8_t *buf;
	int64_t pts;
	uint8_t *out;
	size_t out_cap;
	int open;
} lemma_vp8;

static int lemma_vp8_open(lemma_vp8 *e, int w, int h, int fps, int kbps) {
	memset(e, 0, sizeof(*e));
	if (vpx_codec_enc_config_default(vpx_codec_vp8_cx(), &e->cfg, 0)) return -1;
	e->cfg.g_w = w;
	e->cfg.g_h = h;
	e->cfg.g_timebase.num = 1;
	e->cfg.g_timebase.den = fps;
	e->cfg.rc_target_bitrate = kbps;
	e->cfg.rc_end_usage = VPX_CBR;
	e->cfg.g_lag_in_frames = 0;
	e->cfg.g_error_resilient = VPX_ERROR_RESILIENT_DEFAULT;
	e->cfg.rc_dropframe_thresh = 0;
	e->cfg.rc_min_quantizer = 4;
	e->cfg.rc_max_quantizer = 56;
	e->cfg.rc_undershoot_pct = 100;
	e->cfg.rc_overshoot_pct = 15;
	e->cfg.rc_buf_initial_sz = 500;
	e->cfg.rc_buf_optimal_sz = 600;
	e->cfg.rc_buf_sz = 1000;
	e->cfg.kf_mode = VPX_KF_AUTO;
	e->cfg.kf_max_dist = 300;
	e->cfg.g_threads = 2;
	if (vpx_codec_enc_init(&e->ctx, vpx_codec_vp8_cx(), &e->cfg, 0)) return -1;
	e->open = 1;
	vpx_codec_control(&e->ctx, VP8E_SET_CPUUSED, 8);
	vpx_codec_control(&e->ctx, VP8E_SET_NOISE_SENSITIVITY, 0);
	vpx_codec_control(&e->ctx, VP8E_SET_STATIC_THRESHOLD, 1);
	size_t ysize = (size_t)w * h, csize = (size_t)(w / 2) * (h / 2);
	e->buf = malloc(ysize + 2 * csize);
	if (!e->buf) return -1;
	if (!vpx_img_wrap(&e->img, VPX_IMG_FMT_I420, w, h, 1, e->buf)) return -1;
	return 0;
}

static uint8_t *lemma_vp8_plane(lemma_vp8 *e, int i) { return e->img.planes[i]; }

// Encodes the wrapped image and gathers every frame packet into one buffer.
static int lemma_vp8_encode(lemma_vp8 *e, int force, uint8_t **out) {
	vpx_enc_frame_flags_t flags = force ? VPX_EFLAG_FORCE_KF : 0;
	if (vpx_codec_encode(&e->ctx, &e->img, e->pts++, 1, flags, VPX_DL_REALTIME)) return -1;
	vpx_codec_iter_t iter = NULL;
	const vpx_codec_cx_pkt_t *pkt;
	size_t n = 0;
	while ((pkt = vpx_codec_get_cx_data(&e->ctx, &iter)) != NULL) {
		if (pkt->kind != VPX_CODEC_CX_FRAME_PKT) continue;
		size_t need = n + pkt->data.frame.sz;
		if (need > e->out_cap) {
			uint8_t *grown = realloc(e->out, need * 2);
			if (!grown) return -1;
			e->out = grown;
			e->out_cap = need * 2;
		}
		memcpy(e->out + n, pkt->data.frame.buf, pkt->data.frame.sz);
		n = need;
	}
	*out = e->out;
	return (int)n;
}

static int lemma_vp8_bitrate(lemma_vp8 *e, int kbps) {
	e->cfg.rc_target_bitrate = kbps;
	return vpx_codec_enc_config_set(&e->ctx, &e->cfg);
}

static void lemma_vp8_close(lemma_vp8 *e) {
	if (e->open) vpx_codec_destroy(&e->ctx);
	free(e->buf);
	free(e->out);
	e->open = 0;
	e->buf = NULL;
	e->out = NULL;
}
*/
import "C"

import (
	"errors"
	"unsafe"

	"lemma-streamer/internal/convert"
)

type vp8 struct {
	e      *C.lemma_vp8
	w, h   int
	planes [3][]byte
}

func newVP8(w, h, fps, bps int) (Encoder, error) {
	e := (*C.lemma_vp8)(C.calloc(1, C.size_t(unsafe.Sizeof(C.lemma_vp8{}))))
	if C.lemma_vp8_open(e, C.int(w), C.int(h), C.int(fps), C.int(bps/1000)) != 0 {
		C.lemma_vp8_close(e)
		C.free(unsafe.Pointer(e))
		return nil, errors.New("libvpx VP8 would not open")
	}
	v := &vp8{e: e, w: w, h: h}
	v.planes[0] = unsafe.Slice((*byte)(unsafe.Pointer(C.lemma_vp8_plane(e, 0))), w*h)
	v.planes[1] = unsafe.Slice((*byte)(unsafe.Pointer(C.lemma_vp8_plane(e, 1))), (w/2)*(h/2))
	v.planes[2] = unsafe.Slice((*byte)(unsafe.Pointer(C.lemma_vp8_plane(e, 2))), (w/2)*(h/2))
	return v, nil
}

func (v *vp8) Encode(f *convert.I420, forceKey bool) ([]byte, error) {
	copyPlanes(f, v.planes, v.w, v.h)
	var out *C.uint8_t
	force := C.int(0)
	if forceKey {
		force = 1
	}
	n := C.lemma_vp8_encode(v.e, force, &out)
	if n < 0 {
		return nil, errors.New("VP8 encode failed")
	}
	if n == 0 {
		return nil, nil
	}
	return C.GoBytes(unsafe.Pointer(out), n), nil
}

func (v *vp8) SetBitrate(bps int) { C.lemma_vp8_bitrate(v.e, C.int(bps/1000)) }

func (v *vp8) Close() {
	if v.e != nil {
		C.lemma_vp8_close(v.e)
		C.free(unsafe.Pointer(v.e))
		v.e = nil
	}
}
