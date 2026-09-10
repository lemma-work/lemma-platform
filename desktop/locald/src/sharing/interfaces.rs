//! Which address on this machine a private share is offered on.

use super::*;

pub(crate) fn private_ipv4_interfaces() -> Vec<NetworkInterface> {
    let mut interfaces: Vec<_> = if_addrs::get_if_addrs()
        .unwrap_or_default()
        .into_iter()
        .filter_map(|interface| match interface.ip() {
            IpAddr::V4(address)
                if address.is_private() && !address.is_loopback() && !address.is_link_local() =>
            {
                Some(NetworkInterface {
                    label: format!("{} · {}", interface.name, address),
                    name: interface.name,
                    address: address.to_string(),
                })
            }
            _ => None,
        })
        .collect();
    interfaces.sort_by(|left, right| {
        left.name
            .cmp(&right.name)
            .then(left.address.cmp(&right.address))
    });
    interfaces.dedup_by(|left, right| left.name == right.name && left.address == right.address);
    interfaces
}

pub(crate) fn resolve_private_interface(selection: &str) -> io::Result<NetworkInterface> {
    private_ipv4_interfaces()
        .into_iter()
        .find(|interface| interface.name == selection || interface.address == selection)
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::AddrNotAvailable,
                "the selected private IPv4 interface is no longer available",
            )
        })
}

pub(crate) fn normalize_hostname(value: &str) -> io::Result<String> {
    let value = value.trim().trim_end_matches('.');
    if value.is_empty()
        || value.contains('/')
        || value.contains(':')
        || value.chars().any(char::is_whitespace)
        || !value.contains('.')
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "enter a public hostname such as lemma.example.com",
        ));
    }
    Ok(value.to_ascii_lowercase())
}

pub(crate) fn display_ip(ip: IpAddr) -> String {
    match ip {
        IpAddr::V4(ip) => ip.to_string(),
        IpAddr::V6(ip) => format!("[{ip}]"),
    }
}
