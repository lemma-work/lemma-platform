//go:build linux

// Package session is the streamer's heart: one capture loop, one encoder per
// codec shared by every viewer of it, and the viewers themselves.
package session

import (
	"encoding/base64"
	"log"
	"os"
	"sync"
	"sync/atomic"
	"time"

	"github.com/pion/webrtc/v4"
	"github.com/pion/webrtc/v4/pkg/media"

	"lemma-streamer/internal/capture"
	"lemma-streamer/internal/convert"
	"lemma-streamer/internal/encode"
	"lemma-streamer/internal/inject"
	"lemma-streamer/internal/input"
	"lemma-streamer/internal/rate"
	"lemma-streamer/internal/ticket"
)

// Without damage, a still screen is resent this often, so a viewer that
// joins (or loses a packet) sees a picture without waiting for a change.
const idleInterval = time.Second

// A viewer's loss report forces at most one keyframe this often: a keyframe
// is many times a delta, and a lossy link sends PLIs in bursts.
const keyframeGap = 300 * time.Millisecond

// AgentActiveFile is touched by the agent-browser wrapper on every command.
const AgentActiveFile = "/tmp/lemma-browser/agent-active"

// agentWindow is how long after a command the agent counts as driving.
const agentWindow = 4 * time.Second

// Config is what main supplies.
type Config struct {
	Display  string
	Presets  []input.Preset
	Verifier *ticket.Verifier
}

type Hub struct {
	cfg   Config
	media *mediaAPI
	cap   *capture.Capturer
	inj   *inject.Injector

	mu         sync.Mutex
	peers      map[*Peer]bool
	controller *Peer
	pipelines  map[string]*pipeline
	running    bool
	stop       chan struct{}
	lastLeave  time.Time
	fps        atomic.Int32
	agent      bool

	pool framePool
}

func NewHub(cfg Config) (*Hub, error) {
	md, err := newMedia()
	if err != nil {
		return nil, err
	}
	h := &Hub{cfg: cfg, media: md, peers: map[*Peer]bool{}, pipelines: map[string]*pipeline{}, lastLeave: time.Now()}
	h.fps.Store(rate.FullFPS)
	h.cap, err = capture.Open(cfg.Display, capture.Handlers{
		Cursor:    h.onCursor,
		Clipboard: h.onClipboard,
		Resized:   h.onResize,
	})
	if err != nil {
		return nil, err
	}
	h.inj, err = inject.New(cfg.Display)
	if err != nil {
		h.cap.Close()
		return nil, err
	}
	go h.watchAgent()
	go h.watchCPU()
	return h, nil
}

// Health is what GET /health reports.
type Health struct {
	Viewers    int      `json:"viewers"`
	Controller string   `json:"controller"`
	Codecs     []string `json:"codecs"`
	FPS        int      `json:"fps"`
	Bitrate    int      `json:"bitrate"`
	Capture    string   `json:"capture"`
	W          int      `json:"w"`
	H          int      `json:"h"`
}

func (h *Hub) Health() Health {
	h.mu.Lock()
	defer h.mu.Unlock()
	out := Health{Viewers: len(h.peers), Codecs: []string{}, FPS: int(h.fps.Load()), Capture: h.cap.Mode()}
	out.W, out.H = h.cap.Size()
	if h.controller != nil {
		out.Controller = h.controller.claims.Sub
	}
	for codec, p := range h.pipelines {
		out.Codecs = append(out.Codecs, codec)
		if b := int(p.bitrate.Load()); out.Bitrate == 0 || b < out.Bitrate {
			out.Bitrate = b
		}
	}
	return out
}

// IdleSince is when the last viewer left, or zero while somebody watches.
func (h *Hub) IdleSince() time.Time {
	h.mu.Lock()
	defer h.mu.Unlock()
	if len(h.peers) > 0 {
		return time.Time{}
	}
	return h.lastLeave
}

