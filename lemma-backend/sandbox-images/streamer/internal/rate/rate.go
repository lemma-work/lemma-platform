// Package rate decides how many bits and frames the stream may spend.
//
// Two inputs, two outputs. The network's estimate (one per viewer, from
// congestion control) sets the encoder's bitrate: one encoder serves every
// viewer of a codec, so it follows the slowest of them. The machine's spare
// CPU sets the frame rate, because the stream must never starve the agent's
// own work on the machine it is showing.
package rate

import "time"

const (
	MinBitrate   = 300_000
	StartBitrate = 2_500_000
	// Ceilings, not targets: congestion control climbs only this far even on
	// a fast link. Past it a page of text and UI looks no better, and the
	// extra bytes are what a viewer on a metered link pays for. Measured with
	// `scripts/stream-bench.mjs`; the plan's budget is under 4 Mbps at 1x.
	MaxBitrateSmall = 3_500_000
	MaxBitrateLarge = 7_000_000
	// Above this many pixels a frame is "large" (beyond ~1280x860).
	LargeFramePixels = 1_100_000

	// Apply a new target when it moved this much, or this long has passed.
	changeFraction = 0.10
	refresh        = time.Second
)

// MaxFor is the ceiling for a frame size.
func MaxFor(w, h int) int {
	if w*h > LargeFramePixels {
		return MaxBitrateLarge
	}
	return MaxBitrateSmall
}

// Target is the bitrate one encoder should run at, given its viewers'
// estimates. No estimates yet means the start rate.
func Target(estimates []int, w, h int) int {
	if len(estimates) == 0 {
		return clamp(StartBitrate, w, h)
	}
	low := estimates[0]
	for _, e := range estimates[1:] {
		if e < low {
			low = e
		}
	}
	return clamp(low, w, h)
}

func clamp(v, w, h int) int {
	if v < MinBitrate {
		return MinBitrate
	}
	if m := MaxFor(w, h); v > m {
		return m
	}
	return v
}

// Governor smooths how often the encoder is told.
type Governor struct {
	current int
	at      time.Time
}

// Next returns the bitrate to apply and whether to apply it now.
func (g *Governor) Next(target int, now time.Time) (int, bool) {
	if g.current == 0 {
		g.current, g.at = target, now
		return target, true
	}
	moved := float64(abs(target-g.current)) / float64(g.current)
	if moved > changeFraction || (target != g.current && now.Sub(g.at) >= refresh) {
		g.current, g.at = target, now
		return target, true
	}
	return g.current, false
}

func abs(v int) int {
	if v < 0 {
		return -v
	}
	return v
}

// Frame rates the CPU floor steps through.
const (
	FullFPS = 30
	LowFPS  = 15
	MinFPS  = 5
)

// Free-core thresholds. Stepping down happens below the first number;
// stepping back up needs the second, so a machine hovering at the edge does
// not flap between rates every sample.
const (
	downTo15 = 0.25
	upFrom15 = 0.40
	downTo5  = 0.10
	upFrom5  = 0.20
)

// FPSFor is the frame rate for this much spare CPU (in cores), given the
// current one.
func FPSFor(free float64, current int) int {
	switch current {
	case MinFPS:
		if free >= upFrom15 {
			return FullFPS
		}
		if free >= upFrom5 {
			return LowFPS
		}
		return MinFPS
	case LowFPS:
		if free < downTo5 {
			return MinFPS
		}
		if free >= upFrom15 {
			return FullFPS
		}
		return LowFPS
	default:
		if free < downTo5 {
			return MinFPS
		}
		if free < downTo15 {
			return LowFPS
		}
		return FullFPS
	}
}
