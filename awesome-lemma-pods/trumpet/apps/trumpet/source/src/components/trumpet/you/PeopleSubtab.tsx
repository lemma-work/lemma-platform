import * as React from 'react';
import { AnimatePresence } from 'framer-motion';
import { client } from '@/lib/client';
import { TABLES } from '@/lib/resources';
import { tokens } from '@/lib/tokens';
import { getOrgId } from '@/lib/org';
import { runtimeConfig } from '@/lib/runtime-config';
import { PersonDetail } from './PersonDetail';
import { ImportModal } from './ImportModal';
import type { ImportContact } from './ImportModal';
import type { PersonRecord } from '@/hooks/usePeople';

type ImportStatus = 'idle' | 'loading' | 'not_connected';

// ── Placeholder avatars ───────────────────────────────────────────────────────
// Vite globs every PNG in src/assets/avatars/ at build time.
// To add more: drop a .png into that folder and redeploy — no code change needed.

const _avatarModules = import.meta.glob<string>(
  '../../../assets/avatars/*.png',
  { eager: true, query: '?url', import: 'default' },
);
export const PLACEHOLDER_AVATARS: string[] = Object.values(_avatarModules).sort();

// ── API helpers ───────────────────────────────────────────────────────────────

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

async function fetchGmailContacts(orgId: string): Promise<ImportContact[]> {
  const all: ImportContact[] = [];
  let pageToken: string | undefined;
  for (let i = 0; i < 3; i++) {
    const payload: Record<string, unknown> = { page_size: 100 };
    if (pageToken) payload.page_token = pageToken;
    const raw = await execIntegration(orgId, 'gmail', 'GMAIL_GET_CONTACTS', payload);
    const connections: any[] = raw.connections ?? [];
    for (const c of connections) {
      const emailArr = c.emailAddresses ?? [];
      const email = (emailArr[0]?.value ?? '').toLowerCase().trim();
      if (!email) continue;
      const nameArr = c.names ?? [];
      const name = (nameArr[0]?.displayName ?? '').trim() || email.split('@')[0];
      all.push({ name, email });
    }
    pageToken = raw.nextPageToken;
    if (!pageToken) break;
  }
  return all;
}

async function fetchSlackContacts(orgId: string): Promise<ImportContact[]> {
  const all: ImportContact[] = [];
  let cursor: string | undefined;
  for (let i = 0; i < 5; i++) {
    const payload: Record<string, unknown> = { limit: 200 };
    if (cursor) payload.cursor = cursor;
    const raw = await execIntegration(orgId, 'slack', 'users_list', payload);
    const members: any[] = raw.members ?? [];
    for (const m of members) {
      if (m.is_bot || m.deleted || m.id === 'USLACKBOT') continue;
      const profile = m.profile ?? {};
      const email = (profile.email ?? '').toLowerCase().trim();
      if (!email) continue;
      const name = (m.real_name ?? profile.real_name ?? '').trim() || email.split('@')[0];
      all.push({ name, email });
    }
    cursor = raw.response_metadata?.next_cursor;
    if (!cursor) break;
  }
  return all;
}

// ── Initials fallback ─────────────────────────────────────────────────────────

const INITIALS_GRADIENTS = [
  'linear-gradient(135deg,#6366f1,#8b5cf6)',
  'linear-gradient(135deg,#f59e0b,#ef4444)',
  'linear-gradient(135deg,#10b981,#3b82f6)',
  'linear-gradient(135deg,#ec4899,#8b5cf6)',
  'linear-gradient(135deg,#f97316,#eab308)',
];
function hashName(n: string) { return n.split('').reduce((a,c) => a + c.charCodeAt(0), 0); }
function initials(name: string) { return name.split(' ').map(w => w[0]).filter(Boolean).join('').slice(0,2).toUpperCase(); }

// ── Person card ───────────────────────────────────────────────────────────────

