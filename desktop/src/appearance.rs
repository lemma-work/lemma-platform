use super::*;

/// The user's System Settings accent, as sRGB bytes.
///
/// `controlAccentColor` is a catalog colour with no component accessors of its
/// own — it has to be resolved into a real colour space before it can be read,
/// which is what the `colorUsingColorSpace` hop is for. Returns `None` if that
/// resolution fails, and callers fall back to systemBlue, the macOS default.
///
/// The main-thread gate is not a formality. AppKit is main-thread-only, and
/// this is reached from threads that are not it: `open_local_settings` builds
/// the control webview from a spawned thread on purpose, and the Rust test
/// harness runs every test on a spawned thread of a process that never
/// created an NSApplication at all. Two of those test threads landing in
/// AppKit's first-use initialization at once is what killed the whole
/// `lemma-desktop` test binary with SIGSEGV partway through a CI run — no
/// assertion failed, the process simply died, and the two tests that call
/// `desktop_context_script` were the only two that never reported a result.
///
/// So the read happens on the main thread and its answer is remembered.
/// Everyone else gets the remembered answer — which the main window's setup
/// has already stored by the time any worker can ask — or `None` before the
/// first read, which is the same fallback a machine that cannot answer gets.
#[cfg(target_os = "macos")]
pub(crate) fn macos_accent_rgb() -> Option<(u8, u8, u8)> {
    use objc2::MainThreadMarker;
    use objc2_app_kit::{NSColor, NSColorSpace};

    fn remembered() -> Option<(u8, u8, u8)> {
        *REMEMBERED_ACCENT
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    if MainThreadMarker::new().is_none() {
        return remembered();
    }
    let accent = NSColor::controlAccentColor();
    let Some(srgb) = accent.colorUsingColorSpace(&NSColorSpace::sRGBColorSpace()) else {
        // A failed resolution says nothing about the accent that was read
        // before it, so the remembered one stands rather than being cleared.
        return remembered();
    };
    let to_byte = |v: f64| (v.clamp(0.0, 1.0) * 255.0).round() as u8;
    let rgb = (
        to_byte(srgb.redComponent()),
        to_byte(srgb.greenComponent()),
        to_byte(srgb.blueComponent()),
    );
    *REMEMBERED_ACCENT
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(rgb);
    Some(rgb)
}

#[cfg(not(target_os = "macos"))]
pub(crate) fn macos_accent_rgb() -> Option<(u8, u8, u8)> {
    None
}

/// `"90 63 212"` — the channel triple the frontend's `--accent-rgb` expects.
///
/// Falls back to the brand violet, which is also what the web build uses, so a
/// machine that cannot answer looks like every other install rather than blue.
pub(crate) fn accent_channel_triple() -> String {
    let (r, g, b) = macos_accent_rgb().unwrap_or((90, 63, 212));
    format!("{r} {g} {b}")
}

/// Opt-in until every surface that must stay opaque paints its own background.
#[cfg(target_os = "macos")]
pub(crate) fn desktop_vibrancy_enabled() -> bool {
    std::env::var("LEMMA_DESKTOP_VIBRANCY").as_deref() == Ok("1")
}

#[cfg(not(target_os = "macos"))]
pub(crate) fn desktop_vibrancy_enabled() -> bool {
    false
}

/// setting to offer later — it just is not the default identity.
pub(crate) fn desktop_system_accent_enabled() -> bool {
    std::env::var("LEMMA_DESKTOP_SYSTEM_ACCENT").as_deref() == Ok("1")
}

pub(crate) fn desktop_context_script(mode: &str) -> String {
    let context = json!({
        "version": env!("CARGO_PKG_VERSION"),
        "mode": mode,
        "platform": std::env::consts::OS,
        "accentRgb": accent_channel_triple(),
        "systemAccent": desktop_system_accent_enabled(),
        "vibrancy": desktop_vibrancy_enabled(),
    });
    let local_auth = if mode == "local" {
        // NEXT_PUBLIC values are also rendered into the native host-pack
        // environment. Inject the local auth policy before any page script as
        // a cache-independent guard for an already-open desktop webview.
        "window.__LEMMA_AUTH_CONFIG__ = Object.freeze({AUTH_EMAIL_VERIFICATION_REQUIRED: \"false\"});"
    } else {
        ""
    };
    // Runs before any page script, so whatever it sets is already true at first
    // paint rather than corrected a frame later — and it runs again for every
    // document, which is the reason none of this is a one-shot eval: local mode
    // navigates this same window from the splash to the workspace, and an
    // attribute written by eval would not survive that navigation.
    //
    // The platform attribute always goes on; the accent override and the
    // vibrancy attribute only when their flag asked for them.
    let bootstrap = "(function () {\
          var d = window.__LEMMA_DESKTOP__;\
          var root = document.documentElement;\
          if (!d || !root) return;\
          root.setAttribute('data-desktop-platform', d.platform);\
          if (d.systemAccent) root.style.setProperty('--accent-rgb', d.accentRgb);\
          if (d.vibrancy) root.setAttribute('data-desktop-vibrancy', 'macos');\
        })();";
    format!(
        "window.__LEMMA_DESKTOP__ = Object.freeze({});{}{}",
        serde_json::to_string(&context).unwrap_or_else(|_| "{}".into()),
        bootstrap,
        local_auth,
    )
}

// ---------------------------------------------------------------------------

/// The window layer's colour for an appearance.
pub(crate) fn canvas_color(theme: tauri::Theme) -> tauri::window::Color {
    match theme {
        tauri::Theme::Dark => CANVAS_DARK,
        _ => CANVAS_LIGHT,
    }
}
