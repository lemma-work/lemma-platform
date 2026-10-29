//go:build linux

// lemma-streamer shows the machine's display to viewers over WebRTC.
//
// It listens on loopback only. The relay is the one thing that dials it, and
// it carries signalling from the API; the picture itself goes to the viewer
// through TURN. Every viewer arrives with a ticket the API signed, and only a
// control ticket ever gets an input path. See README.md.
package main

import (
	"context"
	"encoding/json"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/coder/websocket"

	"lemma-streamer/internal/input"
	"lemma-streamer/internal/session"
	"lemma-streamer/internal/ticket"
)

// How long the streamer stays up with nobody watching. The relay starts it
// again on the next viewer; holding X connections and a goroutine pool for
// an empty room is not worth the start-up it saves.
const idleExit = 120 * time.Second

func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}

func main() {
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
	log.SetPrefix("lemma-streamer: ")
	addr := net.JoinHostPort("127.0.0.1", env("LEMMA_BROWSER_STREAM_PORT", "4860"))
	presets := input.ParsePresets(env("LEMMA_STREAM_PRESETS", input.DefaultPresets))
	verifier := ticket.NewVerifier(&ticket.FileSource{Path: env("LEMMA_STREAM_KEY_FILE", "/tmp/lemma-relay/stream.json")})

	hub, err := session.NewHub(session.Config{Display: env("DISPLAY", ":99"), Presets: presets, Verifier: verifier})
	if err != nil {
		log.Fatalf("cannot open the display: %v", err)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(hub.Health())
	})
	mux.HandleFunc("GET /signal", func(w http.ResponseWriter, r *http.Request) {
		ws, err := websocket.Accept(w, r, &websocket.AcceptOptions{
			// Loopback only, and dialled by the relay, which sends no Origin.
			InsecureSkipVerify: true,
			CompressionMode:    websocket.CompressionDisabled,
		})
		if err != nil {
			return
		}
		hub.Serve(r.Context(), ws)
	})

	ln, err := net.Listen("tcp", addr)
	if err != nil {
		log.Fatalf("listen %s: %v", addr, err)
	}
	srv := &http.Server{Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	go func() {
		t := time.NewTicker(5 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				if since := hub.IdleSince(); !since.IsZero() && time.Since(since) > idleExit {
					log.Printf("no viewers for %s; exiting", idleExit)
					stop()
					return
				}
			}
		}
	}()
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutdown)
	}()
	h := hub.Health()
	log.Printf("listening on %s, display %dx%d, capture %s", addr, h.W, h.H, h.Capture)
	if err := srv.Serve(ln); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}
