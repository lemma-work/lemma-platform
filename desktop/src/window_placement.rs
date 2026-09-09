use super::*;

pub(crate) fn placement_of(window: &tauri::Window) -> Option<WindowPlacement> {
    Some(WindowPlacement {
        position: window.outer_position().ok()?,
        size: window.inner_size().ok()?,
    })
}

/// Where the window was when the app last closed.
///
/// Everything about a window's placement is a decision the user made with a
/// mouse, and the app threw all of it away on every quit -- so somebody who
/// works on a 34" display had Lemma come back at 1280x860 in the middle of it,
/// every single morning.
///
/// Read defensively. This is the one piece of state the app restores from disk
/// *before* it can show anything, so a bad value here is an app that opens
/// somewhere the user cannot reach it.
pub(crate) fn remembered_placement(handle: &AppHandle) -> Option<WindowPlacement> {
    let placement = saved_placement(&read_config())?;
    let monitors = match handle.available_monitors() {
        // Nothing to check against is not evidence of a problem. Restoring is
        // the behaviour the user asked for by moving the window in the first
        // place, and the OS still clamps a wildly wrong value.
        Err(_) => return Some(placement),
        Ok(monitors) => monitors,
    };
    let screens: Vec<_> = monitors
        .iter()
        .map(|monitor| (*monitor.position(), *monitor.size()))
        .collect();
    placement_is_reachable(&placement, &screens).then_some(placement)
}

/// Parse a recorded placement, refusing anything that would restore wrong.
///
/// Split from the monitor check so both halves can be tested: this one is
/// about a file that may have been written by another build, hand-edited, or
/// truncated mid-write.
pub(crate) fn saved_placement(config: &Value) -> Option<WindowPlacement> {
    let saved = config.get("window")?;
    let number = |key: &str| saved.get(key)?.as_i64();
    let width = u32::try_from(number("width")?).ok()?;
    let height = u32::try_from(number("height")?).ok()?;
    if width < MIN_RESTORED.0 || height < MIN_RESTORED.1 {
        return None;
    }
    Some(WindowPlacement {
        position: tauri::PhysicalPosition::new(
            i32::try_from(number("x")?).ok()?,
            i32::try_from(number("y")?).ok()?,
        ),
        size: tauri::PhysicalSize::new(width, height),
    })
}

/// A saved placement in the units the window builder actually reads.
///
/// Everything else about placement is in physical pixels and consistently so:
/// `outer_position` and `inner_size` return physical, and
/// `placement_is_reachable` compares them against monitor geometry that is also
/// physical. The window *builder* is the one place that is not --
/// `WebviewWindowBuilder::position` and `inner_size` are documented as logical
/// pixels -- and handing it physical values was silently wrong on every display
/// that is not 1:1.
///
/// On a 2x screen it doubled both: a window saved at 3024x1898 came back asking
/// for 3024x1898 *logical*, which is 6048x3796 physical, so macOS clamped the
/// size to the visible frame, and a saved y of 66 became 132 -- the window
/// opened lower than it was left and short of the top of the screen. Restoring
/// looked broken in a way that reads as "the app won't remember my window".
///
/// Returns None when the window would come back smaller than the app's own
/// minimum. That check belongs here rather than beside the parse, because
/// `MIN_RESTORED` is a logical size and until this point the numbers are not.
pub(crate) fn placement_in_logical(
    placement: &WindowPlacement,
    scale: f64,
) -> Option<(tauri::LogicalPosition<f64>, tauri::LogicalSize<f64>)> {
    if !scale.is_finite() || scale <= 0.0 {
        return None;
    }
    let position = placement.position.to_logical::<f64>(scale);
    let size = placement.size.to_logical::<f64>(scale);
    if size.width < f64::from(MIN_RESTORED.0) || size.height < f64::from(MIN_RESTORED.1) {
        return None;
    }
    Some((position, size))
}

