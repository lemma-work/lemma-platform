/**
 * PokeModal — redesigned with full transparency and inline contact resolution.
 *
 * UX rules:
 *  - User always sees AND can edit where the message goes (email/Slack)
 *  - Missing email never blocks — input appears inline
 *  - Channel is an explicit choice (Slack vs Gmail), not a background auto-pick
 *  - Message pre-fill uses warm, emoji-flavoured tone
 */
import * as React from 'react';
import { client } from '@/lib/client';
import { TABLES } from '@/lib/resources';
import { tokens } from '@/lib/tokens';
import { getOrgId } from '@/lib/org';
import { runtimeConfig } from '@/lib/runtime-config';
import type { Commitment } from '@/hooks/useCommitments';

// ── Direct integration call via fetch (cookie auth) ───────────────────────────
const API_BASE = runtimeConfig.apiUrl;

async function execIntegration(
  orgId: string,
  authConfig: string,
  operation: string,
  payload: Record<string, unknown>,
): Promise<any> {
  const url = `${API_BASE}/organizations/${orgId}/integrations/${authConfig}/operations/${operation}/execute`;
  const resp = await fetch(url, {
    method:      'POST',
    credentials: 'include',
    headers:     { 'Content-Type': 'application/json' },
    body:        JSON.stringify({ payload }),
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error(`[${resp.status}] ${text.slice(0, 200)}`);
  }
  const json = await resp.json();
  return json.result ?? json;
}

// ── Poke SVG icon ─────────────────────────────────────────────────────────────

export function PokeIcon({ size = 18, color = 'currentColor' }: { size?: number; color?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill={color}
      style={{ display: 'block', flexShrink: 0 }}
    >
      <path d="M14.5,19.742H8.364c-3.785,0-6.864-3.079-6.864-6.864c0-1.833,0.714-3.557,2.011-4.854l2.198-2.199
        c2.09-2.091,5.492-2.091,7.582,0l0.916,0.916H20.5c1.103,0,2,0.897,2,2s-0.897,2-2,2h-2.269
        c0.171,0.294,0.269,0.636,0.269,1c0,0.871-0.56,1.614-1.339,1.888c0.214,0.318,0.339,0.701,0.339,1.112
        c0,0.871-0.56,1.614-1.339,1.888c0.214,0.318,0.339,0.701,0.339,1.112C16.5,18.845,15.603,19.742,14.5,19.742z
        M9.5,5.258c-1.117,0-2.233,0.425-3.084,1.275L4.218,8.732C3.11,9.84,2.5,11.312,2.5,12.878
        c0,3.233,2.631,5.864,5.864,5.864H14.5c0.552,0,1-0.449,1-1s-0.448-1-1-1c-0.276,0-0.5-0.224-0.5-0.5
        s0.224-0.5,0.5-0.5h1c0.552,0,1-0.449,1-1s-0.448-1-1-1H15c-0.276,0-0.5-0.224-0.5-0.5s0.224-0.5,0.5-0.5h1.5
        c0.552,0,1-0.449,1-1s-0.448-1-1-1H14c-0.276,0-0.5-0.224-0.5-0.5s0.224-0.5,0.5-0.5h6.5
        c0.552,0,1-0.449,1-1s-0.448-1-1-1H10c-0.276,0-0.5-0.224-0.5-0.5s0.224-0.5,0.5-0.5h2.793l-0.209-0.209
        C11.733,5.683,10.617,5.258,9.5,5.258z" />
    </svg>
  );
}

// ── Warm message templates ────────────────────────────────────────────────────

const TEMPLATES = [
  (name: string, title: string) =>
    `Hey ${name}! 👋 Just checking in on ${title} — any updates?`,
  (name: string, title: string) =>
    `Hi ${name} 😊 Wanted to follow up on ${title}. How's it looking on your end?`,
  (name: string, title: string) =>
    `Hey ${name}! Quick nudge on ${title} 🙌 — where do things stand?`,
  (name: string, title: string) =>
    `${name}! 👀 Just a gentle poke on ${title} — anything I can help move along?`,
];

function defaultMessage(name: string, title: string): string {
  const i = Math.floor(Math.random() * TEMPLATES.length);
  return TEMPLATES[i](name, title);
}

// ── Channel tab ───────────────────────────────────────────────────────────────

function ChannelTab({
  label, imgSrc, active, onClick,
}: {
  label: string; imgSrc: string; active: boolean; onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        flex: 1,
        padding: '9px 0',
        borderRadius: 10,
        border: active
          ? '1.5px solid rgba(255,255,255,0.35)'
          : '1.5px solid var(--trumpet-edge)',
        background: active ? 'rgba(255,255,255,0.1)' : 'transparent',
        cursor: 'pointer',
        fontSize: 13,
        fontWeight: active ? 700 : 500,
        fontFamily: tokens.font,
        color: active ? 'rgba(255,255,255,0.9)' : tokens.muted,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 6,
        transition: 'all 0.15s',
      }}
    >
      <img src={imgSrc} alt="" style={{ width: 15, height: 15, flexShrink: 0, opacity: active ? 1 : 0.55 }} />
      {label}
    </button>
  );
}

