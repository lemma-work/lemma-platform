package input

import (
	"sync"
	"time"
)

// Rates a controller may send at. Well above anything a hand produces, and
// low enough that a hostile client cannot turn the X server into a busy loop.
//
// A paste is not a keystroke: it is up to a megabyte, an `xclip` and a
// clipboard handover, so it has a budget of its own -- a person pastes a few
// times a minute, not a thousand times a second -- and is checked before the
// shared one so a refused paste costs the keyboard nothing.
const (
	MessagesPerSecond = 1000
	WheelPerSecond    = 120
	PastePerSecond    = 2
)

// Limiter is token buckets: everything, wheel notches on their own (a wheel
// event is a press and a release per notch), and pastes on their own.
type Limiter struct {
	mu    sync.Mutex
	now   func() time.Time
	all   bucket
	wheel bucket
	paste bucket
}

type bucket struct {
	tokens float64
	rate   float64
	at     time.Time
}

func (b *bucket) take(now time.Time, n float64) bool {
	if b.at.IsZero() {
		b.tokens, b.at = b.rate, now
	}
	b.tokens += now.Sub(b.at).Seconds() * b.rate
	if b.tokens > b.rate {
		b.tokens = b.rate
	}
	b.at = now
	if b.tokens < n {
		return false
	}
	b.tokens -= n
	return true
}

func NewLimiter(now func() time.Time) *Limiter {
	if now == nil {
		now = time.Now
	}
	return &Limiter{
		now:   now,
		all:   bucket{rate: MessagesPerSecond},
		wheel: bucket{rate: WheelPerSecond},
		paste: bucket{rate: PastePerSecond},
	}
}

// Allow reports whether one more message (and, for a wheel, its notches) fits.
func (l *Limiter) Allow(c Command) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := l.now()
	if c.Kind == Paste && !l.paste.take(now, 1) {
		return false
	}
	if !l.all.take(now, 1) {
		return false
	}
	if c.Kind == Wheel {
		n := abs(c.DX) + abs(c.DY)
		return l.wheel.take(now, float64(n))
	}
	return true
}

func abs(v int) int {
	if v < 0 {
		return -v
	}
	return v
}