func (h *Hub) screen() input.Screen {
	w, hh := h.cap.Size()
	return input.Screen{W: w, H: hh, Presets: h.cfg.Presets}
}

// ---- pipelines ------------------------------------------------------------

// pipeline is one codec: a track every viewer of it is bound to, and the
// encoder feeding it. Frames reach it through a one-frame slot -- a frame
// that arrives while the encoder is busy replaces the waiting one, so a slow
// encode drops frames rather than building a queue behind the screen.
type pipeline struct {
	codec   string
	track   *webrtc.TrackLocalStaticSample
	peers   map[*Peer]bool
	slot    chan *sharedFrame
	key     atomic.Bool
	lastKey atomic.Int64
	bitrate atomic.Int64
	done    chan struct{}
}

func (h *Hub) pipelineFor(codec string) (*pipeline, error) {
	if p, ok := h.pipelines[codec]; ok {
		return p, nil
	}
	track, err := webrtc.NewTrackLocalStaticSample(capabilities[codec], "video", "lemma-screen")
	if err != nil {
		return nil, err
	}
	p := &pipeline{codec: codec, track: track, peers: map[*Peer]bool{}, slot: make(chan *sharedFrame, 1), done: make(chan struct{})}
	p.bitrate.Store(rate.StartBitrate)
	h.pipelines[codec] = p
	go h.encodeLoop(p)
	return p, nil
}

// requestKeyframe is rate-limited unless forced (a new viewer must get one).
func (p *pipeline) requestKeyframe(force bool) {
	now := time.Now().UnixNano()
	if !force && now-p.lastKey.Load() < int64(keyframeGap) {
		return
	}
	p.lastKey.Store(now)
	p.key.Store(true)
}

func (p *pipeline) offer(f *sharedFrame) {
	f.retain()
	select {
	case p.slot <- f:
		return
	default:
	}
	// Full: the waiting frame is stale now. Take it out and put this one in.
	select {
	case old := <-p.slot:
		old.release()
	default:
	}
	select {
	case p.slot <- f:
	default:
		f.release()
	}
}

func (h *Hub) encodeLoop(p *pipeline) {
	var enc encode.Encoder
	var encW, encH int
	var gov rate.Governor
	last := time.Now()
	defer func() {
		if enc != nil {
			enc.Close()
		}
	}()
	for {
		var f *sharedFrame
		select {
		case <-p.done:
			for {
				select {
				case f := <-p.slot:
					f.release()
				default:
					return
				}
			}
		case f = <-p.slot:
		}
		target := rate.Target(h.estimates(p), f.frame.W, f.frame.H)
		force := p.key.Swap(false)
		if enc == nil || encW != f.frame.W&^1 || encH != f.frame.H&^1 {
			if enc != nil {
				enc.Close()
			}
			var err error
			enc, err = encode.New(p.codec, f.frame.W, f.frame.H, rate.FullFPS, target)
			if err != nil {
				log.Printf("encoder %s: %v", p.codec, err)
				f.release()
				enc = nil
				time.Sleep(time.Second)
				continue
			}
			encW, encH = f.frame.W&^1, f.frame.H&^1
			gov = rate.Governor{}
			gov.Next(target, time.Now())
			p.bitrate.Store(int64(target))
			force = true
		} else if bps, apply := gov.Next(target, time.Now()); apply {
			enc.SetBitrate(bps)
			p.bitrate.Store(int64(bps))
		}
		data, err := enc.Encode(f.frame, force)
		f.release()
		if err != nil {
			log.Printf("encode %s: %v", p.codec, err)
			continue
		}
		if len(data) == 0 {
			continue
		}
		now := time.Now()
		if err := p.track.WriteSample(media.Sample{Data: data, Duration: now.Sub(last)}); err != nil {
			log.Printf("write sample: %v", err)
		}
		last = now
	}
}

