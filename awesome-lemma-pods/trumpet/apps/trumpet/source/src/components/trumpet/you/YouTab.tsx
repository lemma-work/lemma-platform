import * as React from 'react';
import { tokens } from '@/lib/tokens';
import { usePeople } from '@/hooks/usePeople';
import { useProfile } from '@/hooks/useProfile';
import { AvatarCircle } from './AvatarCircle';
import { MeSubtab } from './MeSubtab';
import { PeopleSubtab } from './PeopleSubtab';
import { ConnectedSubtab } from './ConnectedSubtab';

type YouSubtab = 'me' | 'people' | 'connected';

const NAV: { id: YouSubtab; label: string }[] = [
  { id: 'me',        label: 'ME'        },
  { id: 'people',    label: 'PEOPLE'    },
  { id: 'connected', label: 'CONNECTED' },
];

export function YouTab() {
  const [subtab, setSubtab] = React.useState<YouSubtab>('people');
  const { people, isLoading, refresh } = usePeople();
  const { user, avatarUrl, uploadAvatar, updateName } = useProfile();

  const u         = user as Record<string, unknown> | undefined;
  const first     = (u?.first_name as string | undefined)?.trim() ?? '';
  const last      = (u?.last_name  as string | undefined)?.trim() ?? '';
  const fullName  = [first, last].filter(Boolean).join(' ') || user?.email?.split('@')[0] || 'You';

  const [editingName, setEditingName]   = React.useState(false);
  const [nameFirst,   setNameFirst]     = React.useState(first);
  const [nameLast,    setNameLast]      = React.useState(last);
  const [savingName,  setSavingName]    = React.useState(false);

  // sync when user loads
  React.useEffect(() => {
    setNameFirst(first);
    setNameLast(last);
  }, [first, last]);

  const commitName = React.useCallback(async () => {
    setEditingName(false);
    const newFirst = nameFirst.trim();
    const newLast  = nameLast.trim();
    if (newFirst === first && newLast === last) return;
    setSavingName(true);
    try { await updateName(newFirst, newLast); } finally { setSavingName(false); }
  }, [nameFirst, nameLast, first, last, updateName]);

  const handleNameKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') void commitName();
    if (e.key === 'Escape') {
      setNameFirst(first);
      setNameLast(last);
      setEditingName(false);
    }
  };

  return (
    <div style={{
      position:      'absolute',
      inset:         0,
      display:       'flex',
      fontFamily:    tokens.font,
      paddingBottom: 140,
    }}>
      {/* ── Left sidebar ── */}
      <div style={{
        width:         210,
        flexShrink:    0,
        padding:       '52px 0 40px 52px',
        display:       'flex',
        flexDirection: 'column',
      }}>

        {/* Avatar */}
        <div style={{ marginBottom: 16 }}>
          <AvatarCircle
            src={avatarUrl}
            name={fullName}
            size={72}
            onUpload={uploadAvatar}
            style={{ border: '3px solid var(--trumpet-avatar-ring)' }}
          />
        </div>

        {/* Name — click to edit */}
        {editingName ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 28, marginRight: 16 }}>
            <input
              autoFocus
              value={nameFirst}
              onChange={e => setNameFirst(e.target.value)}
              onKeyDown={handleNameKeyDown}
              placeholder="First"
              style={{
                background:   'var(--trumpet-surface)',
                border:       '1px solid var(--trumpet-edge-strong)',
                borderRadius: 8,
                padding:      '5px 8px',
                fontSize:     13,
                fontWeight:   600,
                color:        tokens.fg,
                fontFamily:   tokens.font,
                outline:      'none',
                width:        '100%',
                boxSizing:    'border-box',
              }}
            />
            <input
              value={nameLast}
              onChange={e => setNameLast(e.target.value)}
              onKeyDown={handleNameKeyDown}
              onBlur={() => void commitName()}
              placeholder="Last"
              style={{
                background:   'var(--trumpet-surface)',
                border:       '1px solid var(--trumpet-edge-strong)',
                borderRadius: 8,
                padding:      '5px 8px',
                fontSize:     13,
                fontWeight:   600,
                color:        tokens.fg,
                fontFamily:   tokens.font,
                outline:      'none',
                width:        '100%',
                boxSizing:    'border-box',
              }}
            />
          </div>
        ) : (
          <div
            onClick={() => setEditingName(true)}
            title="Click to edit name"
            style={{
              fontSize:      18,
              fontWeight:    800,
              letterSpacing: -0.5,
              color:         tokens.fg,
              lineHeight:    1.15,
              marginBottom:  28,
              marginRight:   16,
              cursor:        'text',
              opacity:       savingName ? 0.5 : 1,
              transition:    'opacity 0.15s',
              wordBreak:     'break-word',
            }}
          >
            {fullName}
          </div>
        )}

        <nav style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {NAV.map(item => {
            const active = subtab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => setSubtab(item.id)}
                style={{
                  display:        'flex',
                  alignItems:     'center',
                  background:     'none',
                  border:         'none',
                  borderLeft:     active
                    ? '2px solid var(--trumpet-nav-active)'
                    : '2px solid transparent',
                  cursor:         'pointer',
                  padding:        '11px 16px',
                  fontSize:       12,
                  fontWeight:     700,
                  letterSpacing:  1.4,
                  textTransform:  'uppercase' as const,
                  color:          active ? 'var(--trumpet-nav-active)' : tokens.muted,
                  fontFamily:     tokens.font,
                  textAlign:      'left',
                  transition:     'color 0.15s, border-color 0.15s',
                }}
              >
                {item.label}
              </button>
            );
          })}
        </nav>
      </div>

      {/* ── Vertical rule ── */}
      <div style={{
        width:      1,
        background: 'var(--trumpet-divider)',
        flexShrink: 0,
        alignSelf:  'stretch',
      }} />

      {/* ── Right content panel ── */}
      <div style={{
        flex:     1,
        position: 'relative',
        overflow: 'hidden',
      }}>
        {subtab === 'me'        && <MeSubtab />}
        {subtab === 'people'    && (
          <PeopleSubtab
            people={people}
            isLoading={isLoading}
            onRefresh={refresh}
          />
        )}
        {subtab === 'connected' && <ConnectedSubtab />}
      </div>
    </div>
  );
}