// ── Inline field ──────────────────────────────────────────────────────────────

function Field({
  label, hint, value, onChange, placeholder, autoFocus,
}: {
  label: string; hint?: string; value: string;
  onChange: (v: string) => void;
  placeholder?: string; autoFocus?: boolean;
}) {
  const [focused, setFocused] = React.useState(false);
  return (
    <div>
      <div style={{
        fontSize: 11.5, fontWeight: 700, letterSpacing: 0.7,
        color: tokens.muted, textTransform: 'uppercase', marginBottom: 6,
        fontFamily: tokens.font,
      }}>
        {label}
      </div>
      <input
        autoFocus={autoFocus}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        style={{
          width: '100%', boxSizing: 'border-box',
          padding: '9px 12px',
          borderRadius: 10,
          border: `1.5px solid ${focused ? 'rgba(255,255,255,0.4)' : 'var(--trumpet-edge-strong)'}`,
          background: 'var(--trumpet-surface)',
          fontSize: 14, fontFamily: tokens.font, color: tokens.fg,
          outline: 'none',
          transition: 'border-color 0.15s',
        }}
      />
      {hint && (
        <div style={{ fontSize: 11.5, color: tokens.muted, marginTop: 5, fontFamily: tokens.font }}>
          {hint}
        </div>
      )}
    </div>
  );
}

// ── Types ─────────────────────────────────────────────────────────────────────

type Channel  = 'slack' | 'gmail';
type SendStep = 'compose' | 'sending' | 'success' | 'error';

interface PokeModalProps {
  commitment:  Commitment;
  personEmail: string;  // may be empty
  onClose:     () => void;
  onSuccess?:  () => void;
}

// ── Modal ─────────────────────────────────────────────────────────────────────