function PersonCard({
  person,
  placeholderIdx,
  onOpen,
  onAvatarUpload,
}: {
  person:         PersonRecord;
  placeholderIdx: number;           // position in people list — drives unique avatar assignment
  onOpen:         () => void;
  onAvatarUpload: (file: File) => Promise<void>;
}) {
  const [hovered,   setHovered]   = React.useState(false);
  const [uploading, setUploading] = React.useState(false);
  const fileInputRef = React.useRef<HTMLInputElement>(null);
  const hasContext   = !!person.agentNotes?.trim();

  // photo_url set → use it; otherwise unique placeholder if available; else initials
  const placeholderSrc = placeholderIdx < PLACEHOLDER_AVATARS.length
    ? PLACEHOLDER_AVATARS[placeholderIdx]
    : null;
  const imgSrc = person.photo_url || placeholderSrc;

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = '';
    setUploading(true);
    try { await onAvatarUpload(file); } finally { setUploading(false); }
  };

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        borderRadius:  18,
        border:        `1px solid ${hovered ? 'rgba(87,184,106,0.35)' : 'var(--trumpet-edge-sm)'}`,
        background:    'var(--trumpet-bg)',
        overflow:      'hidden',
        cursor:        'pointer',
        transition:    'border-color 0.18s, box-shadow 0.18s',
        boxShadow:     hovered
          ? '0 8px 28px -12px rgba(87,184,106,0.2), 0 2px 8px -4px rgba(0,0,0,0.1)'
          : '0 2px 8px -4px rgba(0,0,0,0.06)',
        display:       'flex',
        flexDirection: 'column',
      }}
    >
      {/* Illustration area — flat beige background */}
      <div
        onClick={onOpen}
        style={{
          position:  'relative',
          height:    200,
          background: 'var(--trumpet-bg2)',
          display:   'flex',
          alignItems: 'flex-end',
          justifyContent: 'center',
          overflow:  'hidden',
          flexShrink: 0,
        }}
      >
        {/* Role badge */}
        {person.role && (
          <div style={{
            position:     'absolute',
            top:          12,
            right:        12,
            zIndex:       2,
            background:   'var(--trumpet-bg)',
            border:       '1px solid var(--trumpet-edge-strong)',
            borderRadius: 8,
            padding:      '4px 10px',
            fontSize:     11,
            fontWeight:   700,
            color:        tokens.fg,
            fontFamily:   tokens.font,
            letterSpacing: 0.1,
            maxWidth:     140,
            overflow:     'hidden',
            textOverflow: 'ellipsis',
            whiteSpace:   'nowrap',
          }}>
            {person.role}
          </div>
        )}

        {/* Avatar — illustration or uploaded photo or initials */}
        {imgSrc ? (
          <img
            src={imgSrc}
            alt={person.name}
            style={{
              height:         '95%',
              width:          '80%',
              objectFit:      person.photo_url ? 'cover' : 'contain',
              objectPosition: person.photo_url ? 'top center' : 'bottom center',
              display:        'block',
              opacity:        uploading ? 0.4 : 1,
              transition:     'opacity 0.15s',
              position:       'relative',
              zIndex:         1,
            }}
          />
        ) : (
          /* Initials fallback when no placeholder left */
          <div style={{
            width:          90,
            height:         90,
            borderRadius:   '50%',
            background:     INITIALS_GRADIENTS[hashName(person.name) % INITIALS_GRADIENTS.length],
            display:        'flex',
            alignItems:     'center',
            justifyContent: 'center',
            marginBottom:   20,
            border:         '3px solid var(--trumpet-bg)',
            flexShrink:     0,
            zIndex:         1,
            position:       'relative',
          }}>
            <span style={{ fontSize: 32, fontWeight: 700, color: 'rgba(255,255,255,0.9)', fontFamily: tokens.font }}>
              {initials(person.name)}
            </span>
          </div>
        )}

        {/* Camera button — top-left, only visible on hover */}
        {hovered && !uploading && (
          <button
            onClick={e => { e.stopPropagation(); fileInputRef.current?.click(); }}
            style={{
              position:       'absolute',
              top:            10,
              left:           10,
              zIndex:         3,
              width:          32,
              height:         32,
              borderRadius:   '50%',
              background:     'rgba(0,0,0,0.55)',
              border:         '1px solid rgba(255,255,255,0.25)',
              display:        'flex',
              alignItems:     'center',
              justifyContent: 'center',
              cursor:         'pointer',
              backdropFilter: 'blur(4px)',
              padding:        0,
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round">
              <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
              <circle cx="12" cy="13" r="4"/>
            </svg>
          </button>
        )}

        {uploading && (
          <div style={{
            position:       'absolute',
            inset:          0,
            zIndex:         3,
            display:        'flex',
            alignItems:     'center',
            justifyContent: 'center',
          }}>
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke={tokens.green} strokeWidth="2">
              <circle cx="12" cy="12" r="10" strokeOpacity="0.2"/>
              <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round">
                <animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.8s" repeatCount="indefinite"/>
              </path>
            </svg>
          </div>
        )}

        <input ref={fileInputRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={handleFileChange} />
      </div>

      {/* Info area */}
      <div
        onClick={onOpen}
        style={{
          padding:       '16px 18px 18px',
          display:       'flex',
          flexDirection: 'column',
          gap:           6,
          flex:          1,
        }}
      >
        <div style={{
          fontSize:      17,
          fontWeight:    800,
          color:         tokens.fg,
          fontFamily:    tokens.font,
          letterSpacing: -0.3,
          lineHeight:    1.2,
          whiteSpace:    'nowrap',
          overflow:      'hidden',
          textOverflow:  'ellipsis',
        }}>
          {person.name}
        </div>

        {person.email && (
          <div style={{
            display:    'flex',
            alignItems: 'center',
            gap:        6,
            fontSize:   12,
            color:      tokens.muted,
          }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ flexShrink: 0 }}>
              <rect x="2" y="4" width="20" height="16" rx="2"/>
              <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>
            </svg>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {person.email}
            </span>
          </div>
        )}

        {/* Add / Edit context button */}
        <button
          onClick={e => { e.stopPropagation(); onOpen(); }}
          style={{
            marginTop:    6,
            display:      'inline-flex',
            alignItems:   'center',
            gap:          6,
            padding:      '7px 13px',
            borderRadius: 10,
            border:       hasContext
              ? '1px solid rgba(87,184,106,0.3)'
              : '1px solid var(--trumpet-edge-sm)',
            background:   hasContext ? 'rgba(87,184,106,0.06)' : 'var(--trumpet-surface)',
            color:        hasContext ? '#3a8f50' : tokens.muted,
            fontSize:     12,
            fontWeight:   600,
            fontFamily:   tokens.font,
            cursor:       'pointer',
            alignSelf:    'flex-start',
            transition:   'all 0.15s',
          }}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
          {hasContext ? 'Edit context' : 'Add context'}
        </button>
      </div>
    </div>
  );
}

