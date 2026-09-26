//! Asking the guest to give disk back. See `crate::disk_hygiene`.

use super::*;

impl ManagedRuntimeController {
    /// Remove the images no container uses and this release does not name.
    ///
    /// The guest decides what is in use -- it can see every container,
    /// stopped ones included -- and is told only what the running release
    /// pins, which it never removes.
    pub fn prune_unused_images(&self) -> io::Result<serde_json::Value> {
        self.runtime.request_cancellable(
            "core.prune_images",
            json!({ "images": self.spec.images }),
            self.cancellation.clone(),
        )
    }

    /// Hand the data disk's freed blocks back to macOS.
    ///
    /// macOS only: there the data disk is `data.raw`, a sparse file that
    /// shrinks when the guest discards. WSL keeps its data elsewhere.
    pub fn trim_data_disk(&self) -> io::Result<serde_json::Value> {
        if !cfg!(target_os = "macos") {
            return Ok(json!({ "supported": false }));
        }
        self.runtime
            .request_cancellable("core.trim", json!({}), self.cancellation.clone())
    }
}
