//go:build linux

package session

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/coder/websocket"
	"github.com/pion/interceptor/pkg/cc"
	"github.com/pion/rtcp"
	"github.com/pion/webrtc/v4"

	"lemma-streamer/internal/capture"
	"lemma-streamer/internal/input"
	"lemma-streamer/internal/ticket"
)

// Signalling limits.
const (
	MaxSignalFrame = 64 << 10
	helloTimeout   = 5 * time.Second
	maxICEServers  = 8
)

// Data channel labels. Nothing else is accepted.
const (
	labelEvents = "events"
	labelInput  = "input"
)

// signal is every field a signalling message may carry, in either direction.
type signal struct {
	T             string      `json:"t"`
	Ticket        string      `json:"ticket,omitempty"`
	ICEServers    []iceServer `json:"ice_servers,omitempty"`
	Policy        string      `json:"policy,omitempty"`
	SDP           string      `json:"sdp,omitempty"`
	Candidate     *string     `json:"candidate,omitempty"`
	SDPMid        *string     `json:"sdpMid,omitempty"`
	SDPMLineIndex *uint16     `json:"sdpMLineIndex,omitempty"`
	Mode          string      `json:"mode,omitempty"`
	Code          string      `json:"code,omitempty"`
	Message       string      `json:"message,omitempty"`
	Codec         string      `json:"codec,omitempty"`
}

type iceServer struct {
	URLs       []string `json:"urls"`
	Username   string   `json:"username,omitempty"`
	Credential string   `json:"credential,omitempty"`
}

// event is a streamer -> viewer message on the events channel.
type event struct {
	T       string `json:"t"`
	X       int    `json:"x,omitempty"`
	Y       int    `json:"y,omitempty"`
	W       int    `json:"w,omitempty"`
	H       int    `json:"h,omitempty"`
	ID      uint32 `json:"id,omitempty"`
	PNG     string `json:"png,omitempty"`
	Codec   string `json:"codec,omitempty"`
	S       string `json:"s,omitempty"`
	You     *bool  `json:"you,omitempty"`
	Driving *bool  `json:"driving,omitempty"`
}

// Peer is one viewer: its signalling socket, its peer connection, and what
// its ticket allows.
type Peer struct {
	hub    *Hub
	ws     *websocket.Conn
	claims ticket.Claims

	mu        sync.Mutex
	pc        *webrtc.PeerConnection
	estimator cc.BandwidthEstimator
	pipe      *pipeline
	events    *webrtc.DataChannel
	input     *webrtc.DataChannel
	cursors   map[uint32]bool
	control   bool

	lastPaste atomic.Value // string
	dropped   atomic.Int64
	limiter   *input.Limiter
}

func (p *Peer) mode() string {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.control {
		return ticket.ModeControl
	}
	return ticket.ModeView
}

// Serve runs one signalling socket until it closes.
func (h *Hub) Serve(ctx context.Context, ws *websocket.Conn) {
	ws.SetReadLimit(MaxSignalFrame)
	p := &Peer{hub: h, ws: ws, cursors: map[uint32]bool{}, limiter: input.NewLimiter(nil)}
	p.lastPaste.Store("")
	defer p.close()

	helloCtx, cancel := context.WithTimeout(ctx, helloTimeout)
	first, err := p.read(helloCtx)
	cancel()
	if err != nil {
		log.Printf("viewer sent no hello: %v", err)
		p.fail("hello", "no hello in time")
		return
	}
	if first.T != "hello" {
		p.fail("hello", "the first message must be hello")
		return
	}
	claims, err := h.cfg.Verifier.Verify(first.Ticket)
	if err != nil {
		log.Printf("refusing viewer: %v", err)
		p.fail("ticket", err.Error())
		return
	}
	p.claims = claims
	p.control = claims.Mode == ticket.ModeControl
	cfg, err := iceConfig(first)
	if err != nil {
		p.fail("ice", err.Error())
		return
	}
	pc, est, err := h.media.newPeerConnection(cfg)
	if err != nil {
		p.fail("peer", "could not create a peer connection")
		return
	}
	p.mu.Lock()
	p.pc, p.estimator = pc, est
	p.mu.Unlock()
	p.wire(pc)
	if p.control {
		h.takeControl(p)
	}
	p.send(signal{T: "ready", Mode: p.mode()})

	for {
		msg, err := p.read(ctx)
		if err != nil {
			return
		}
		switch msg.T {
		case "offer":
			if err := p.answer(msg.SDP); err != nil {
				p.fail("offer", err.Error())
				return
			}
		case "candidate":
			if msg.Candidate == nil || *msg.Candidate == "" {
				continue // end of the viewer's candidates
			}
			_ = pc.AddICECandidate(webrtc.ICECandidateInit{Candidate: *msg.Candidate, SDPMid: msg.SDPMid, SDPMLineIndex: msg.SDPMLineIndex})
		case "control":
			c, err := h.cfg.Verifier.Verify(msg.Ticket)
			if err != nil || c.Mode != ticket.ModeControl {
				log.Printf("refusing control for %s: %v", p.claims.Sub, err)
				p.fail("ticket", "control ticket refused")
				return
			}
			p.mu.Lock()
			p.control = true
			p.claims.Sub = c.Sub
			p.mu.Unlock()
			h.takeControl(p)
			p.send(signal{T: "mode", Mode: ticket.ModeControl})
		case "release":
			p.mu.Lock()
			p.control = false
			p.mu.Unlock()
			p.closeInput()
			h.releaseControl(p)
			p.send(signal{T: "mode", Mode: ticket.ModeView})
		case "bye":
			return
		default:
			p.fail("protocol", "unknown message")
			return
		}
	}
}

