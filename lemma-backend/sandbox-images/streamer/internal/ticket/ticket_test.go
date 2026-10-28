package ticket

import (
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

var key = []byte("0123456789abcdef0123456789abcdef")

func fixed(now time.Time) *Verifier {
	v := NewVerifier(StaticSource{Key: key, Machine: "m-1"})
	v.Now = func() time.Time { return now }
	return v
}

func claims(now time.Time) Claims {
	return Claims{Sub: "u-1", Machine: "m-1", Mode: ModeControl, Exp: now.Add(60 * time.Second).Unix(), JTI: "j-1"}
}

func TestVerify(t *testing.T) {
	now := time.Unix(1_800_000_000, 0)
	good := claims(now)

	flipped := func() string {
		tok := Mint(key, good)
		p, s, _ := strings.Cut(tok, ".")
		raw, _ := base64.RawURLEncoding.DecodeString(p)
		var c map[string]any
		_ = json.Unmarshal(raw, &c)
		c["mode"] = "view"
		b, _ := json.Marshal(c)
		return base64.RawURLEncoding.EncodeToString(b) + "." + s
	}

	cases := []struct {
		name  string
		token string
		want  error
	}{
		{"valid", Mint(key, good), nil},
		{"garbage", "not a ticket", ErrMalformed},
		{"empty", "", ErrMalformed},
		{"three parts", Mint(key, good) + ".x", ErrMalformed},
		{"wrong key", Mint([]byte("another-key-another-key-another!!"), good), ErrSignature},
		{"mode flipped", flipped(), ErrSignature},
		{"other machine", Mint(key, Claims{Sub: "u", Machine: "m-2", Mode: "view", Exp: good.Exp, JTI: "a"}), ErrMachine},
		{"expired", Mint(key, Claims{Sub: "u", Machine: "m-1", Mode: "view", Exp: now.Add(-10 * time.Second).Unix(), JTI: "b"}), ErrExpired},
		{"within skew", Mint(key, Claims{Sub: "u", Machine: "m-1", Mode: "view", Exp: now.Add(-3 * time.Second).Unix(), JTI: "c"}), nil},
		{"bad mode", Mint(key, Claims{Sub: "u", Machine: "m-1", Mode: "admin", Exp: good.Exp, JTI: "d"}), ErrMode},
		{"no jti", Mint(key, Claims{Sub: "u", Machine: "m-1", Mode: "view", Exp: good.Exp}), ErrMalformed},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := fixed(now).Verify(tc.token)
			if !errors.Is(err, tc.want) {
				t.Fatalf("got %v, want %v", err, tc.want)
			}
		})
	}
}

func TestReplayRefused(t *testing.T) {
	now := time.Unix(1_800_000_000, 0)
	v := fixed(now)
	tok := Mint(key, claims(now))
	if _, err := v.Verify(tok); err != nil {
		t.Fatal(err)
	}
	if _, err := v.Verify(tok); !errors.Is(err, ErrReplayed) {
		t.Fatalf("second use: %v", err)
	}
}

func TestSeenSetIsPruned(t *testing.T) {
	now := time.Unix(1_800_000_000, 0)
	v := fixed(now)
	_, _ = v.Verify(Mint(key, claims(now)))
	v.Now = func() time.Time { return now.Add(time.Hour) }
	_, _ = v.Verify(Mint(key, Claims{Sub: "u", Machine: "m-1", Mode: "view", Exp: now.Add(time.Hour + time.Minute).Unix(), JTI: "later"}))
	if len(v.seen) != 1 {
		t.Fatalf("seen set holds %d, want 1", len(v.seen))
	}
}

func TestNoKeyRefusesEverything(t *testing.T) {
	v := NewVerifier(&FileSource{Path: filepath.Join(t.TempDir(), "absent.json")})
	if _, err := v.Verify(Mint(key, claims(time.Now()))); !errors.Is(err, ErrNoKey) {
		t.Fatalf("got %v", err)
	}
}

func TestFileSourceRereadsOnChange(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stream.json")
	write := func(machine string, at time.Time) {
		body := `{"key":"` + hex.EncodeToString(key) + `","machine":"` + machine + `"}`
		if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
		_ = os.Chtimes(path, at, at)
	}
	src := &FileSource{Path: path}
	write("m-1", time.Unix(1000, 0))
	s, err := src.Secret()
	if err != nil || s.Machine != "m-1" || string(s.Key) != string(key) {
		t.Fatalf("first read: %+v %v", s, err)
	}
	write("m-9", time.Unix(2000, 0))
	if s, _ = src.Secret(); s.Machine != "m-9" {
		t.Fatalf("not re-read: %s", s.Machine)
	}
	if err := os.WriteFile(path, []byte(`{"key":"zz","machine":"m"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	_ = os.Chtimes(path, time.Unix(3000, 0), time.Unix(3000, 0))
	if _, err := src.Secret(); !errors.Is(err, ErrNoKey) {
		t.Fatalf("bad key accepted: %v", err)
	}
}

func FuzzVerify(f *testing.F) {
	now := time.Unix(1_800_000_000, 0)
	f.Add(Mint(key, claims(now)))
	f.Add("a.b")
	f.Add("..")
	f.Fuzz(func(t *testing.T, tok string) {
		v := fixed(now)
		c, err := v.Verify(tok)
		if err == nil && (c.Machine != "m-1" || (c.Mode != ModeView && c.Mode != ModeControl)) {
			t.Fatalf("accepted %+v", c)
		}
	})
}
