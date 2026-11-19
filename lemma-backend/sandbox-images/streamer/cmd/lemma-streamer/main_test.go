//go:build linux

package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestSignalRefusesAnyRequestWithAnOrigin(t *testing.T) {
	for _, origin := range []string{"https://evil.example", "null", ""} {
		r := httptest.NewRequest(http.MethodGet, "/signal", nil)
		r.Header["Origin"] = []string{origin}
		w := httptest.NewRecorder()
		if !refuseBrowsers(w, r) || w.Code != http.StatusForbidden {
			t.Fatalf("origin %q was not refused (code %d)", origin, w.Code)
		}
	}
}

func TestSignalAdmitsTheRelay(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/signal", nil)
	w := httptest.NewRecorder()
	if refuseBrowsers(w, r) {
		t.Fatal("a request with no Origin was refused")
	}
}