func iceConfig(hello signal) (webrtc.Configuration, error) {
	cfg := webrtc.Configuration{}
	if len(hello.ICEServers) > maxICEServers {
		return cfg, errors.New("too many ICE servers")
	}
	for _, s := range hello.ICEServers {
		if len(s.URLs) == 0 || len(s.URLs) > 4 || len(s.Username) > 512 || len(s.Credential) > 512 {
			return cfg, errors.New("malformed ICE server")
		}
		for _, u := range s.URLs {
			if len(u) > 512 || !(strings.HasPrefix(u, "turn:") || strings.HasPrefix(u, "turns:") || strings.HasPrefix(u, "stun:")) {
				return cfg, errors.New("ICE server URL must be stun:, turn: or turns:")
			}
		}
		cfg.ICEServers = append(cfg.ICEServers, webrtc.ICEServer{URLs: s.URLs, Username: s.Username, Credential: s.Credential})
	}
	switch hello.Policy {
	case "", "relay":
		// Relay unless told otherwise: the sandbox's own addresses are not
		// for viewers, and TURN is the path that works through every fabric.
		cfg.ICETransportPolicy = webrtc.ICETransportPolicyRelay
	case "all":
		cfg.ICETransportPolicy = webrtc.ICETransportPolicyAll
	default:
		return cfg, errors.New("policy must be relay or all")
	}
	return cfg, nil
}

func (p *Peer) wire(pc *webrtc.PeerConnection) {
	pc.OnICECandidate(func(c *webrtc.ICECandidate) {
		if c == nil {
			p.send(signal{T: "candidate"})
			return
		}
		init := c.ToJSON()
		p.send(signal{T: "candidate", Candidate: &init.Candidate, SDPMid: init.SDPMid, SDPMLineIndex: init.SDPMLineIndex})
	})
	pc.OnConnectionStateChange(func(s webrtc.PeerConnectionState) {
		switch s {
		case webrtc.PeerConnectionStateConnected:
			p.mu.Lock()
			pipe := p.pipe
			p.mu.Unlock()
			if pipe != nil {
				pipe.requestKeyframe(true)
			}
		case webrtc.PeerConnectionStateFailed:
			log.Printf("viewer %s: connection failed", p.claims.Sub)
			_ = p.ws.Close(websocket.StatusGoingAway, "connection failed")
		}
	})
	pc.OnDataChannel(func(dc *webrtc.DataChannel) {
		switch dc.Label() {
		case labelEvents:
			p.mu.Lock()
			p.events = dc
			p.mu.Unlock()
			dc.OnOpen(func() { p.greet() })
			dc.OnMessage(func(webrtc.DataChannelMessage) { p.dropped.Add(1) })
		case labelInput:
			if !p.hub.isController(p) {
				log.Printf("viewer %s (%s): refusing data channel %q", p.claims.Sub, p.mode(), dc.Label())
				_ = dc.Close()
				return
			}
			p.mu.Lock()
			if p.input != nil {
				_ = p.input.Close()
			}
			p.input = dc
			p.mu.Unlock()
			dc.OnMessage(func(m webrtc.DataChannelMessage) { p.onInput(dc, m) })
		default:
			log.Printf("viewer %s: refusing data channel %q", p.claims.Sub, dc.Label())
			_ = dc.Close()
		}
	})
}

// onInput is the only path from a viewer to the display.
func (p *Peer) onInput(dc *webrtc.DataChannel, m webrtc.DataChannelMessage) {
	p.mu.Lock()
	current := p.input == dc
	p.mu.Unlock()
	if !current || !p.hub.isController(p) || !m.IsString {
		p.reject()
		return
	}
	cmd, err := input.Parse(m.Data, p.hub.screen())
	if err != nil || !p.limiter.Allow(cmd) {
		p.reject()
		return
	}
	if cmd.Kind == input.Paste {
		p.lastPaste.Store(cmd.S)
	}
	if err := p.hub.inj.Apply(cmd); err != nil {
		log.Printf("viewer %s: %s failed: %v", p.claims.Sub, cmd.Kind, err)
	}
}