func (h *Hub) estimates(p *pipeline) []int {
	h.mu.Lock()
	defer h.mu.Unlock()
	var out []int
	for peer := range p.peers {
		if peer.estimator != nil {
			out = append(out, peer.estimator.GetTargetBitrate())
		}
	}
	return out
}

// ---- capture ----------------------------------------------------------------

func (h *Hub) captureLoop(stop chan struct{}) {
	var last time.Time
	fps := int(h.fps.Load())
	tick := time.NewTicker(time.Second / time.Duration(fps))
	defer tick.Stop()
	lastPos := [2]int{-1, -1}
	for {
		select {
		case <-stop:
			return
		case <-tick.C:
		}
		if now := int(h.fps.Load()); now != fps {
			fps = now
			tick.Reset(time.Second / time.Duration(fps))
		}
		if x, y, ok := h.cap.Pointer(); ok && (x != lastPos[0] || y != lastPos[1]) {
			lastPos = [2]int{x, y}
			h.broadcast(event{T: "pos", X: x, Y: y}, nil)
		}
		h.mu.Lock()
		pipes := make([]*pipeline, 0, len(h.pipelines))
		wantKey := false
		for _, p := range h.pipelines {
			pipes = append(pipes, p)
			wantKey = wantKey || p.key.Load()
		}
		h.mu.Unlock()
		if len(pipes) == 0 {
			continue
		}
		if !h.cap.Dirty() && !wantKey && time.Since(last) < idleInterval {
			continue
		}
		raw, err := h.cap.Grab()
		if err != nil {
			continue
		}
		f := h.pool.get(raw.W, raw.H)
		convert.BGRAToI420(raw.Pix, raw.Stride, f.frame)
		last = time.Now()
		for _, p := range pipes {
			p.offer(f)
		}
		f.release()
	}
}

// ---- viewers ------------------------------------------------------------------

func (h *Hub) join(p *Peer, codec string) (*pipeline, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	pl, err := h.pipelineFor(codec)
	if err != nil {
		return nil, err
	}
	pl.peers[p] = true
	h.peers[p] = true
	if !h.running {
		h.running = true
		h.stop = make(chan struct{})
		go h.captureLoop(h.stop)
	}
	log.Printf("viewer %s joined (%s, %s); %d watching", p.claims.Sub, p.mode(), codec, len(h.peers))
	return pl, nil
}

func (h *Hub) leave(p *Peer) {
	h.mu.Lock()
	wasController := h.controller == p
	if wasController {
		h.controller = nil
	}
	if !h.peers[p] {
		h.mu.Unlock()
		if wasController {
			h.inj.ReleaseAll()
		}
		return
	}
	delete(h.peers, p)
	for codec, pl := range h.pipelines {
		delete(pl.peers, p)
		if len(pl.peers) == 0 {
			close(pl.done)
			delete(h.pipelines, codec)
		}
	}
	if len(h.peers) == 0 && h.running {
		close(h.stop)
		h.running = false
		h.lastLeave = time.Now()
	}
	n := len(h.peers)
	h.mu.Unlock()
	if wasController {
		h.inj.ReleaseAll()
	}
	log.Printf("viewer %s left; %d watching", p.claims.Sub, n)
}

// takeControl makes p the controller. The previous one is told and loses its
// input channel; anything it held down is released.
func (h *Hub) takeControl(p *Peer) {
	h.mu.Lock()
	prev := h.controller
	h.controller = p
	h.mu.Unlock()
	if prev != nil && prev != p {
		prev.revoke()
		h.inj.ReleaseAll()
	}
	h.broadcastControl()
}

func (h *Hub) releaseControl(p *Peer) {
	h.mu.Lock()
	was := h.controller == p
	if was {
		h.controller = nil
	}
	h.mu.Unlock()
	if was {
		h.inj.ReleaseAll()
		h.broadcastControl()
	}
}

func (h *Hub) isController(p *Peer) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.controller == p
}

