//go:build linux

package session

import (
	"strings"
	"sync"

	"github.com/pion/ice/v4"
	"github.com/pion/interceptor"
	"github.com/pion/interceptor/pkg/cc"
	"github.com/pion/interceptor/pkg/gcc"
	"github.com/pion/sdp/v3"
	"github.com/pion/webrtc/v4"

	"lemma-streamer/internal/encode"
	"lemma-streamer/internal/rate"
)

// The only H.264 we offer: constrained baseline, packetization mode 1. It is
// what iOS Safari decodes in hardware, and every desktop browser takes it.
const h264Fmtp = "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f"

var feedback = []webrtc.RTCPFeedback{
	{Type: "goog-remb"}, {Type: "ccm", Parameter: "fir"},
	{Type: "nack"}, {Type: "nack", Parameter: "pli"}, {Type: "transport-cc"},
}

var capabilities = map[string]webrtc.RTPCodecCapability{
	encode.H264: {MimeType: webrtc.MimeTypeH264, ClockRate: 90000, SDPFmtpLine: h264Fmtp, RTCPFeedback: feedback},
	encode.VP8:  {MimeType: webrtc.MimeTypeVP8, ClockRate: 90000, RTCPFeedback: feedback},
}

// mediaAPI builds the Pion API: a codec list of exactly two, NACK, RTCP
// reports, and transport-wide congestion control feeding a per-peer
// bandwidth estimate.
type mediaAPI struct {
	api *webrtc.API

	mu        sync.Mutex // one NewPeerConnection at a time, so estimators pair up
	estimates chan cc.BandwidthEstimator
}

func newMedia() (*mediaAPI, error) {
	m := &webrtc.MediaEngine{}
	if err := m.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: capabilities[encode.H264], PayloadType: 102}, webrtc.RTPCodecTypeVideo); err != nil {
		return nil, err
	}
	if err := m.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: capabilities[encode.VP8], PayloadType: 96}, webrtc.RTPCodecTypeVideo); err != nil {
		return nil, err
	}
	reg := &interceptor.Registry{}
	if err := webrtc.ConfigureNack(m, reg); err != nil {
		return nil, err
	}
	if err := webrtc.ConfigureRTCPReports(reg); err != nil {
		return nil, err
	}
	md := &mediaAPI{estimates: make(chan cc.BandwidthEstimator, 1)}
	ccf, err := cc.NewInterceptor(func() (cc.BandwidthEstimator, error) {
		bwe, err := gcc.NewSendSideBWE(
			gcc.SendSideBWEInitialBitrate(rate.StartBitrate),
			gcc.SendSideBWEMinBitrate(rate.MinBitrate),
			gcc.SendSideBWEMaxBitrate(rate.MaxBitrateLarge),
		)
		if err != nil {
			return nil, err
		}
		return tolerantBWE{bwe}, nil
	})
	if err != nil {
		return nil, err
	}
	ccf.OnNewPeerConnection(func(_ string, e cc.BandwidthEstimator) {
		select {
		case md.estimates <- e:
		default:
		}
	})
	reg.Add(ccf)
	// After the congestion controller, so it wraps it: interceptors added
	// later run first on the way out, and the sequence number has to be on
	// the packet before the pacer and the feedback adapter look for it.
	if err := webrtc.ConfigureTWCCHeaderExtensionSender(m, reg); err != nil {
		return nil, err
	}
	se := webrtc.SettingEngine{}
	// Our own host candidates are plain addresses; a browser's mDNS ones are
	// still accepted when a test runs without TURN.
	se.SetICEMulticastDNSMode(ice.MulticastDNSModeQueryOnly)
	// Every path here is a relay: waiting for a better one only delays the
	// first frame.
	se.SetRelayAcceptanceMinWait(0)
	md.api = webrtc.NewAPI(webrtc.WithMediaEngine(m), webrtc.WithInterceptorRegistry(reg), webrtc.WithSettingEngine(se))
	return md, nil
}

// tolerantBWE leaves a stream unpaced when the viewer did not negotiate
// transport-wide sequence numbers. GCC refuses every packet of such a stream,
// and a viewer without TWCC should get video at the start bitrate rather than
// none.
type tolerantBWE struct{ *gcc.SendSideBWE }

func (t tolerantBWE) AddStream(info *interceptor.StreamInfo, w interceptor.RTPWriter) interceptor.RTPWriter {
	for _, e := range info.RTPHeaderExtensions {
		if e.URI == sdp.TransportCCURI {
			return t.SendSideBWE.AddStream(info, w)
		}
	}
	return w
}

// newPeerConnection returns the connection and its bandwidth estimator.
func (md *mediaAPI) newPeerConnection(cfg webrtc.Configuration) (*webrtc.PeerConnection, cc.BandwidthEstimator, error) {
	md.mu.Lock()
	defer md.mu.Unlock()
	select {
	case <-md.estimates:
	default:
	}
	pc, err := md.api.NewPeerConnection(cfg)
	if err != nil {
		return nil, nil, err
	}
	var est cc.BandwidthEstimator
	select {
	case est = <-md.estimates:
	default:
	}
	return pc, est, nil
}

// codecFor picks from what the offer carries: H.264 when it can be had,
// since that is what phones decode in hardware.
func codecFor(sdp string) string {
	lower := strings.ToLower(sdp)
	if strings.Contains(lower, "h264/90000") && strings.Contains(lower, "packetization-mode=1") {
		return encode.H264
	}
	if strings.Contains(lower, "vp8/90000") {
		return encode.VP8
	}
	return ""
}
