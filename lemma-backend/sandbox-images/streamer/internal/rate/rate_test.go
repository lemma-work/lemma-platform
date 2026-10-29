package rate

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestTarget(t *testing.T) {
	cases := []struct {
		est  []int
		w, h int
		want int
	}{
		{nil, 1280, 800, StartBitrate},
		{[]int{4_000_000, 1_000_000, 3_000_000}, 1280, 800, 1_000_000},
		{[]int{100}, 1280, 800, MinBitrate},
		{[]int{50_000_000}, 1280, 800, MaxBitrateSmall},
		{[]int{50_000_000}, 2560, 1600, MaxBitrateLarge},
	}
	for _, c := range cases {
		if got := Target(c.est, c.w, c.h); got != c.want {
			t.Errorf("Target(%v, %dx%d) = %d, want %d", c.est, c.w, c.h, got, c.want)
		}
	}
}

func TestGovernor(t *testing.T) {
	var g Governor
	t0 := time.Unix(0, 0)
	if v, ok := g.Next(2_000_000, t0); !ok || v != 2_000_000 {
		t.Fatal("first value not applied")
	}
	if _, ok := g.Next(2_100_000, t0.Add(100*time.Millisecond)); ok {
		t.Fatal("5% change applied immediately")
	}
	if v, ok := g.Next(2_100_000, t0.Add(1100*time.Millisecond)); !ok || v != 2_100_000 {
		t.Fatal("small change not applied after the refresh")
	}
	if v, ok := g.Next(1_000_000, t0.Add(1200*time.Millisecond)); !ok || v != 1_000_000 {
		t.Fatal("large drop not applied at once")
	}
}

func TestFPSFor(t *testing.T) {
	steps := []struct {
		free float64
		want int
	}{
		{1.0, 30}, {0.3, 30}, {0.2, 15}, {0.3, 15}, {0.45, 30},
		{0.05, 5}, {0.15, 5}, {0.25, 15}, {0.05, 5}, {0.5, 30},
	}
	fps := FullFPS
	for i, s := range steps {
		fps = FPSFor(s.free, fps)
		if fps != s.want {
			t.Fatalf("step %d (free %.2f): %d, want %d", i, s.free, fps, s.want)
		}
	}
}

func TestParseCPUMax(t *testing.T) {
	if q, ok := ParseCPUMax("100000 100000\n"); !ok || q != 1 {
		t.Fatal(q, ok)
	}
	if q, ok := ParseCPUMax("250000 100000"); !ok || q != 2.5 {
		t.Fatal(q, ok)
	}
	if _, ok := ParseCPUMax("max 100000"); ok {
		t.Fatal("max read as a quota")
	}
}

func TestCPUFromCgroup(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "sys/fs/cgroup")
	_ = os.MkdirAll(dir, 0o755)
	write := func(usec string) {
		_ = os.WriteFile(filepath.Join(dir, "cpu.stat"), []byte("usage_usec "+usec+"\nuser_usec 1\n"), 0o644)
	}
	_ = os.WriteFile(filepath.Join(dir, "cpu.max"), []byte("100000 100000\n"), 0o644)
	c := &CPU{Root: root, horizon: 5 * time.Second}
	t0 := time.Unix(0, 0)
	write("0")
	if f := c.Sample(t0); f != -1 {
		t.Fatalf("no window yet, got %v", f)
	}
	write("1600000") // 1.6 core-seconds over 2 s on a 1-core quota
	if f := c.Sample(t0.Add(2 * time.Second)); f < 0.19 || f > 0.21 {
		t.Fatalf("free %.3f, want 0.2", f)
	}
}

func TestProcStat(t *testing.T) {
	s := "cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 50 0 50 350 50 0 0 0 0 0\ncpu1 50 0 50 350 50 0 0 0 0 0\n"
	used, cores, ok := procStat(s)
	if !ok || cores != 2 || used != 2 {
		t.Fatalf("used %v cores %v ok %v", used, cores, ok)
	}
}