func (h *Hub) broadcastControl() {
	h.mu.Lock()
	peers := make([]*Peer, 0, len(h.peers))
	for p := range h.peers {
		peers = append(peers, p)
	}
	ctrl := h.controller
	h.mu.Unlock()
	for _, p := range peers {
		you := p == ctrl
		p.sendEvent(event{T: "control", You: &you})
	}
}

func (h *Hub) broadcast(e event, only func(*Peer) bool) {
	h.mu.Lock()
	peers := make([]*Peer, 0, len(h.peers))
	for p := range h.peers {
		if only == nil || only(p) {
			peers = append(peers, p)
		}
	}
	h.mu.Unlock()
	for _, p := range peers {
		p.sendEvent(e)
	}
}

func (h *Hub) onResize(w, hh int) {
	h.broadcast(event{T: "size", W: w, H: hh}, nil)
}

func (h *Hub) onCursor(c capture.Cursor) {
	h.mu.Lock()
	peers := make([]*Peer, 0, len(h.peers))
	for p := range h.peers {
		peers = append(peers, p)
	}
	h.mu.Unlock()
	for _, p := range peers {
		p.sendCursor(c)
	}
}

func (h *Hub) onClipboard() {
	go func() {
		s := capture.ReadClipboard()
		if s == "" {
			return
		}
		h.mu.Lock()
		ctrl := h.controller
		h.mu.Unlock()
		if ctrl != nil && ctrl.lastPaste.Load() != s {
			ctrl.sendEvent(event{T: "clip", S: s})
		}
	}()
}

func (h *Hub) watchAgent() {
	for range time.Tick(time.Second) {
		info, err := os.Stat(AgentActiveFile)
		driving := err == nil && time.Since(info.ModTime()) < agentWindow
		h.mu.Lock()
		changed := driving != h.agent
		h.agent = driving
		h.mu.Unlock()
		if changed {
			h.broadcast(event{T: "agent", Driving: &driving}, nil)
		}
	}
}

func (h *Hub) agentDriving() bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.agent
}

func (h *Hub) watchCPU() {
	cpu := rate.NewCPU()
	for range time.Tick(time.Second) {
		free := cpu.Sample(time.Now())
		if free < 0 {
			continue
		}
		cur := int(h.fps.Load())
		if next := rate.FPSFor(free, cur); next != cur {
			log.Printf("frame rate %d -> %d (%.2f cores free)", cur, next, free)
			h.fps.Store(int32(next))
		}
	}
}

func cursorEvent(c capture.Cursor, withImage bool) event {
	e := event{T: "cursor", ID: c.ID, X: c.XHot, Y: c.YHot, W: c.W, H: c.H}
	if withImage {
		e.PNG = base64.StdEncoding.EncodeToString(c.PNG)
	}
	return e
}

// ---- frames -------------------------------------------------------------------

// sharedFrame is a converted frame several encoders may read; it returns to
// the pool when the last of them is done.
type sharedFrame struct {
	frame *convert.I420
	refs  atomic.Int32
	pool  *framePool
}

func (f *sharedFrame) retain() { f.refs.Add(1) }
func (f *sharedFrame) release() {
	if f.refs.Add(-1) == 0 {
		f.pool.put(f)
	}
}

type framePool struct {
	mu   sync.Mutex
	free []*sharedFrame
}

func (fp *framePool) get(w, h int) *sharedFrame {
	fp.mu.Lock()
	defer fp.mu.Unlock()
	for len(fp.free) > 0 {
		f := fp.free[len(fp.free)-1]
		fp.free = fp.free[:len(fp.free)-1]
		if f.frame.W == w && f.frame.H == h {
			f.refs.Store(1)
			return f
		}
	}
	f := &sharedFrame{frame: convert.NewI420(w, h), pool: fp}
	f.refs.Store(1)
	return f
}

func (fp *framePool) put(f *sharedFrame) {
	fp.mu.Lock()
	defer fp.mu.Unlock()
	if len(fp.free) < 4 {
		fp.free = append(fp.free, f)
	}
}