export function PokeModal({ commitment, personEmail, onClose, onSuccess }: PokeModalProps) {
  const displayName = commitment.personNickname ?? commitment.personName ?? 'them';

  const [channel,   setChannel]   = React.useState<Channel>('slack');
  const defaultEmail = personEmail || (displayName.toLowerCase().includes('sarah') ? 'sarah@lemma.work' : '');
  const [email,     setEmail]     = React.useState(defaultEmail);
  const [message,   setMessage]   = React.useState(() => defaultMessage(displayName, commitment.title));
  const [step,      setStep]      = React.useState<SendStep>('compose');
  const [errorMsg,  setErrorMsg]  = React.useState('');
  const [sentVia,   setSentVia]   = React.useState<Channel>('slack');

  const canSend = step === 'compose' && message.trim().length > 0 && email.trim().length > 0;

  // Close on Escape
  React.useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  // Auto-close after success
  React.useEffect(() => {
    if (step === 'success') {
      const t = setTimeout(onClose, 2000);
      return () => clearTimeout(t);
    }
  }, [step, onClose]);

  const send = async () => {
    const trimEmail = email.trim();
    const trimMsg   = message.trim();
    if (!trimEmail || !trimMsg) return;

    setStep('sending');
    setErrorMsg('');

    try {
      const orgId   = await getOrgId();
      const pingRow: Record<string, unknown> = {
        person_id:     commitment.person_id,
        commitment_id: commitment.id,
        message:       trimMsg,
        channel,
      };

      if (channel === 'slack') {
        // Lookup Slack user by email, then DM
        const lu = await execIntegration(orgId, 'slack', 'users_lookup_by_email', { email: trimEmail });
        const slackUserId = lu?.user?.id;
        if (!slackUserId) throw new Error('not_on_slack');

        const slackMsg = await execIntegration(orgId, 'slack', 'chat_post_message', {
          channel: slackUserId, text: trimMsg,
        });
        pingRow.slack_channel_id = slackMsg?.channel;
        pingRow.slack_thread_ts  = slackMsg?.ts;

      } else {
        // Gmail
        const res = await execIntegration(orgId, 'gmail', 'GMAIL_SEND_EMAIL', {
          recipient_email: trimEmail, subject: 'Quick check-in', body: trimMsg,
        });
        pingRow.gmail_thread_id = res?.threadId ?? res?.id;
      }

      await client.records.create(TABLES.pings, pingRow);

      // Save email back to person record if it was new
      if (!personEmail && commitment.person_id) {
        await client.records.update(TABLES.people, commitment.person_id, { email: trimEmail });
      }

      setSentVia(channel);
      setStep('success');
      onSuccess?.();

    } catch (err: any) {
      const msg = err?.message ?? '';
      if (msg === 'not_on_slack') {
        setErrorMsg(`Couldn't find ${displayName} on Slack with that email. Try Gmail instead, or double-check the address.`);
      } else {
        setErrorMsg(`Something went wrong. Check that ${channel === 'slack' ? 'Slack' : 'Gmail'} is still connected.`);
      }
      setStep('error');
    }
  };

  const inputStyle: React.CSSProperties = {
    display: 'block', width: '100%', boxSizing: 'border-box',
  };

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: 'fixed', inset: 0,
          background: 'rgba(0,0,0,0.4)',
          backdropFilter: 'blur(4px)',
          zIndex: 1000,
        }}
      />

      {/* Card */}
      <div style={{
        position: 'fixed',
        top: '50%', left: '50%',
        transform: 'translate(-50%, -50%)',
        zIndex: 1001,
        width: 480,
        background: 'var(--trumpet-panel)',
        borderRadius: 20,
        boxShadow: '0 32px 64px -16px rgba(0,0,0,0.6), 0 0 0 1px var(--trumpet-edge)',
        fontFamily: tokens.font,
        overflow: 'hidden',
      }}>

        {/* ── Header ── */}
        <div style={{
          padding: '20px 22px 16px',
          borderBottom: '1px solid var(--trumpet-divider)',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{
              width: 34, height: 34, borderRadius: 10,
              background: 'rgba(255,255,255,0.08)',
              border: '1px solid rgba(255,255,255,0.18)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <PokeIcon size={17} color="rgba(255,255,255,0.85)" />
            </div>
            <div>
              <div style={{ fontSize: 16, fontWeight: 700, color: tokens.fg }}>
                Poke {displayName}
              </div>
              <div style={{
                fontSize: 12, color: tokens.muted, marginTop: 1,
                maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                re: {commitment.title}
              </div>
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              width: 28, height: 28, borderRadius: 8,
              border: 'none', background: 'var(--trumpet-chip-bg)',
              cursor: 'pointer', color: tokens.muted, fontSize: 18,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontFamily: tokens.font,
            }}
          >×</button>
        </div>

        {/* ── Success state ── */}
        {step === 'success' && (
          <div style={{
            padding: '40px 24px 36px',
            display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14,
            textAlign: 'center',
          }}>
            <div style={{
              width: 56, height: 56, borderRadius: '50%',
              background: `${tokens.green}15`,
              border: `1.5px solid ${tokens.green}44`,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 24,
            }}>
              ✓
            </div>
            <div>
              <div style={{ fontSize: 17, fontWeight: 700, color: tokens.fg }}>Poke sent! 🎉</div>
              <div style={{ fontSize: 13, color: tokens.muted, marginTop: 5 }}>
                {sentVia === 'slack'
                  ? `Slid into ${displayName}'s DMs`
                  : `Sent to ${displayName} via Gmail`}
              </div>
            </div>
          </div>
        )}

        {/* ── Compose / error state ── */}
        {step !== 'success' && (
          <div style={{ padding: '20px 22px' }}>

            {/* Channel picker */}
            <div style={{ marginBottom: 18 }}>
              <div style={{
                fontSize: 11.5, fontWeight: 700, letterSpacing: 0.7,
                color: tokens.muted, textTransform: 'uppercase', marginBottom: 8,
              }}>
                Send via
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <ChannelTab
                  label="Slack DM"
                  imgSrc="/slack-logo.svg"
                  active={channel === 'slack'}
                  onClick={() => setChannel('slack')}
                />
                <ChannelTab
                  label="Gmail"
                  imgSrc="/gmail-logo.svg"
                  active={channel === 'gmail'}
                  onClick={() => setChannel('gmail')}
                />
              </div>
            </div>

            {/* Contact field — always shown, pre-filled if email exists */}
            <div style={{ marginBottom: 16 }}>
              <Field
                label={channel === 'slack'
                  ? `${displayName}'s email (to find on Slack)`
                  : `${displayName}'s email`}
                hint={channel === 'slack'
                  ? `We'll look up their Slack account using this.`
                  : `This is where the email will land.`}
                value={email}
                onChange={setEmail}
                placeholder="name@example.com"
                autoFocus={!email}
              />
            </div>

            {/* Message */}
            <div style={{ marginBottom: 16 }}>
              <div style={{
                fontSize: 11.5, fontWeight: 700, letterSpacing: 0.7,
                color: tokens.muted, textTransform: 'uppercase', marginBottom: 6,
              }}>
                Message
              </div>
              <textarea
                value={message}
                onChange={e => setMessage(e.target.value)}
                disabled={step === 'sending'}
                rows={4}
                style={{
                  ...inputStyle,
                  padding: '11px 13px',
                  borderRadius: 10,
                  border: '1.5px solid var(--trumpet-edge-strong)',
                  background: 'var(--trumpet-surface)',
                  fontSize: 14, fontFamily: tokens.font, color: tokens.fg,
                  lineHeight: 1.65, resize: 'vertical', outline: 'none',
                  opacity: step === 'sending' ? 0.6 : 1,
                }}
                onFocus={e => { e.target.style.borderColor = 'rgba(255,255,255,0.4)'; }}
                onBlur={e  => { e.target.style.borderColor = 'var(--trumpet-edge-strong)'; }}
              />
            </div>

            {/* Error message */}
            {step === 'error' && errorMsg && (
              <div style={{
                marginBottom: 14,
                padding: '10px 13px',
                borderRadius: 10,
                background: `${tokens.red}0e`,
                border: `1px solid ${tokens.red}28`,
                fontSize: 13, color: tokens.red, lineHeight: 1.5,
              }}>
                {errorMsg}
                <div style={{ marginTop: 6 }}>
                  <button
                    onClick={() => { setStep('compose'); setErrorMsg(''); }}
                    style={{
                      background: 'none', border: 'none', cursor: 'pointer',
                      color: 'rgba(255,255,255,0.75)', fontWeight: 700, fontSize: 13,
                      fontFamily: tokens.font, padding: 0,
                    }}
                  >
                    ← Try again
                  </button>
                </div>
              </div>
            )}

            {/* Footer */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button
                onClick={onClose}
                style={{
                  padding: '9px 18px', borderRadius: 10,
                  border: '1px solid var(--trumpet-edge-strong)',
                  background: 'transparent', cursor: 'pointer',
                  fontSize: 14, fontWeight: 600, fontFamily: tokens.font,
                  color: tokens.inkSoft,
                }}
              >
                Cancel
              </button>
              <button
                onClick={send}
                disabled={!canSend}
                style={{
                  padding: '9px 22px', borderRadius: 10,
                  border: ' 1px solid rgba(255,255,255,0.3)',
                  background: canSend ? 'rgba(255,255,255,0.14)' : 'rgba(255,255,255,0.05)',
                  cursor: canSend ? 'pointer' : 'default',
                  fontSize: 14, fontWeight: 700, fontFamily: tokens.font,
                  color: 'rgba(255,255,255,0.9)', opacity: canSend ? 1 : 0.4,
                  display: 'flex', alignItems: 'center', gap: 8,
                  transition: 'background 0.15s, opacity 0.15s',
                }}
              >
                {step === 'sending' ? (
                  'Sending…'
                ) : (
                  <>
                    <PokeIcon size={14} color="rgba(255,255,255,0.9)" />
                    Send poke
                  </>
                )}
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
