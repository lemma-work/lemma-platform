package rate

import (
	"os"
	"strconv"
	"strings"
	"time"
)

// CPU reports how many cores the machine has to spare, averaged over a
// window. A cgroup's quota is the real limit inside a container or an E2B
// sandbox; /proc/stat is the fallback where there is no cgroup v2.
type CPU struct {
	Root string // "" means "/"; tests point it at a fixture

	window  []sample
	horizon time.Duration
}

type sample struct {
	at    time.Time
	used  float64 // cumulative core-seconds used
	cores float64 // capacity in cores
}

func NewCPU() *CPU { return &CPU{horizon: 5 * time.Second} }

// Sample reads the counters once and returns free cores over the window, or
// -1 while there is not yet a window to average over.
func (c *CPU) Sample(now time.Time) float64 {
	used, cores, ok := c.read()
	if !ok {
		return -1
	}
	c.window = append(c.window, sample{now, used, cores})
	for len(c.window) > 2 && now.Sub(c.window[1].at) >= c.horizon {
		c.window = c.window[1:]
	}
	first := c.window[0]
	span := now.Sub(first.at).Seconds()
	if span <= 0 {
		return -1
	}
	return Free(used-first.used, span, cores)
}

// Free is spare cores, given core-seconds used over a span.
func Free(usedCoreSeconds, spanSeconds, cores float64) float64 {
	f := cores - usedCoreSeconds/spanSeconds
	if f < 0 {
		return 0
	}
	return f
}

func (c *CPU) path(p string) string {
	if c.Root == "" {
		return p
	}
	return c.Root + p
}

func (c *CPU) read() (used, cores float64, ok bool) {
	if raw, err := os.ReadFile(c.path("/sys/fs/cgroup/cpu.stat")); err == nil {
		if usec, found := field(string(raw), "usage_usec"); found {
			return usec / 1e6, c.quota(), true
		}
	}
	raw, err := os.ReadFile(c.path("/proc/stat"))
	if err != nil {
		return 0, 0, false
	}
	return procStat(string(raw))
}

// quota is cpu.max as cores, else the number of CPUs the kernel lists.
func (c *CPU) quota() float64 {
	if raw, err := os.ReadFile(c.path("/sys/fs/cgroup/cpu.max")); err == nil {
		if q, ok := ParseCPUMax(string(raw)); ok {
			return q
		}
	}
	if raw, err := os.ReadFile(c.path("/proc/stat")); err == nil {
		n := 0
		for _, line := range strings.Split(string(raw), "\n") {
			if strings.HasPrefix(line, "cpu") && !strings.HasPrefix(line, "cpu ") {
				n++
			}
		}
		if n > 0 {
			return float64(n)
		}
	}
	return 1
}

// ParseCPUMax reads "200000 100000" as 2 cores; "max ..." is unlimited.
func ParseCPUMax(s string) (float64, bool) {
	parts := strings.Fields(s)
	if len(parts) != 2 || parts[0] == "max" {
		return 0, false
	}
	q, err1 := strconv.ParseFloat(parts[0], 64)
	p, err2 := strconv.ParseFloat(parts[1], 64)
	if err1 != nil || err2 != nil || p <= 0 {
		return 0, false
	}
	return q / p, true
}

func field(s, name string) (float64, bool) {
	for _, line := range strings.Split(s, "\n") {
		k, v, ok := strings.Cut(line, " ")
		if ok && k == name {
			f, err := strconv.ParseFloat(strings.TrimSpace(v), 64)
			return f, err == nil
		}
	}
	return 0, false
}

// procStat sums busy jiffies across all CPUs (USER_HZ is 100 on every Linux
// we run on).
func procStat(s string) (used, cores float64, ok bool) {
	for _, line := range strings.Split(s, "\n") {
		if strings.HasPrefix(line, "cpu") && !strings.HasPrefix(line, "cpu ") {
			cores++
		}
	}
	for _, line := range strings.Split(s, "\n") {
		if !strings.HasPrefix(line, "cpu ") {
			continue
		}
		f := strings.Fields(line)[1:]
		var busy float64
		for i, v := range f {
			n, err := strconv.ParseFloat(v, 64)
			if err != nil {
				return 0, 0, false
			}
			if i == 3 || i == 4 { // idle, iowait
				continue
			}
			if i >= 8 { // guest time is already counted in user
				break
			}
			busy += n
		}
		return busy / 100, cores, cores > 0
	}
	return 0, 0, false
}
