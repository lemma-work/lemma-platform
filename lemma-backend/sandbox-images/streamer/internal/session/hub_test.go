//go:build linux

package session

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/coder/websocket"

	"lemma-streamer/internal/input"
	"lemma-streamer/internal/ticket"
)

// The hub's bookkeeping, without an X server: no capture loop is started
// (the hub is marked running), and the injector only counts releases.

type fakeInjector struct {
	mu       sync.Mutex
	releases int
}

func (f *fakeInjector) Apply(input.Command) error { return nil }
func (f *fakeInjector) ReleaseAll() {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.releases++
}
func (f *fakeInjector) count() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.releases
}

func testHub(t *testing.T) (*Hub, *fakeInjector) {
	t.Helper()
	inj := &fakeInjector{}
	h := &Hub{inj: inj, peers: map[*Peer]bool{}, pipelines: map[string]*pipeline{}, running: true, stop: make(chan struct{})}
	return h, inj
}

// testPeer is a viewer with a real signalling socket; the returned client end
// reads what the streamer sends it.
func testPeer(t *testing.T, h *Hub, sub string) (*Peer, *websocket.Conn) {
	t.Helper()
	server := make(chan *websocket.Conn, 1)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ws, err := websocket.Accept(w, r, nil)
		if err != nil {
			t.Error(err)
			return
		}
		server <- ws
		<-r.Context().Done()
	}))
	t.Cleanup(srv.Close)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	client, _, err := websocket.Dial(ctx, "ws"+srv.URL[len("http"):], nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = client.CloseNow() })
	p := &Peer{hub: h, ws: <-server, claims: ticket.Claims{Sub: sub}, cursors: map[uint32]bool{}, limiter: input.NewLimiter(nil)}
	t.Cleanup(func() { _ = p.ws.CloseNow() })
	return p, client
}

func readSignal(t *testing.T, c *websocket.Conn) signal {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_, raw, err := c.Read(ctx)
	if err != nil {
		t.Fatal(err)
	}
	var s signal
	if err := json.Unmarshal(raw, &s); err != nil {
		t.Fatal(err)
	}
	return s
}

func TestViewersShareAPipelineAndTheLastOneOutStopsIt(t *testing.T) {
	h, _ := testHub(t)
	a, _ := testPeer(t, h, "a")
	b, _ := testPeer(t, h, "b")

	pa, err := h.join(a, "vp8")
	if err != nil {
		t.Fatal(err)
	}
	pb, _ := h.join(b, "vp8")
	if pa != pb || len(h.pipelines) != 1 || len(pa.peers) != 2 {
		t.Fatalf("two viewers of one codec should share one encoder: %d pipelines", len(h.pipelines))
	}
	if !h.IdleSince().IsZero() {
		t.Fatal("idle while two are watching")
	}

	h.leave(a)
	if len(h.pipelines) != 1 || len(h.peers) != 1 {
		t.Fatal("the remaining viewer lost its pipeline")
	}
	h.leave(b)
	select {
	case <-pa.done:
	default:
		t.Fatal("the encoder was left running for nobody")
	}
	if len(h.pipelines) != 0 || h.running || h.IdleSince().IsZero() {
		t.Fatal("the hub did not go idle when the last viewer left")
	}
	// Leaving twice is harmless: a socket error and a bye can both arrive.
	h.leave(b)
}

func TestANewControllerDisplacesTheOldOne(t *testing.T) {
	h, inj := testHub(t)
	a, aClient := testPeer(t, h, "a")
	b, _ := testPeer(t, h, "b")
	h.join(a, "vp8")
	h.join(b, "vp8")

	a.control = true
	h.takeControl(a)
	if !h.isController(a) || inj.count() != 0 {
		t.Fatal("the first controller should take control without a release")
	}

	b.control = true
	h.takeControl(b)
	if !h.isController(b) || h.isController(a) {
		t.Fatal("control did not move to the newer ticket")
	}
	if a.mode() != ticket.ModeView {
		t.Fatal("the displaced controller still thinks it drives")
	}
	if got := readSignal(t, aClient); got.T != "mode" || got.Mode != ticket.ModeView {
		t.Fatalf("the displaced controller was told %+v", got)
	}
	if inj.count() != 1 {
		t.Fatalf("keys held by the old controller were not released (%d)", inj.count())
	}

	// A viewer that is not in control giving it back changes nothing.
	h.releaseControl(a)
	if !h.isController(b) || inj.count() != 1 {
		t.Fatal("a non-controller released somebody else's control")
	}

	// The controller leaving releases what it held and leaves nobody driving.
	h.leave(b)
	if h.isController(b) || inj.count() != 2 {
		t.Fatal("a controller that left still holds control or its keys")
	}
	h.leave(a)
}

func TestAControlTicketMustNameTheSameViewer(t *testing.T) {
	me := ticket.Claims{Sub: "user:pane", Mode: ticket.ModeView}
	if err := controlFor(me, ticket.Claims{Sub: "user:pane", Mode: ticket.ModeControl}); err != nil {
		t.Fatalf("own control ticket refused: %v", err)
	}
	if controlFor(me, ticket.Claims{Sub: "someone:else", Mode: ticket.ModeControl}) == nil {
		t.Fatal("a control ticket for another viewer was accepted")
	}
	if controlFor(me, ticket.Claims{Sub: "user:pane", Mode: ticket.ModeView}) == nil {
		t.Fatal("a view ticket granted control")
	}
}

func TestLossReportsAskForAKeyframeAtMostSoOften(t *testing.T) {
	p := &pipeline{}
	p.requestKeyframe(false)
	if !p.key.Swap(false) {
		t.Fatal("the first loss report asked for nothing")
	}
	p.requestKeyframe(false)
	if p.key.Load() {
		t.Fatal("a burst of PLIs asked for a second keyframe inside the gap")
	}
	p.requestKeyframe(true)
	if !p.key.Swap(false) {
		t.Fatal("a joining viewer's forced keyframe was rate-limited")
	}
	p.lastKey.Store(time.Now().Add(-2 * keyframeGap).UnixNano())
	p.requestKeyframe(false)
	if !p.key.Load() {
		t.Fatal("a loss report after the gap asked for nothing")
	}
}
