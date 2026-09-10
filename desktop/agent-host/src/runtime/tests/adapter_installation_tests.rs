use super::{HostConfig, HostPaths, HostRuntime};
use std::time::{Duration, Instant};

#[test]
fn background_cache_failure_invalidates_the_serving_hosts_discovery() {
    let directory = tempfile::tempdir().unwrap();
    let paths = HostPaths::under(directory.path());
    let config = HostConfig::load_or_create(&paths).unwrap();
    // An old installation may leave a file where the cache directory belongs.
    // This fails before npm is launched and must remain available for repair.
    std::fs::write(&paths.adapters, b"existing installation").unwrap();
    let runtime = HostRuntime::new(config, paths.clone()).unwrap();
    let before = runtime.manifest.installed_fingerprint();
    let warmup = runtime.start_adapter_installation().unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    while runtime.manifest.installed_fingerprint() == before && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    drop(warmup);
    assert_ne!(runtime.manifest.installed_fingerprint(), before);
    assert_eq!(
        std::fs::read(&paths.adapters).unwrap(),
        b"existing installation"
    );
}
