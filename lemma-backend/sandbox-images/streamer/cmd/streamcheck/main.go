// streamcheck is a headless viewer for the browser stream: it connects the
// way a browser does, counts what arrives, and prints one JSON line.
//
// Two ways in:
//
//   - through the API, as the web app does:
//     streamcheck -api http://localhost:8000 -token T -mode view
//     The API sends the ICE configuration first and mints every ticket; this
//     client never sees one.
//   - straight to a streamer, with a ticket you minted:
//     streamcheck -signal ws://127.0.0.1:4860/signal -ticket T -ice '[...]'
//
// Built without cgo (it decodes nothing) so it drops into any container.
package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/coder/websocket"
	"github.com/pion/interceptor"
	"github.com/pion/webrtc/v4"
)

type result struct {
	Connected     bool   `json:"connected"`
	ICEMs         int64  `json:"ice_ms"`
	FirstFrameMs  int64  `json:"first_frame_ms"`
	Frames        int64  `json:"frames"`
	Bytes         int64  `json:"bytes"`
	Codec         string `json:"codec"`
	SelectedPair  string `json:"selected_pair"`
	InputChannel  string `json:"input_channel"`
	Mode          string `json:"mode"`
	ModeAfterTake string `json:"mode_after_take,omitempty"`
	Revoked       bool   `json:"revoked"`
	Events        int64  `json:"events"`
	Width         int    `json:"width,omitempty"`
	Height        int    `json:"height,omitempty"`
	CloseCode     int    `json:"close_code,omitempty"`
	Error         string `json:"error,omitempty"`
}

type msg struct {
	T             string      `json:"t"`
	Ticket        string      `json:"ticket,omitempty"`
	ICEServers    []iceServer `json:"ice_servers,omitempty"`
	Policy        string      `json:"policy,omitempty"`
	Mode          string      `json:"mode,omitempty"`
	SDP           string      `json:"sdp,omitempty"`
	Candidate     *string     `json:"candidate,omitempty"`
	SDPMid        *string     `json:"sdpMid,omitempty"`
	SDPMLineIndex *uint16     `json:"sdpMLineIndex,omitempty"`
	Code          any         `json:"code,omitempty"`
	Message       string      `json:"message,omitempty"`
	Codec         string      `json:"codec,omitempty"`
}

// iceServer is the wire shape the API and the streamer agree on.
type iceServer struct {
	URLs       []string `json:"urls"`
	Username   string   `json:"username,omitempty"`
	Credential string   `json:"credential,omitempty"`
}

func main() {
	var (
		api           = flag.String("api", "", "API base URL (http[s]://host[:port]); connects through /machines/me/browser/view")
		path          = flag.String("path", "/machines/me/browser/view", "the view socket's path on the API")
		token         = flag.String("token", "", "bearer token for the API")
		origin        = flag.String("origin", "", "Origin header to send to the API")
		mode          = flag.String("mode", "view", "view or control")
		signalURL     = flag.String("signal", "", "direct streamer signalling URL (ws://127.0.0.1:4860/signal)")
		ticket        = flag.String("ticket", "", "ticket for -signal")
		controlTicket = flag.String("control-ticket", "", "ticket sent as `control` with -take in -signal mode")
		iceJSON       = flag.String("ice", "", "ICE servers JSON for -signal mode")
		seconds       = flag.Float64("seconds", 5, "how long to watch")
		tryInput      = flag.Bool("try-input", false, "open the input channel and send a pointer move")
		move          = flag.String("move", "", "x,y to move the pointer to once input is open")
		typeText      = flag.String("type", "", "text to send as `txt` once input is open")
		take          = flag.Bool("take", false, "ask for control mid-session")
		codec         = flag.String("codec", "", "offer only this codec: h264 or vp8")
		relayOnly     = flag.Bool("relay-only", false, "iceTransportPolicy relay on this side")
		policy        = flag.String("policy", "relay", "policy sent to the streamer in -signal mode")
		_             = flag.Bool("json", true, "print JSON (always on)")
	)
	flag.Parse()
	res := run(config{
		api: *api, path: *path, token: *token, origin: *origin, mode: *mode,
		signal: *signalURL, ticket: *ticket, controlTicket: *controlTicket, ice: *iceJSON,
		seconds: *seconds, tryInput: *tryInput, move: *move, typeText: *typeText, take: *take,
		codec: *codec, relayOnly: *relayOnly, policy: *policy,
	})
	out, _ := json.Marshal(res)
	fmt.Println(string(out))
	if res.Error != "" {
		os.Exit(1)
	}
}

