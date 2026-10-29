// Package ticket verifies the per-viewer tickets the API mints.
//
// A ticket is the only thing that decides whether a viewer may type. The API
// signs it with a key derived for this one machine and hands it to the
// streamer through the relay; the viewer's browser never sees it. So a
// viewer cannot promote itself from watching to driving, and a ticket for one
// machine is useless on another, because the key differs and the machine id
// is checked as well.
package ticket

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"sync"
	"time"
)

const (
	ModeView    = "view"
	ModeControl = "control"
)

// Skew tolerated on expiry: the API and the sandbox do not share a clock, and
// a ticket only has to survive signalling.
const Skew = 5 * time.Second

// Claims is what the API asserts about one viewer.
type Claims struct {
	Sub     string `json:"sub"`
	Machine string `json:"machine"`
	Mode    string `json:"mode"`
	Exp     int64  `json:"exp"`
	JTI     string `json:"jti"`
}

var (
	ErrNoKey        = errors.New("no stream key has been delivered")
	ErrMalformed    = errors.New("malformed ticket")
	ErrSignature    = errors.New("bad signature")
	ErrMachine      = errors.New("ticket is for another machine")
	ErrExpired      = errors.New("ticket expired")
	ErrMode         = errors.New("ticket mode is not view or control")
	ErrReplayed     = errors.New("ticket already used")
	errEmptyPayload = errors.New("empty payload")
)

// Secret is the key and the machine id it belongs to.
type Secret struct {
	Key     []byte
	Machine string
}

// Source supplies the current secret. Re-read rather than cached forever, so
// a resumed sandbox handed a fresh file takes it without a restart.
type Source interface {
	Secret() (Secret, error)
}

// FileSource reads `{"key":"<hex>","machine":"<id>"}`, again whenever the
// file's mtime moves.
type FileSource struct {
	Path string

	mu     sync.Mutex
	mtime  time.Time
	size   int64
	cached Secret
}

func (f *FileSource) Secret() (Secret, error) {
	info, err := os.Stat(f.Path)
	if err != nil {
		return Secret{}, ErrNoKey
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if info.ModTime().Equal(f.mtime) && info.Size() == f.size && len(f.cached.Key) > 0 {
		return f.cached, nil
	}
	raw, err := os.ReadFile(f.Path)
	if err != nil || len(raw) > 4096 {
		return Secret{}, ErrNoKey
	}
	var body struct {
		Key     string `json:"key"`
		Machine string `json:"machine"`
	}
	if json.Unmarshal(raw, &body) != nil {
		return Secret{}, ErrNoKey
	}
	key, err := hex.DecodeString(strings.TrimSpace(body.Key))
	if err != nil || len(key) < 16 || body.Machine == "" {
		return Secret{}, ErrNoKey
	}
	f.cached = Secret{Key: key, Machine: body.Machine}
	f.mtime, f.size = info.ModTime(), info.Size()
	return f.cached, nil
}

// StaticSource is a fixed secret, for tests and the integration check.
type StaticSource Secret

func (s StaticSource) Secret() (Secret, error) {
	if len(s.Key) == 0 {
		return Secret{}, ErrNoKey
	}
	return Secret(s), nil
}

// Verifier checks tickets and remembers which have been used.
type Verifier struct {
	Source Source
	Now    func() time.Time

	mu   sync.Mutex
	seen map[string]int64 // jti -> exp
}

func NewVerifier(src Source) *Verifier {
	return &Verifier{Source: src, Now: time.Now, seen: map[string]int64{}}
}

// Verify runs the checks in a fixed order, so the first failure is the one
// reported: signature, machine, expiry, mode, replay.
func (v *Verifier) Verify(token string) (Claims, error) {
	var c Claims
	secret, err := v.Source.Secret()
	if err != nil {
		return c, ErrNoKey
	}
	payload, sig, ok := strings.Cut(token, ".")
	if !ok || payload == "" || sig == "" || strings.Contains(sig, ".") || len(token) > 2048 {
		return c, ErrMalformed
	}
	got, err := base64.RawURLEncoding.DecodeString(sig)
	if err != nil {
		return c, ErrMalformed
	}
	if !hmac.Equal(got, sign(secret.Key, payload)) {
		return c, ErrSignature
	}
	raw, err := base64.RawURLEncoding.DecodeString(payload)
	if err != nil {
		return c, ErrMalformed
	}
	if err := json.Unmarshal(raw, &c); err != nil {
		return c, ErrMalformed
	}
	if c.Machine != secret.Machine {
		return c, ErrMachine
	}
	now := v.Now()
	if now.Add(-Skew).Unix() > c.Exp {
		return c, ErrExpired
	}
	if c.Mode != ModeView && c.Mode != ModeControl {
		return c, ErrMode
	}
	if c.JTI == "" || c.Sub == "" {
		return c, ErrMalformed
	}
	v.mu.Lock()
	defer v.mu.Unlock()
	for jti, exp := range v.seen {
		if now.Add(-Skew).Unix() > exp {
			delete(v.seen, jti)
		}
	}
	if _, used := v.seen[c.JTI]; used {
		return c, ErrReplayed
	}
	v.seen[c.JTI] = c.Exp
	return c, nil
}

func sign(key []byte, payload string) []byte {
	m := hmac.New(sha256.New, key)
	m.Write([]byte(payload))
	return m.Sum(nil)
}

// Mint signs claims the way the API does. Used by tests and the integration
// check; production tickets come from the API.
func Mint(key []byte, c Claims) string {
	b, _ := json.Marshal(c)
	if len(b) == 0 {
		panic(errEmptyPayload)
	}
	p := base64.RawURLEncoding.EncodeToString(b)
	return p + "." + base64.RawURLEncoding.EncodeToString(sign(key, p))
}