func (p *Peer) reject() {
	if n := p.dropped.Add(1); n == 1 || n%100 == 0 {
		log.Printf("viewer %s: dropped %d input messages outside the set", p.claims.Sub, n)
	}
}

func (p *Peer) answer(sdp string) error {
	if sdp == "" {
		return errors.New("empty offer")
	}
	p.mu.Lock()
	pc, first := p.pc, p.pipe == nil
	p.mu.Unlock()
	if first {
		codec := codecFor(sdp)
		if codec == "" {
			return errors.New("the offer has neither H.264 (packetization-mode=1) nor VP8")
		}
		pipe, err := p.hub.join(p, codec)
		if err != nil {
			return err
		}
		sender, err := pc.AddTrack(pipe.track)
		if err != nil {
			return err
		}
		p.mu.Lock()
		p.pipe = pipe
		p.mu.Unlock()
		go p.readRTCP(sender, pipe)
	}
	if err := pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeOffer, SDP: sdp}); err != nil {
		return errors.New("offer refused")
	}
	ans, err := pc.CreateAnswer(nil)
	if err != nil {
		return errors.New("could not answer")
	}
	if err := pc.SetLocalDescription(ans); err != nil {
		return errors.New("could not answer")
	}
	p.send(signal{T: "answer", SDP: ans.SDP, Codec: p.pipe.codec})
	return nil
}

// readRTCP turns a viewer's loss reports into keyframes.
func (p *Peer) readRTCP(sender *webrtc.RTPSender, pipe *pipeline) {
	for {
		pkts, _, err := sender.ReadRTCP()
		if err != nil {
			return
		}
		for _, pkt := range pkts {
			switch pkt.(type) {
			case *rtcp.PictureLossIndication, *rtcp.FullIntraRequest:
				pipe.requestKeyframe(false)
			}
		}
	}
}

// greet tells a viewer what it is looking at, once its events channel opens.
func (p *Peer) greet() {
	w, h := p.hub.cap.Size()
	p.mu.Lock()
	codec := ""
	if p.pipe != nil {
		codec = p.pipe.codec
	}
	p.mu.Unlock()
	p.sendEvent(event{T: "hello", W: w, H: h, Codec: codec})
	you := p.hub.isController(p)
	p.sendEvent(event{T: "control", You: &you})
	driving := p.hub.agentDriving()
	p.sendEvent(event{T: "agent", Driving: &driving})
	if c, ok := p.hub.cap.CurrentCursor(); ok {
		p.sendCursor(c)
	}
}

// sendCursor sends a cursor's image the first time this viewer sees it, and
// only its id after that.
func (p *Peer) sendCursor(c capture.Cursor) {
	p.mu.Lock()
	open := p.events != nil && p.events.ReadyState() == webrtc.DataChannelStateOpen
	seen := p.cursors[c.ID]
	if open {
		p.cursors[c.ID] = true
	}
	p.mu.Unlock()
	if open {
		p.sendEvent(cursorEvent(c, !seen))
	}
}

func (p *Peer) sendEvent(e event) {
	p.mu.Lock()
	dc := p.events
	p.mu.Unlock()
	if dc == nil || dc.ReadyState() != webrtc.DataChannelStateOpen {
		return
	}
	b, err := json.Marshal(e)
	if err != nil {
		return
	}
	_ = dc.SendText(string(b))
}

// revoke is called when a newer control ticket takes over.
func (p *Peer) revoke() {
	p.mu.Lock()
	p.control = false
	p.mu.Unlock()
	p.sendEvent(event{T: "revoked"})
	p.closeInput()
	p.send(signal{T: "mode", Mode: ticket.ModeView})
}

func (p *Peer) closeInput() {
	p.mu.Lock()
	dc := p.input
	p.input = nil
	p.mu.Unlock()
	if dc != nil {
		_ = dc.Close()
	}
}

func (p *Peer) read(ctx context.Context) (signal, error) {
	var s signal
	typ, raw, err := p.ws.Read(ctx)
	if err != nil {
		return s, err
	}
	if typ != websocket.MessageText {
		return s, errors.New("binary signalling frame")
	}
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&s); err != nil {
		return s, errors.New("malformed signalling message")
	}
	return s, nil
}

func (p *Peer) send(s signal) {
	b, err := json.Marshal(s)
	if err != nil {
		return
	}
	if s.T == "candidate" && s.Candidate == nil {
		b = []byte(`{"t":"candidate","candidate":null}`)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = p.ws.Write(ctx, websocket.MessageText, b)
}

func (p *Peer) fail(code, message string) {
	p.send(signal{T: "error", Code: code, Message: message})
	_ = p.ws.Close(websocket.StatusPolicyViolation, code)
}

func (p *Peer) close() {
	p.mu.Lock()
	pc := p.pc
	p.mu.Unlock()
	if pc != nil {
		_ = pc.Close()
	}
	p.hub.leave(p)
	_ = p.ws.CloseNow()
}
