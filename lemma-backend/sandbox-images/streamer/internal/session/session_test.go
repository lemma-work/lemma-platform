//go:build linux

package session

import (
	"strings"
	"testing"

	"github.com/pion/webrtc/v4"
)

func TestCodecFor(t *testing.T) {
	cases := map[string]string{
		"a=rtpmap:102 H264/90000\r\na=fmtp:102 packetization-mode=1;profile-level-id=42e01f\r\na=rtpmap:96 VP8/90000": "h264",
		"a=rtpmap:102 H264/90000\r\na=fmtp:102 packetization-mode=0;profile-level-id=42e01f\r\na=rtpmap:96 VP8/90000": "vp8",
		"a=rtpmap:96 VP8/90000": "vp8",
		"a=rtpmap:98 VP9/90000": "",
	}
	for sdp, want := range cases {
		if got := codecFor(sdp); got != want {
			t.Errorf("codecFor(%q) = %q, want %q", sdp, got, want)
		}
	}
}

func TestICEConfig(t *testing.T) {
	ok := signal{ICEServers: []iceServer{{URLs: []string{"turns:turn.example:443?transport=tcp"}, Username: "u", Credential: "c"}}}
	cfg, err := iceConfig(ok)
	if err != nil || cfg.ICETransportPolicy != webrtc.ICETransportPolicyRelay || len(cfg.ICEServers) != 1 {
		t.Fatalf("default policy: %+v %v", cfg, err)
	}
	ok.Policy = "all"
	if cfg, _ := iceConfig(ok); cfg.ICETransportPolicy != webrtc.ICETransportPolicyAll {
		t.Fatal("policy all not honoured")
	}
	bad := []signal{
		{Policy: "none"},
		{ICEServers: []iceServer{{URLs: []string{"http://evil"}}}},
		{ICEServers: []iceServer{{}}},
		{ICEServers: []iceServer{{URLs: []string{"turn:" + strings.Repeat("a", 600)}}}},
		{ICEServers: make([]iceServer, maxICEServers+1)},
	}
	for _, s := range bad {
		if _, err := iceConfig(s); err == nil {
			t.Errorf("accepted %+v", s)
		}
	}
}