// ── Main subtab ───────────────────────────────────────────────────────────────

interface PeopleSubtabProps {
  people:    PersonRecord[];
  isLoading: boolean;
  onRefresh: () => void;
}

export function PeopleSubtab({ people, isLoading, onRefresh }: PeopleSubtabProps) {
  const [selectedPerson,  setSelectedPerson]  = React.useState<PersonRecord | null>(null);
  const [selectedIdx,     setSelectedIdx]     = React.useState<number>(0);
  const [importStatus,    setImportStatus]    = React.useState<ImportStatus>('idle');
  const [bannerMsg,       setBannerMsg]       = React.useState('');
  const [importContacts,  setImportContacts]  = React.useState<ImportContact[]>([]);
  const [importSource,    setImportSource]    = React.useState<'gmail' | 'slack'>('gmail');
  const [showModal,       setShowModal]       = React.useState(false);

  const runImport = React.useCallback(async (source: 'gmail' | 'slack') => {
    setImportStatus('loading');
    setBannerMsg('');
    try {
      const orgId = await getOrgId();
      let raw: ImportContact[];
      try {
        raw = source === 'gmail'
          ? await fetchGmailContacts(orgId)
          : await fetchSlackContacts(orgId);
      } catch (err: any) {
        setBannerMsg(err?.message ?? `${source} import failed`);
        setImportStatus('not_connected');
        return;
      }
      const existingEmails = new Set(people.map(p => p.email).filter(Boolean));
      const newContacts = raw.filter(c => c.email && !existingEmails.has(c.email));
      setImportContacts(newContacts);
      setImportSource(source);
      setShowModal(true);
    } catch (err: any) {
      setBannerMsg(err?.message ?? 'Unexpected error');
      setImportStatus('not_connected');
    } finally {
      setImportStatus('idle');
    }
  }, [people]);

  const handleImport = React.useCallback(async (selected: ImportContact[]) => {
    await Promise.all(
      selected.map(c => client.records.create(TABLES.people, { name: c.name, email: c.email }))
    );
    setShowModal(false);
    setImportContacts([]);
    onRefresh();
  }, [onRefresh]);

  const handlePersonClose = React.useCallback((refreshNeeded: boolean) => {
    setSelectedPerson(null);
    if (refreshNeeded) onRefresh();
  }, [onRefresh]);

  // Avatar upload from the card directly
  const handleCardAvatarUpload = React.useCallback(async (person: PersonRecord, file: File) => {
    const result = await client.files.upload(file, {
      directoryPath: '/pod/photos',
      name: `${person.id}${file.name.slice(file.name.lastIndexOf('.'))}`,
    });
    await client.records.update(TABLES.people, person.id, {
      photo_url: result.path,
    } as never);
    onRefresh();
  }, [onRefresh]);

  const btnBase: React.CSSProperties = {
    padding:      '9px 18px',
    borderRadius: 12,
    border:       '1px solid var(--trumpet-edge-strong)',
    fontSize:     14,
    fontWeight:   600,
    fontFamily:   tokens.font,
    background:   'var(--trumpet-chip-bg)',
    color:        tokens.fg,
    display:      'flex',
    alignItems:   'center',
    gap:          7,
    cursor:       'pointer',
  };

  return (
    <div style={{
      position:      'absolute',
      inset:         0,
      display:       'flex',
      flexDirection: 'column',
      fontFamily:    tokens.font,
      overflow:      'hidden',
    }}>
      {/* ── Header ── */}
      <div style={{
        display:        'flex',
        alignItems:     'flex-end',
        justifyContent: 'space-between',
        padding:        '44px 52px 28px',
        flexShrink:     0,
      }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <img src="/people-icon.svg" alt="" style={{ width: 52, height: 52, flexShrink: 0, marginTop: 8 }} />
            <div style={{
              fontSize:      43,
              fontWeight:    800,
              letterSpacing: -1,
              color:         tokens.fg,
              lineHeight:    1,
              fontFamily:    '"Nunito", "Fredoka", var(--font-sans)',
            }}>
              People
            </div>
          </div>
          <div style={{ fontSize: 14, color: tokens.muted, marginTop: 6 }}>
            {isLoading
              ? 'Loading…'
              : people.length === 0
                ? 'No contacts yet'
                : `${people.length} contact${people.length === 1 ? '' : 's'}`}
          </div>
        </div>

        <div style={{ display: 'flex', gap: 8 }}>
          <button
            onClick={importStatus === 'idle' ? () => runImport('gmail') : undefined}
            style={{
              ...btnBase,
              opacity: importStatus === 'loading' ? 0.6 : 1,
              cursor:  importStatus !== 'idle' ? 'default' : 'pointer',
            }}
          >
            {importStatus === 'loading' ? (
              <><span style={{ fontSize: 13 }}>⟳</span>Scanning Gmail…</>
            ) : (
              <><img src="/gmail-logo.svg" alt="" style={{ width: 16, height: 16 }} />Import from Gmail</>
            )}
          </button>

          <button
            onClick={importStatus === 'idle' ? () => runImport('slack') : undefined}
            style={{
              ...btnBase,
              opacity: importStatus === 'loading' ? 0.6 : 1,
              cursor:  importStatus !== 'idle' ? 'default' : 'pointer',
            }}
          >
            <img src="/slack-logo.svg" alt="" style={{ width: 16, height: 16 }} />
            Import from Slack
          </button>
        </div>
      </div>

      {/* Error banner */}
      {importStatus === 'not_connected' && bannerMsg && (
        <div style={{ padding: '0 52px 20px', flexShrink: 0 }}>
          <div style={{
            padding:        '16px 20px',
            background:     'rgba(243,178,35,0.08)',
            border:         '1px solid rgba(243,178,35,0.2)',
            borderRadius:   14,
            display:        'flex',
            alignItems:     'center',
            justifyContent: 'space-between',
            gap:            16,
          }}>
            <span style={{ fontSize: 13, color: tokens.amber, wordBreak: 'break-word', flex: 1 }}>
              {bannerMsg}
            </span>
            <button
              onClick={async () => {
                try {
                  const orgId = await getOrgId();
                  const req = await client.integrations.createConnectRequest(orgId, importSource);
                  if (req.authorization_url) window.open(req.authorization_url, '_blank');
                } catch { /* ignore */ }
              }}
              style={{
                padding:      '7px 16px',
                borderRadius: 10,
                background:   'rgba(243,178,35,0.15)',
                border:       '1px solid rgba(243,178,35,0.25)',
                color:        tokens.amber,
                fontSize:     13,
                fontWeight:   700,
                cursor:       'pointer',
                fontFamily:   tokens.font,
                flexShrink:   0,
              }}
            >
              Connect →
            </button>
          </div>
        </div>
      )}

      {/* Divider */}
      <div style={{ height: 1, background: 'var(--trumpet-divider)', flexShrink: 0, marginBottom: 4 }} />

      {/* ── Card grid ── */}
      <div style={{ flex: 1, overflowY: 'auto', scrollbarWidth: 'none', padding: '28px 48px 40px' }}>
        {isLoading ? (
          <div style={{ color: tokens.muted, fontSize: 15 }}>Loading contacts…</div>
        ) : people.length === 0 ? (
          <div style={{ color: tokens.muted, fontSize: 15, lineHeight: 1.7 }}>
            No people yet.<br />Import from Gmail or Slack to get started.
          </div>
        ) : (
          <div style={{
            display:               'grid',
            gridTemplateColumns:   'repeat(4, 1fr)',
            gap:                   16,
          }}>
            {people.map((person, idx) => (
              <PersonCard
                key={person.id}
                person={person}
                placeholderIdx={idx}
                onOpen={() => { setSelectedPerson(person); setSelectedIdx(idx); }}
                onAvatarUpload={file => handleCardAvatarUpload(person, file)}
              />
            ))}
          </div>
        )}
      </div>

      {/* ── PersonDetail slide-in ── */}
      <AnimatePresence>
        {selectedPerson && (
          <PersonDetail
            key={selectedPerson.id}
            person={selectedPerson}
            placeholderIdx={selectedIdx}
            onClose={handlePersonClose}
          />
        )}
      </AnimatePresence>

      {/* ── Import modal ── */}
      {showModal && (
        <ImportModal
          contacts={importContacts}
          source={importSource}
          onImport={handleImport}
          onClose={() => { setShowModal(false); setImportContacts([]); }}
        />
      )}
    </div>
  );
}
