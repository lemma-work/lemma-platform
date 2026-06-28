import * as React from 'react';
import { useCurrentUser } from 'lemma-sdk/react';
import { client } from '@/lib/client';
import { tokens } from '@/lib/tokens';
import { getOrgId } from '@/lib/org';

// ── App catalogue ─────────────────────────────────────────────────────────────
//
// SURFACE apps are pod-level — admin connects them once, all users share them
// (Slack bot posts to channels, Telegram bot sends messages).
//
// PERSONAL apps are per-user — each member connects their own account
// (emails send from their Gmail, calendar reads their schedule).
//
// ALWAYS_PERSONAL are always shown as slots even if the pod hasn't installed
// them yet. On first Connect we auto-install the auth config via enableApp
// (both have system_default_available: true) then open OAuth for that user.

const SURFACE_APP_IDS = new Set(['slack', 'telegram']);

const ALWAYS_PERSONAL: { appId: string; label: string; icon: string; hint: string }[] = [
  {
    appId: 'gmail',
    label: 'Gmail',
    icon:  '✉️',
    hint:  'Reminders and nudges send from your address. Import contacts from your inbox.',
  },
  {
    appId: 'googlecalendar',
    label: 'Google Calendar',
    icon:  '📅',
    hint:  'Your Schedule tab pulls from here. Mr. Toot writes new events back to your calendar.',
  },
];

const APP_META: Record<string, { label: string; icon: string }> = {
  slack:          { label: 'Slack',           icon: '💬' },
  telegram:       { label: 'Telegram',        icon: '✈️' },
  gmail:          { label: 'Gmail',           icon: '✉️' },
  googlecalendar: { label: 'Google Calendar', icon: '📅' },
  outlook:        { label: 'Outlook',         icon: '📧' },
};

type AccountStatus = 'loading' | 'connected' | 'disconnected' | 'connecting';

interface ServiceRow {
  appId:        string;
  authConfigId: string;
  label:        string;
  icon:         string;
  hint?:        string;
  accountId?:   string;
  status:       AccountStatus;
}

// ── Status chip ───────────────────────────────────────────────────────────────

function StatusChip({ status }: { status: AccountStatus }) {
  const map: Record<AccountStatus, { label: string; color: string; bg: string }> = {
    loading:      { label: 'Checking…',    color: tokens.muted, bg: 'var(--trumpet-chip-bg)' },
    connecting:   { label: 'Connecting…',  color: tokens.amber, bg: 'rgba(243,178,35,0.08)' },
    connected:    { label: 'Connected',    color: tokens.green, bg: 'rgba(87,184,106,0.12)' },
    disconnected: { label: 'Not connected', color: tokens.muted, bg: 'var(--trumpet-chip-bg)' },
  };
  const s = map[status];
  return (
    <span style={{
      fontSize:     13,
      fontWeight:   600,
      color:        s.color,
      background:   s.bg,
      padding:      '4px 12px',
      borderRadius: 99,
    }}>
      {s.label}
    </span>
  );
}

// ── Section ───────────────────────────────────────────────────────────────────

