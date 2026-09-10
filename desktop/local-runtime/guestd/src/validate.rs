//! Refusing a request before it reaches the engine.

use super::*;

pub(crate) fn validate_metadata(metadata: &BTreeMap<String, String>) -> Result<(), GuestError> {
    if metadata.len() > 32 {
        return Err(GuestError::invalid(
            "sandbox metadata cannot contain more than 32 entries",
        ));
    }
    for (name, value) in metadata {
        if !valid_identifier(name) || name.len() > 128 || value.len() > 4096 || value.contains('\0')
        {
            return Err(GuestError::invalid("sandbox metadata is invalid"));
        }
    }
    Ok(())
}

pub(crate) fn validate_apps(apps: &[AppSpec]) -> Result<(), GuestError> {
    if apps.is_empty() || apps.len() > 16 {
        return Err(GuestError::invalid(
            "apps must contain between 1 and 16 entries",
        ));
    }
    for app in apps {
        if !valid_app_name(&app.name)
            || !valid_identifier(&app.public_slug)
            || app.port == 0
            || !app.health_path.starts_with('/')
            || !matches!(app.startup.as_str(), "eager" | "lazy")
            || !matches!(app.exposure.as_str(), "private" | "workspace_user")
            || !matches!(
                app.auth_mode.as_str(),
                "manager_api_key" | "workspace_access_token"
            )
        {
            return Err(GuestError::invalid(format!(
                "invalid app specification {}",
                app.name
            )));
        }
    }
    Ok(())
}

pub(crate) fn validate_environment(
    environment: &BTreeMap<String, String>,
) -> Result<(), GuestError> {
    for (name, value) in environment {
        if name.is_empty()
            || !name
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
            || name.as_bytes()[0].is_ascii_digit()
            || value.contains(['\n', '\r', '\0'])
        {
            return Err(GuestError::invalid(format!(
                "invalid environment entry {name:?}"
            )));
        }
    }
    Ok(())
}

pub(crate) fn validate_secret(name: &str, value: &str) -> Result<(), GuestError> {
    if !(16..=512).contains(&value.len())
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
    {
        return Err(GuestError::invalid(format!(
            "{name} must contain 16 to 512 ASCII letters, digits, '-' or '_'"
        )));
    }
    Ok(())
}

pub(crate) fn validate_resources(resources: &ResourceSpec) -> Result<u64, GuestError> {
    let memory = resources
        .memory
        .as_deref()
        .filter(|value| !value.trim().is_empty())
        .map(parse_memory_bytes)
        .transpose()?
        .unwrap_or(DEFAULT_SANDBOX_MEMORY_BYTES);
    if !(256 * 1024 * 1024..=6 * 1024 * 1024 * 1024).contains(&memory) {
        return Err(GuestError::invalid(
            "sandbox memory must be between 256 MiB and 6 GiB",
        ));
    }
    if let Some(cpus) = resources
        .cpus
        .as_deref()
        .filter(|value| !value.trim().is_empty())
    {
        let cpus = cpus
            .parse::<f64>()
            .ok()
            .filter(|value| value.is_finite() && (0.1..=16.0).contains(value))
            .ok_or_else(|| {
                GuestError::invalid("sandbox cpus must be a number between 0.1 and 16")
            })?;
        let _ = cpus;
    }
    Ok(memory)
}

pub(crate) fn parse_memory_bytes(value: &str) -> Result<u64, GuestError> {
    let value = value.trim().to_ascii_lowercase();
    let (digits, multiplier) = [
        ("gib", 1024_u64.pow(3)),
        ("gb", 1000_u64.pow(3)),
        ("gi", 1024_u64.pow(3)),
        ("g", 1024_u64.pow(3)),
        ("mib", 1024_u64.pow(2)),
        ("mb", 1000_u64.pow(2)),
        ("mi", 1024_u64.pow(2)),
        ("m", 1024_u64.pow(2)),
        ("kib", 1024_u64),
        ("kb", 1000_u64),
        ("ki", 1024_u64),
        ("k", 1024_u64),
        ("b", 1_u64),
    ]
    .into_iter()
    .find_map(|(suffix, multiplier)| {
        value
            .strip_suffix(suffix)
            .map(|digits| (digits, multiplier))
    })
    .unwrap_or((&value, 1));
    let number = digits
        .parse::<u64>()
        .map_err(|_| GuestError::invalid("sandbox memory has an invalid size"))?;
    number
        .checked_mul(multiplier)
        .ok_or_else(|| GuestError::invalid("sandbox memory size overflow"))
}

pub(crate) fn validate_sandbox_id(value: &str) -> Result<(), GuestError> {
    if value.is_empty()
        || value.len() > 63
        || !value.as_bytes()[0].is_ascii_lowercase()
        || !value.as_bytes()[value.len() - 1].is_ascii_alphanumeric()
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
    {
        return Err(GuestError::invalid("invalid sandbox_id"));
    }
    Ok(())
}

pub(crate) fn valid_app_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 63
        && value.bytes().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-' || byte == b'_'
        })
}

pub(crate) fn valid_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 63
        && value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
}

pub(crate) fn required_string(value: &Value, key: &str) -> Result<String, GuestError> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .ok_or_else(|| GuestError::invalid(format!("missing {key}")))
}

pub(crate) fn callback_probe_url(base: &str, health_path: &str) -> Result<String, GuestError> {
    if !(base.starts_with("http://") || base.starts_with("https://")) {
        return Err(GuestError::invalid("LEMMA_BASE_URL must use HTTP(S)"));
    }
    let path = if health_path.starts_with('/') {
        health_path.to_owned()
    } else {
        format!("/{health_path}")
    };
    Ok(format!("{}{path}", base.trim_end_matches('/')))
}
