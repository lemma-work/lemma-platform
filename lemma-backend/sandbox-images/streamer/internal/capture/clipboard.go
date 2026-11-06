//go:build linux

package capture

import (
	"context"
	"io"
	"os/exec"
	"time"
	"unicode/utf8"
)

// MaxClipboard is the most clipboard text sent to a controller.
const MaxClipboard = 1 << 20

// ReadClipboard asks xclip for the clipboard as text. Empty when the owner
// offers no text (an image, say) or does not answer in time.
func ReadClipboard() string {
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "xclip", "-o", "-selection", "clipboard", "-t", "UTF8_STRING")
	out, err := cmd.StdoutPipe()
	if err != nil {
		return ""
	}
	if cmd.Start() != nil {
		return ""
	}
	b, _ := io.ReadAll(io.LimitReader(out, MaxClipboard+1))
	_ = cmd.Wait()
	if len(b) > MaxClipboard || !utf8.Valid(b) {
		return ""
	}
	return string(b)
}
