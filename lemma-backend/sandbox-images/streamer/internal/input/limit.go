package input

import (
	"sync"
	"time"
)

// Rates a controller may send at. Well above anything a hand produces, and
// low enough that a hostile client cannot turn the X server into a busy loop.
const (
	MessagesPerSecond = 1000
	WheelPerSecond    = 120
)

// Limiter is two token buckets: everything, and wheel notches on their own,
// because a wheel event is a press and a release per notch.
type Limiter struct {
	mu    sync.Mutex
	now   func() time.Time
	all   bucket
	wheel bucket
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
	return &Limiter{now: now, all: bucket{rate: MessagesPerSecond}, wheel: bucket{rate: WheelPerSecond}}
}

// Allow reports whether one more message (and, for a wheel, its notches) fits.
func (l *Limiter) Allow(c Command) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := l.now()
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
