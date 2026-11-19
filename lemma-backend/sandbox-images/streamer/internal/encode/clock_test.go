package encode

import (
	"testing"
	"time"
)

func TestFrameClockFollowsRealTime(t *testing.T) {
	at := time.Unix(100, 0)
	defer func(saved func() time.Time) { now = saved }(now)
	now = func() time.Time { return at }

	c := newFrameClock()
	if pts, d := c.next(); pts != 0 || d != 1 {
		t.Fatalf("first frame %d/%d", pts, d)
	}
	at = at.Add(66 * time.Millisecond)
	if pts, d := c.next(); pts != 66 || d != 66 {
		t.Fatalf("a 15 fps frame stamped %d/%d", pts, d)
	}
	// Two frames in the same millisecond still move forward.
	if pts, d := c.next(); pts != 67 || d != 1 {
		t.Fatalf("same-instant frame stamped %d/%d", pts, d)
	}
}
