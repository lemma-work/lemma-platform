//! Refusing a runtime spec this host cannot honour.

use super::*;

#[cfg(not(windows))]
pub(crate) fn wsl_distribution_for(_root: &Path) -> String {
    DEFAULT_WSL_DISTRIBUTION.to_string()
}

pub(crate) fn runtime_path_value(path: &Path) -> io::Result<String> {
    path.to_str().map(str::to_owned).ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("managed runtime path is not Unicode: {}", path.display()),
        )
    })
}

pub(crate) fn validate_spec(spec: &ManagedRuntimeSpec) -> io::Result<()> {
    for (name, image) in [
        ("postgres", &spec.images.postgres),
        ("redis", &spec.images.redis),
        ("supertokens", &spec.images.supertokens),
    ] {
        if !image.contains("@sha256:") || image.bytes().any(|byte| byte.is_ascii_whitespace()) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("managed {name} image must be digest-pinned"),
            ));
        }
    }
    validate_secret("postgres_password", &spec.credentials.postgres_password)?;
    validate_secret("redis_password", &spec.credentials.redis_password)?;
    let ports = [
        spec.ports.postgres,
        spec.ports.redis,
        spec.ports.supertokens,
        spec.ports.backend,
        spec.ports.frontend,
    ];
    if ports.contains(&0) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "managed runtime ports must be non-zero",
        ));
    }
    Ok(())
}

pub(crate) fn private_ipv4(value: &str, label: &str) -> io::Result<Ipv4Addr> {
    let address = value.parse::<IpAddr>().map_err(|_| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("{label} must be a literal IPv4 address"),
        )
    })?;
    match address {
        IpAddr::V4(address)
            if !address.is_unspecified()
                && !address.is_loopback()
                && !address.is_multicast()
                && (address.is_private() || address.is_link_local()) =>
        {
            Ok(address)
        }
        _ => Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!("{label} must be a private non-loopback IPv4 address"),
        )),
    }
}

#[cfg(any(target_os = "macos", windows))]
pub(crate) fn bundled_executable(variable: &str, sibling_name: &str) -> io::Result<PathBuf> {
    let path = match env::var_os(variable).filter(|value| !value.is_empty()) {
        Some(path) => PathBuf::from(path),
        None => env::current_exe()?
            .parent()
            .ok_or_else(|| io::Error::other("locald executable has no parent"))?
            .join(sibling_name),
    };
    if !path.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            format!(
                "bundled managed runtime executable is missing: {}",
                path.display()
            ),
        ));
    }
    Ok(path)
}
