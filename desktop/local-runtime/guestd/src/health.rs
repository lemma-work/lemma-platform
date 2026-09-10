//! Whether this guest is well enough to be asked for anything.

use super::*;
use crate::capacity::data_disk_space;

impl<E: Engine + 'static> GuestService<E> {
    pub(crate) fn health(&self) -> Result<Value, GuestError> {
        self.health_at(SystemTime::now())
    }

    pub(crate) fn health_at(&self, now: SystemTime) -> Result<Value, GuestError> {
        self.check_kernel_health()?;
        let marker = self.cache_reset_marker();
        let repair_due = marker
            .metadata()
            .and_then(|metadata| metadata.modified())
            .ok()
            .and_then(|modified| now.duration_since(modified).ok())
            .is_some_and(|age| age >= CACHE_REPAIR_RESPONSE_GRACE);
        if repair_due {
            return Err(GuestError {
                code: "guest_cache_repair_required".into(),
                message: "container cache repair required".into(),
                retryable: true,
                status_code: 503,
            });
        }
        // Core guest readiness includes a usable container engine. Do not
        // report the VM healthy with a fabricated zero count when nerdctl
        // cannot access its writable state; doing so only defers an appliance
        // layout failure until the first image pull.
        let active_sandboxes = self.running_sandbox_count()?;
        let endpoint_host = self.current_endpoint_host();
        Ok(json!({
            "status": "ready", "engine": "containerd",
            // Null when the guest holds no DHCP lease. The guest is still
            // serving -- core services reach the host over the private socket
            // bridges -- but nothing can reach a sandbox, and the host needs
            // to be able to tell that apart from a guest that is simply gone.
            "endpoint_host": endpoint_host,
            "network": {
                "leased": endpoint_host.is_some(),
            },
            "host_gateway": self.host_gateway,
            "active_sandboxes": active_sandboxes,
            // What is left of the disk everything in the guest shares. Absent
            // on a guest too old to report it, and absent rather than guessed
            // when the filesystem cannot be measured.
            "data_disk": data_disk_space(&self.state_root).map(|(free, total)| json!({
                "free_bytes": free,
                "total_bytes": total,
            })),
            // Reported on every health call so a drifting guest clock is
            // visible to whoever is already asking whether the guest is well,
            // rather than only to whoever thinks to ask about time.
            "clock_epoch": now
                .duration_since(UNIX_EPOCH)
                .map(|since| since.as_secs())
                .unwrap_or_default(),
        }))
    }

    pub(crate) fn check_kernel_health(&self) -> Result<(), GuestError> {
        let Some(path) = &self.kernel_taint_path else {
            return Ok(());
        };
        let taint = fs::read_to_string(path)
            .ok()
            .and_then(|text| text.trim().parse::<u64>().ok())
            .ok_or_else(|| GuestError::engine("Could not read Linux guest kernel health"))?;
        // Linux taint bits: machine check, bad page, and kernel Oops/DIE.
        // An unsigned module or a warning alone is not evidence of this failure.
        if taint & ((1 << 4) | (1 << 5) | (1 << 7)) != 0 {
            return Err(GuestError {
                code: "guest_kernel_failed".into(),
                message: "Lemma's Linux guest kernel crashed. Quit and reopen Lemma to restart the local runtime. Your stored data has not been reset. If this repeats, repair the runtime from Updates and recovery.".into(),
                retryable: false,
                status_code: 503,
            });
        }
        Ok(())
    }
}