/// The scale of the display a placement lands on, or the primary one.
///
/// Asked per placement rather than taken from the primary monitor, because a
/// second display with a different scale is exactly the case that makes the
/// conversion wrong in a way the user sees.
pub(crate) fn placement_scale_factor(handle: &AppHandle, placement: &WindowPlacement) -> f64 {
    handle
        .monitor_from_point(
            f64::from(placement.position.x),
            f64::from(placement.position.y),
        )
        .ok()
        .flatten()
        .or_else(|| handle.primary_monitor().ok().flatten())
        .map_or(1.0, |monitor| monitor.scale_factor())
}

/// Whether a saved placement still lands on a display that exists.
///
/// The failure this prevents is the classic one: quit with the window on a
/// second monitor, unplug it, launch, and the app restores to coordinates that
/// are now nowhere. The window is real, focused, and invisible, and the only
/// way back is deleting a config file the user does not know about.
///
/// Judged by the window's *title bar* rather than its whole frame, and by a
/// generous strip of it: a window may legitimately hang off the side of a
/// display, but if you cannot grab the top of it you cannot move it back.
pub(crate) fn placement_is_reachable(
    placement: &WindowPlacement,
    screens: &[(tauri::PhysicalPosition<i32>, tauri::PhysicalSize<u32>)],
) -> bool {
    if screens.is_empty() {
        return true;
    }
    // How much of the title bar has to be on a display to be worth calling
    // reachable. Any overlap at all is not enough -- three pixels of chrome
    // poking over the bottom edge is not something a person can grab, and
    // treating it as fine is how the window ends up effectively lost anyway.
    const GRABBABLE_HEIGHT: i32 = 24;
    const GRABBABLE_WIDTH: i32 = 80;

    let bar_top = placement.position.y;
    let bar_bottom = bar_top.saturating_add(TITLE_BAR_HEIGHT);
    let left = placement.position.x;
    let right = left.saturating_add(i32::try_from(placement.size.width).unwrap_or(i32::MAX));
    // Summed across displays, not tested one at a time: a window straddling two
    // monitors has a perfectly grabbable title bar even when neither display
    // holds enough of it on its own.
    screens.iter().any(|(origin, size)| {
        let monitor_right = origin
            .x
            .saturating_add(i32::try_from(size.width).unwrap_or(i32::MAX));
        let monitor_bottom = origin
            .y
            .saturating_add(i32::try_from(size.height).unwrap_or(i32::MAX));
        // Saturating, like the additions above. A saved `x` of `i32::MIN` --
        // and the config accepts any `i32` -- makes this subtraction overflow:
        // a debug build panics during launch, and a release build wraps to a
        // large positive number and calls an unreachable window visible.
        let visible_width = right.min(monitor_right).saturating_sub(left.max(origin.x));
        let visible_height = bar_bottom
            .min(monitor_bottom)
            .saturating_sub(bar_top.max(origin.y));
        visible_width >= GRABBABLE_WIDTH && visible_height >= GRABBABLE_HEIGHT
    })
}

/// Record where the window is, so the next launch opens it there.
///
/// Written on move and resize rather than only on quit, because the app is not
/// always quit: it is force-killed, it is replaced by an update, the machine
/// restarts. A geometry that only survives a graceful exit is one that is
/// usually lost. `write_config` is a read-modify-write of a small file and
/// these events arrive at most a few times a second while a drag is in
/// progress, which is well inside what this can absorb.
pub(crate) fn remember_placement(window: &tauri::WebviewWindow) {
    // A minimised or fullscreen window reports a placement that is about the
    // OS's temporary arrangement, not the one the user chose to come back to.
    if window.is_minimized().unwrap_or(false) || window.is_fullscreen().unwrap_or(false) {
        return;
    }
    let Some(placement) = placement_of(&window.as_ref().window()) else {
        return;
    };
    if placement.size.width < MIN_RESTORED.0 || placement.size.height < MIN_RESTORED.1 {
        return;
    }
    let _ = write_config(|config| {
        config["window"] = json!({
            "x": placement.position.x,
            "y": placement.position.y,
            "width": placement.size.width,
            "height": placement.size.height,
        });
    });
}
