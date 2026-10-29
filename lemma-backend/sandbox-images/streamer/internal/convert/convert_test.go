package convert

import (
	"math"
	"math/rand"
	"testing"
)

// reference is the textbook float conversion, per pixel and per 2x2 block.
func reference(src []byte, stride, w, h int) *I420 {
	out := NewI420(w, h)
	px := func(x, y int) (float64, float64, float64) {
		if x >= w {
			x = w - 1
		}
		if y >= h {
			y = h - 1
		}
		i := y*stride + x*4
		return float64(src[i+2]), float64(src[i+1]), float64(src[i])
	}
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			r, g, b := px(x, y)
			out.Y[y*w+x] = byte(math.Round(16 + 0.257*r + 0.504*g + 0.098*b))
		}
	}
	for cy := 0; cy < (h+1)/2; cy++ {
		for cx := 0; cx < (w+1)/2; cx++ {
			var r, g, b float64
			for _, d := range [][2]int{{0, 0}, {1, 0}, {0, 1}, {1, 1}} {
				pr, pg, pb := px(cx*2+d[0], cy*2+d[1])
				r, g, b = r+pr/4, g+pg/4, b+pb/4
			}
			out.U[cy*out.StrideC+cx] = byte(math.Round(128 - 0.148*r - 0.291*g + 0.439*b))
			out.V[cy*out.StrideC+cx] = byte(math.Round(128 + 0.439*r - 0.368*g - 0.071*b))
		}
	}
	return out
}

func within(t *testing.T, name string, got, want []byte, tol int) {
	t.Helper()
	for i := range want {
		d := int(got[i]) - int(want[i])
		if d < -tol || d > tol {
			t.Fatalf("%s[%d] = %d, want %d±%d", name, i, got[i], want[i], tol)
		}
	}
}

func TestMatchesReference(t *testing.T) {
	for _, size := range [][2]int{{1, 1}, {3, 3}, {17, 9}, {64, 48}, {1281, 801}} {
		w, h := size[0], size[1]
		stride := w*4 + 8 // X pads rows; the stride must be honoured
		src := make([]byte, stride*h)
		rand.New(rand.NewSource(int64(w))).Read(src)
		got := NewI420(w, h)
		BGRAToI420(src, stride, got)
		want := reference(src, stride, w, h)
		within(t, "Y", got.Y, want.Y, 1)
		within(t, "U", got.U, want.U, 2)
		within(t, "V", got.V, want.V, 2)
	}
}

func TestKnownColours(t *testing.T) {
	cases := []struct {
		b, g, r byte
		y, u, v byte
	}{
		{0, 0, 0, 16, 128, 128},
		{255, 255, 255, 235, 128, 128},
		{0, 0, 255, 82, 90, 240},
	}
	for _, c := range cases {
		src := []byte{c.b, c.g, c.r, 0, c.b, c.g, c.r, 0, c.b, c.g, c.r, 0, c.b, c.g, c.r, 0}
		out := NewI420(2, 2)
		BGRAToI420(src, 8, out)
		if d := int(out.Y[0]) - int(c.y); d < -1 || d > 1 {
			t.Errorf("Y %d want %d", out.Y[0], c.y)
		}
		if d := int(out.U[0]) - int(c.u); d < -1 || d > 1 {
			t.Errorf("U %d want %d", out.U[0], c.u)
		}
		if d := int(out.V[0]) - int(c.v); d < -1 || d > 1 {
			t.Errorf("V %d want %d", out.V[0], c.v)
		}
	}
}

func BenchmarkBGRAToI420_1280x800(b *testing.B) {
	w, h := 1280, 800
	src := make([]byte, w*4*h)
	rand.New(rand.NewSource(1)).Read(src)
	dst := NewI420(w, h)
	b.SetBytes(int64(len(src)))
	for i := 0; i < b.N; i++ {
		BGRAToI420(src, w*4, dst)
	}
}