function Section({
  title,
  subtitle,
  rows,
  onConnect,
  onDisconnect,
  showConnectButton,
}: {
  title:             string;
  subtitle:          string;
  rows:              ServiceRow[];
  onConnect:         (row: ServiceRow) => void;
  onDisconnect:      (row: ServiceRow) => void;
  showConnectButton: boolean;
}) {
  if (rows.length === 0) return null;
  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 18, fontWeight: 700, color: tokens.fg }}>{title}</div>
        <div style={{ fontSize: 13, color: tokens.muted, marginTop: 3 }}>{subtitle}</div>
      </div>
      <div style={{
        display:       'flex',
        flexDirection: 'column',
        gap:           2,
        borderRadius:  18,
        border:        '1px solid var(--trumpet-divider)',
        overflow:      'hidden',
      }}>
        {rows.map((row, i) => (
          <div
            key={row.appId}
            style={{
              display:      'flex',
              alignItems:   'center',
              gap:          16,
              padding:      '20px 28px',
              background:   i % 2 === 0 ? 'var(--trumpet-surface)' : 'transparent',
              borderBottom: i < rows.length - 1 ? '1px solid var(--trumpet-edge-sm)' : 'none',
            }}
          >
            <span style={{
              fontSize:       22,
              width:          44,
              height:         44,
              display:        'flex',
              alignItems:     'center',
              justifyContent: 'center',
              background:     'var(--trumpet-chip-bg)',
              borderRadius:   12,
              flexShrink:     0,
              fontFamily:     "'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji',sans-serif",
            }}>
              {row.icon}
            </span>

            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 16, fontWeight: 700, color: tokens.fg }}>{row.label}</div>
              {row.hint && (
                <div style={{ fontSize: 12, color: tokens.muted, marginTop: 2 }}>{row.hint}</div>
              )}
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
              <StatusChip status={row.status} />

              {showConnectButton && row.status === 'disconnected' && (
                <button
                  onClick={() => onConnect(row)}
                  style={{
                    padding:      '8px 16px',
                    borderRadius: 10,
                    background:   'var(--trumpet-chip-bg)',
                    border:       '1px solid var(--trumpet-edge-strong)',
                    color:        tokens.fg,
                    fontSize:     13,
                    fontWeight:   700,
                    cursor:       'pointer',
                    fontFamily:   tokens.font,
                  }}
                >
                  Connect →
                </button>
              )}

              {showConnectButton && row.status === 'connected' && (
                <button
                  onClick={() => onDisconnect(row)}
                  style={{
                    padding:      '8px 16px',
                    borderRadius: 10,
                    background:   'transparent',
                    border:       '1px solid var(--trumpet-edge-sm)',
                    color:        tokens.muted,
                    fontSize:     13,
                    fontWeight:   600,
                    cursor:       'pointer',
                    fontFamily:   tokens.font,
                  }}
                >
                  Disconnect
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main ─────────────────────────────────────────────────────────────────────

export function ConnectedSubtab() {
  const { user } = useCurrentUser({ client, autoLoad: true });
  const [surfaceRows,  setSurfaceRows]  = React.useState<ServiceRow[]>([]);
  const [personalRows, setPersonalRows] = React.useState<ServiceRow[]>([]);
  const [isLoading, setIsLoading]       = React.useState(true);
  const pollRef = React.useRef<ReturnType<typeof setInterval> | null>(null);

  const loadAccounts = React.useCallback(async () => {
    try {
      const orgId = await getOrgId();
      const [authCfgResp, accountsResp] = await Promise.all([
        client.integrations.authConfigs.list(orgId),
        client.integrations.accounts.list(orgId),
      ]);

      const userAccounts = accountsResp.items.filter(a => a.user_id === user?.id);
      const anyAccounts  = accountsResp.items;

      const toRow = (
        appId: string,
        authConfigId: string,
        isSurface: boolean,
      ): ServiceRow => {
        const accounts = isSurface ? anyAccounts : userAccounts;
        const match = accounts.find(
          a => a.application_id === appId && a.status === 'CONNECTED',
        );
        const meta = APP_META[appId] ?? {
          label: appId.charAt(0).toUpperCase() + appId.slice(1),
          icon:  '🔗',
        };
        const hint = ALWAYS_PERSONAL.find(s => s.appId === appId)?.hint;
        return {
          appId,
          authConfigId,
          label:     meta.label,
          icon:      meta.icon,
          hint,
          accountId: match?.id,
          status:    match ? 'connected' : 'disconnected',
        };
      };

      const surface:  ServiceRow[] = [];
      const personal: ServiceRow[] = [];
      const installedAppIds = new Set(authCfgResp.items.map(c => c.application_id));

      for (const cfg of authCfgResp.items) {
        if (SURFACE_APP_IDS.has(cfg.application_id)) {
          surface.push(toRow(cfg.application_id, cfg.id, true));
        } else {
          personal.push(toRow(cfg.application_id, cfg.id, false));
        }
      }

      // Always show gmail + google_calendar personal slots, even if not yet installed
      for (const slot of ALWAYS_PERSONAL) {
        if (!installedAppIds.has(slot.appId)) {
          personal.push({
            appId:        slot.appId,
            authConfigId: '',         // not installed yet — enableApp on first connect
            label:        slot.label,
            icon:         slot.icon,
            hint:         slot.hint,
            accountId:    undefined,
            status:       'disconnected',
          });
        }
      }

      setSurfaceRows(surface);
      setPersonalRows(personal);
    } finally {
      setIsLoading(false);
    }
  }, [user?.id]);

  React.useEffect(() => {
    if (user?.id) void loadAccounts();
  }, [user?.id, loadAccounts]);

  React.useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const handleConnect = React.useCallback(async (row: ServiceRow) => {
    const setRows = SURFACE_APP_IDS.has(row.appId) ? setSurfaceRows : setPersonalRows;
    setRows(prev => prev.map(r => r.appId === row.appId ? { ...r, status: 'connecting' } : r));
    try {
      const orgId = await getOrgId();

      // If auth config not installed yet, install it now using system defaults
      if (!row.authConfigId) {
        await client.integrations.enableApp(orgId, row.appId);
      }

      const req = await client.integrations.createConnectRequest(orgId, row.appId);
      if (req.authorization_url) {
        window.open(req.authorization_url, '_blank');
        // Poll until the user's account appears (max 60s)
        let elapsed = 0;
        pollRef.current = setInterval(async () => {
          elapsed += 3000;
          if (elapsed >= 60000) {
            clearInterval(pollRef.current!);
            setRows(prev => prev.map(r => r.appId === row.appId ? { ...r, status: 'disconnected' } : r));
            return;
          }
          const accounts = await client.integrations.accounts.list(orgId);
          const match = accounts.items.find(
            a => a.user_id === user?.id && a.application_id === row.appId && a.status === 'CONNECTED',
          );
          if (match) {
            clearInterval(pollRef.current!);
            setRows(prev => prev.map(r =>
              r.appId === row.appId ? { ...r, status: 'connected', accountId: match.id } : r,
            ));
          }
        }, 3000);
      } else {
        setRows(prev => prev.map(r => r.appId === row.appId ? { ...r, status: 'disconnected' } : r));
      }
    } catch {
      const setR = SURFACE_APP_IDS.has(row.appId) ? setSurfaceRows : setPersonalRows;
      setR(prev => prev.map(r => r.appId === row.appId ? { ...r, status: 'disconnected' } : r));
    }
  }, [user?.id]);

  const handleDisconnect = React.useCallback(async (row: ServiceRow) => {
    if (!row.accountId) return;
    const setRows = SURFACE_APP_IDS.has(row.appId) ? setSurfaceRows : setPersonalRows;
    const orgId = await getOrgId();
    await client.integrations.accounts.delete(orgId, row.accountId);
    setRows(prev => prev.map(r =>
      r.appId === row.appId ? { ...r, status: 'disconnected', accountId: undefined } : r,
    ));
  }, []);

  return (
    <div style={{
      padding:        '52px 80px',
      fontFamily:     tokens.font,
      display:        'flex',
      flexDirection:  'column',
      gap:            40,
      overflowY:      'auto',
      height:         '100%',
      scrollbarWidth: 'none',
    }}>
      {/* Header */}
      <div>
        <div style={{
          fontSize:      36,
          fontWeight:    800,
          letterSpacing: -1,
          color:         tokens.fg,
          lineHeight:    1,
        }}>
          Connected
        </div>
        <div style={{ fontSize: 14, color: tokens.muted, marginTop: 6 }}>
          Pod integrations are shared by everyone. Personal integrations are yours alone.
        </div>
      </div>

      {isLoading ? (
        <div style={{ color: tokens.muted, fontSize: 14 }}>Loading…</div>
      ) : (
        <>
          {/* Surface / pod-level */}
          <Section
            title="Pod integrations"
            subtitle="Set up by your admin — shared across the pod. Slack messages and Telegram notifications come from here."
            rows={surfaceRows}
            onConnect={handleConnect}
            onDisconnect={handleDisconnect}
            showConnectButton={false}
          />

          {/* Personal / per-user */}
          <Section
            title="Your integrations"
            subtitle="Connect your own accounts. Reminder emails send from your Gmail, calendar reads your events."
            rows={personalRows}
            onConnect={handleConnect}
            onDisconnect={handleDisconnect}
            showConnectButton={true}
          />

          {personalRows.length === 0 && surfaceRows.length === 0 && (
            <div style={{ color: tokens.muted, fontSize: 14 }}>
              No integrations installed in this pod yet.
            </div>
          )}
        </>
      )}

      <button
        onClick={() => { setIsLoading(true); void loadAccounts(); }}
        style={{
          alignSelf:    'flex-start',
          padding:      '9px 18px',
          borderRadius: 10,
          background:   'var(--trumpet-surface)',
          border:       '1px solid var(--trumpet-edge)',
          color:        tokens.muted,
          fontSize:     13,
          fontWeight:   600,
          cursor:       'pointer',
          fontFamily:   tokens.font,
        }}
      >
        Refresh
      </button>
    </div>
  );
}