type config struct {
	api, path, token, origin, mode     string
	signal, ticket, controlTicket, ice string
	seconds                            float64
	tryInput                           bool
	move, typeText                     string
	take                               bool
	codec                              string
	relayOnly                          bool
	policy                             string
}

func randomID() string {
	b := make([]byte, 8)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

func dialURL(c config) (string, http.Header, error) {
	h := http.Header{}
	if c.signal != "" {
		return c.signal, h, nil
	}
	if c.api == "" {
		return "", nil, errors.New("give -api or -signal")
	}
	u, err := url.Parse(strings.TrimRight(c.api, "/") + c.path)
	if err != nil {
		return "", nil, err
	}
	switch u.Scheme {
	case "http":
		u.Scheme = "ws"
	case "https":
		u.Scheme = "wss"
	}
	q := u.Query()
	q.Set("transport", "rtc")
	q.Set("mode", c.mode)
	q.Set("viewer", randomID())
	if c.token != "" {
		q.Set("access_token", c.token)
	}
	u.RawQuery = q.Encode()
	if c.origin != "" {
		h.Set("Origin", c.origin)
	}
	return u.String(), h, nil
}

func run(c config) (res result) {
	res.InputChannel = "none"
	start := time.Now()
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(c.seconds*float64(time.Second))+20*time.Second)
	defer cancel()

	target, header, err := dialURL(c)
	if err != nil {
		res.Error = err.Error()
		return
	}
	ws, _, err := websocket.Dial(ctx, target, &websocket.DialOptions{HTTPHeader: header})
	if err != nil {
		res.Error = "dial: " + err.Error()
		return
	}
	ws.SetReadLimit(1 << 20)
	defer ws.CloseNow()
	var wmu sync.Mutex
	send := func(m msg) {
		b, _ := json.Marshal(m)
		wmu.Lock()
		defer wmu.Unlock()
		_ = ws.Write(ctx, websocket.MessageText, b)
	}
	read := func() (msg, error) {
		var m msg
		_, raw, err := ws.Read(ctx)
		if err != nil {
			return m, err
		}
		return m, json.Unmarshal(raw, &m)
	}
	closeCode := func(err error) {
		if code := websocket.CloseStatus(err); code != -1 {
			res.CloseCode = int(code)
		}
	}

	// The ICE configuration: the API sends it; in direct mode it is a flag
	// and this client says hello itself.
	var iceServers []iceServer
	if c.signal != "" {
		if c.ice != "" {
			if err := json.Unmarshal([]byte(c.ice), &iceServers); err != nil {
				res.Error = "bad -ice: " + err.Error()
				return
			}
		}
		send(msg{T: "hello", Ticket: c.ticket, ICEServers: iceServers, Policy: c.policy})
	} else {
		first, err := read()
		if err != nil {
			closeCode(err)
			res.Error = "no config: " + err.Error()
			return
		}
		if first.T != "config" {
			res.Error = "first message was " + first.T + ", not config"
			return
		}
		iceServers = first.ICEServers
	}
	ready, err := read()
	if err != nil {
		closeCode(err)
		res.Error = "no ready: " + err.Error()
		return
	}
	if ready.T == "error" {
		res.Error = fmt.Sprintf("refused: %v %s", ready.Code, ready.Message)
		return
	}
	if ready.T != "ready" {
		res.Error = "expected ready, got " + ready.T
		return
	}
	res.Mode = ready.Mode

	m := &webrtc.MediaEngine{}
	if c.codec != "vp8" {
		_ = m.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: webrtc.RTPCodecCapability{
			MimeType: webrtc.MimeTypeH264, ClockRate: 90000,
			SDPFmtpLine:  "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f",
			RTCPFeedback: []webrtc.RTCPFeedback{{Type: "nack"}, {Type: "nack", Parameter: "pli"}, {Type: "transport-cc"}},
		}, PayloadType: 102}, webrtc.RTPCodecTypeVideo)
	}
	if c.codec != "h264" {
		_ = m.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: webrtc.RTPCodecCapability{
			MimeType: webrtc.MimeTypeVP8, ClockRate: 90000,
			RTCPFeedback: []webrtc.RTCPFeedback{{Type: "nack"}, {Type: "nack", Parameter: "pli"}, {Type: "transport-cc"}},
		}, PayloadType: 96}, webrtc.RTPCodecTypeVideo)
	}
	cfg := webrtc.Configuration{}
	for _, s := range iceServers {
		cfg.ICEServers = append(cfg.ICEServers, webrtc.ICEServer{URLs: s.URLs, Username: s.Username, Credential: s.Credential})
	}
	if c.relayOnly {
		cfg.ICETransportPolicy = webrtc.ICETransportPolicyRelay
	}
	// The browser's defaults: NACK, RTCP reports and transport-wide feedback,
	// which the streamer's congestion control reads.
	reg := &interceptor.Registry{}
	if err := webrtc.RegisterDefaultInterceptors(m, reg); err != nil {
		res.Error = err.Error()
		return
	}
	// Browsers take a relay pair as soon as it works; Pion waits 2 s for a
	// better one by default, which would read as slow ICE.
	se := webrtc.SettingEngine{}
	se.SetRelayAcceptanceMinWait(0)
	pc, err := webrtc.NewAPI(webrtc.WithMediaEngine(m), webrtc.WithInterceptorRegistry(reg), webrtc.WithSettingEngine(se)).NewPeerConnection(cfg)
	if err != nil {
		res.Error = err.Error()
		return
	}
	defer pc.Close()
	if _, err := pc.AddTransceiverFromKind(webrtc.RTPCodecTypeVideo, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionRecvonly}); err != nil {
		res.Error = err.Error()
		return
	}

	var frames, bytes, events atomic.Int64
	var firstFrame atomic.Int64
	var mu sync.Mutex
	pc.OnTrack(func(t *webrtc.TrackRemote, _ *webrtc.RTPReceiver) {
		mu.Lock()
		res.Codec = strings.ToLower(strings.TrimPrefix(t.Codec().MimeType, "video/"))
		mu.Unlock()
		for {
			pkt, _, err := t.ReadRTP()
			if err != nil {
				return
			}
			bytes.Add(int64(len(pkt.Payload)))
			if pkt.Marker {
				if frames.Add(1) == 1 {
					firstFrame.Store(time.Since(start).Milliseconds())
				}
			}
		}
	})

	evc, err := pc.CreateDataChannel("events", nil)
	if err != nil {
		res.Error = err.Error()
		return
	}
	evc.OnMessage(func(dm webrtc.DataChannelMessage) {
		events.Add(1)
		var e struct {
			T    string `json:"t"`
			W, H int
		}
		if json.Unmarshal(dm.Data, &e) == nil {
			mu.Lock()
			switch e.T {
			case "hello", "size":
				res.Width, res.Height = e.W, e.H
			case "revoked":
				res.Revoked = true
			}
			mu.Unlock()
		}
	})

	// The input channel's fate is read off its state at the end: a streamer
	// refusing it closes it right after it opens.
	var inputDC atomic.Pointer[webrtc.DataChannel]
	openInput := func() {
		dc, err := pc.CreateDataChannel("input", nil)
		if err != nil {
			return
		}
		inputDC.Store(dc)
		dc.OnOpen(func() {
			if c.move != "" {
				var x, y int
				if _, err := fmt.Sscanf(c.move, "%d,%d", &x, &y); err == nil {
					_ = dc.SendText(fmt.Sprintf(`{"t":"m","x":%d,"y":%d}`, x, y))
				}
			} else {
				_ = dc.SendText(`{"t":"m","x":1,"y":1}`)
			}
			if c.typeText != "" {
				b, _ := json.Marshal(map[string]string{"t": "txt", "s": c.typeText})
				_ = dc.SendText(string(b))
			}
		})
	}
	if c.tryInput {
		openInput()
	}

	pc.OnICECandidate(func(cand *webrtc.ICECandidate) {
		if cand == nil {
			send(msg{T: "candidate"})
			return
		}
		j := cand.ToJSON()
		send(msg{T: "candidate", Candidate: &j.Candidate, SDPMid: j.SDPMid, SDPMLineIndex: j.SDPMLineIndex})
	})
	connected := make(chan struct{})
	var once sync.Once
	pc.OnConnectionStateChange(func(s webrtc.PeerConnectionState) {
		if s == webrtc.PeerConnectionStateConnected {
			once.Do(func() { close(connected) })
		}
	})

	offer, err := pc.CreateOffer(nil)
	if err != nil {
		res.Error = err.Error()
		return
	}
	if err := pc.SetLocalDescription(offer); err != nil {
		res.Error = err.Error()
		return
	}
	send(msg{T: "offer", SDP: offer.SDP})

	modes := make(chan string, 4)
	readErr := make(chan error, 1)
	go func() {
		for {
			m, err := read()
			if err != nil {
				readErr <- err
				return
			}
			switch m.T {
			case "answer":
				if err := pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeAnswer, SDP: m.SDP}); err != nil {
					readErr <- err
					return
				}
			case "candidate":
				if m.Candidate != nil && *m.Candidate != "" {
					_ = pc.AddICECandidate(webrtc.ICECandidateInit{Candidate: *m.Candidate, SDPMid: m.SDPMid, SDPMLineIndex: m.SDPMLineIndex})
				}
			case "mode":
				modes <- m.Mode
			case "error":
				readErr <- fmt.Errorf("streamer error: %v %s", m.Code, m.Message)
				return
			}
		}
	}()

	iceStart := time.Now()
	select {
	case <-connected:
		res.Connected = true
		res.ICEMs = time.Since(iceStart).Milliseconds()
	case err := <-readErr:
		closeCode(err)
		res.Error = err.Error()
		return
	case <-time.After(15 * time.Second):
		res.Error = "ICE did not connect within 15 s"
		return
	}

	watch := time.After(time.Duration(c.seconds * float64(time.Second)))
	if c.take {
		if c.signal != "" {
			send(msg{T: "control", Ticket: c.controlTicket})
		} else {
			send(msg{T: "take"})
		}
	}
	done := false
	for !done {
		select {
		case m := <-modes:
			if c.take && res.ModeAfterTake == "" {
				res.ModeAfterTake = m
				if m == "control" && c.tryInput {
					openInput()
				}
			}
			if m == "view" && res.ModeAfterTake == "control" {
				res.Revoked = true
			}
		case err := <-readErr:
			closeCode(err)
			if websocket.CloseStatus(err) == -1 || websocket.CloseStatus(err) != websocket.StatusNormalClosure {
				res.Error = "signalling closed: " + err.Error()
			}
			done = true
		case <-watch:
			done = true
		}
	}
	// Before saying goodbye, which closes every channel.
	if dc := inputDC.Load(); dc != nil {
		res.InputChannel = "refused"
		if dc.ReadyState() == webrtc.DataChannelStateOpen {
			res.InputChannel = "open"
		}
	}
	send(msg{T: "bye"})

	res.Frames = frames.Load()
	res.Bytes = bytes.Load()
	res.Events = events.Load()
	res.FirstFrameMs = firstFrame.Load()
	res.SelectedPair = selectedPair(pc)
	return
}

// selectedPair names the local side of the nominated pair: its type and the
// protocol that reaches the TURN server for a relay.
func selectedPair(pc *webrtc.PeerConnection) string {
	report := pc.GetStats()
	for _, s := range report {
		pair, ok := s.(webrtc.ICECandidatePairStats)
		if !ok || !pair.Nominated {
			continue
		}
		local, ok := report[pair.LocalCandidateID].(webrtc.ICECandidateStats)
		if !ok {
			continue
		}
		proto := local.Protocol
		if local.CandidateType == webrtc.ICECandidateTypeRelay && local.RelayProtocol != "" {
			proto = local.RelayProtocol
		}
		return local.CandidateType.String() + "/" + proto
	}
	return ""
}
