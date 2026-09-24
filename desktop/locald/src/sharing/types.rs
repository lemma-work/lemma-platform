//! What a caller asks for, and what it is told back.

use super::*;

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TunnelProvider {
    Ngrok,
    Cloudflare,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CloudflareSetup {
    #[default]
    Automatic,
    Existing,
}

/// Who may create an account once this installation is reachable from
/// somewhere other than this Mac.
///
/// A preference rather than part of a mode, because it is a decision about
/// people and a mode is a decision about networks: somebody sharing on the LAN
/// with their family and somebody sharing publicly with one colleague want the
/// same answer here. Invite-only by default -- the installation owner is always
/// admitted, and anyone they invite from inside Lemma can join -- because the
/// failure the other default produces is a stranger with an account and a
/// sandbox on the owner's computer, discovered after the fact.
#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum WhoCanJoin {
    #[default]
    InviteOnly,
    Open,
}

impl WhoCanJoin {
    /// The backend's `SIGNUP_MODE` for this choice.
    pub fn signup_mode(self) -> &'static str {
        match self {
            Self::InviteOnly => "invite_only",
            Self::Open => "open",
        }
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct SharingPreferences {
    pub schema_version: u64,
    pub last_provider: Option<TunnelProvider>,
    pub selected_interface: Option<String>,
    pub cloudflare_setup: CloudflareSetup,
    pub cloudflare_tunnel_id: Option<String>,
    pub cloudflare_tunnel_name: Option<String>,
    pub cloudflare_hostname: Option<String>,
    pub cloudflare_tunnel_owned: bool,
    pub cloudflare_dns_routed: bool,
    /// Absent from a `sharing.json` written before it existed, which reads as
    /// the default: an upgraded installation starts invite-only rather than
    /// keeping an openness nobody ever chose.
    pub who_can_join: WhoCanJoin,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct EnableSharingRequest {
    pub mode: SharingMode,
    pub interface: Option<String>,
    pub provider: Option<TunnelProvider>,
    pub cloudflare_setup: CloudflareSetup,
    pub cloudflare_tunnel_id: Option<String>,
    pub cloudflare_tunnel_name: Option<String>,
    pub hostname: Option<String>,
    pub public_warning_confirmed: bool,
    /// Changes the saved preference when present; absent keeps it.
    pub who_can_join: Option<WhoCanJoin>,
}

impl Default for EnableSharingRequest {
    fn default() -> Self {
        Self {
            mode: SharingMode::ThisComputer,
            interface: None,
            provider: None,
            cloudflare_setup: CloudflareSetup::Automatic,
            cloudflare_tunnel_id: None,
            cloudflare_tunnel_name: None,
            hostname: None,
            public_warning_confirmed: false,
            who_can_join: None,
        }
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct NetworkInterface {
    pub name: String,
    pub address: String,
    pub label: String,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct ProviderReadiness {
    pub installed: bool,
    pub authenticated: bool,
    pub executable: Option<String>,
    pub version: Option<String>,
    pub message: Option<String>,
    pub instructions: Vec<String>,
    pub tunnels: Vec<CloudflareTunnel>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct CloudflareTunnel {
    pub id: String,
    pub name: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct SharingSnapshot {
    pub mode: SharingMode,
    pub phase: String,
    pub progress: u64,
    pub canonical_url: String,
    pub provider: Option<TunnelProvider>,
    pub provider_readiness: HashMap<String, ProviderReadiness>,
    pub tunnel_status: String,
    pub warnings: Vec<String>,
    pub last_error: Option<String>,
    pub started_at_ms: Option<u128>,
    pub interfaces: Vec<NetworkInterface>,
    pub selected_interface: Option<String>,
    pub qr_svg: Option<String>,
    pub preferences: SharingPreferences,
    pub transition_running: bool,
    pub who_can_join: WhoCanJoin,
    pub public_confirmation: String,
    pub apps_limitation: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SetWhoCanJoinRequest {
    pub who_can_join: WhoCanJoin,
}

pub(crate) fn render_qr(value: &str) -> Option<String> {
    QrCode::new(value.as_bytes()).ok().map(|code| {
        code.render::<svg::Color>()
            .min_dimensions(176, 176)
            .dark_color(svg::Color("#1f1d18"))
            .light_color(svg::Color("#ffffff"))
            .build()
    })
}

pub(crate) fn provider_name(provider: TunnelProvider) -> &'static str {
    match provider {
        TunnelProvider::Ngrok => "ngrok",
        TunnelProvider::Cloudflare => "cloudflared",
    }
}
