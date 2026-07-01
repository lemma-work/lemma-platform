import * as React from 'react';
import { tokens } from '@/lib/tokens';
import { useProfile } from '@/hooks/useProfile';
import { AvatarCircle } from './AvatarCircle';

type ThemeMode = 'light' | 'dark';

function getTheme(): ThemeMode {
  const stored = window.localStorage.getItem('lemma-theme');
  if (stored === 'light' || stored === 'dark') return stored;
  return 'dark';
}

function applyTheme(t: ThemeMode) {
  document.documentElement.classList.toggle('dark', t === 'dark');
  document.documentElement.style.colorScheme = t;
  window.localStorage.setItem('lemma-theme', t);
}

export function MeSubtab() {
  const { user, isLoading } = useProfile();

  if (isLoading) {
    return (
      <div style={{ padding: '60px 80px', color: tokens.muted, fontSize: 16, fontFamily: tokens.font }}>
        Loading…
      </div>
    );
  }

  const u        = user as Record<string, unknown> | undefined;
  const first    = (u?.first_name as string | undefined)?.trim();
  const last     = (u?.last_name  as string | undefined)?.trim();
  const fullName = [first, last].filter(Boolean).join(' ') || user?.email?.split('@')[0] || 'You';
  const role     = u?.role       as string | undefined;
  const avatar   = u?.avatar_url as string | undefined;
  const email    = user?.email;

  return (
    <div style={{
      padding:       '60px 80px',
      fontFamily:    tokens.font,
      display:       'flex',
      flexDirection: 'column',
      gap:           36,
    }}>

      {/* ── Profile card ── */}
      <div style={{
        display:         'flex',
        alignItems:      'center',
        gap:             32,
        padding:         '40px 44px',
        background:      'var(--trumpet-surface)',
        border:          '1px solid var(--trumpet-divider)',
        borderRadius:    24,
      }}>
        <AvatarCircle
          src={avatar || '/photos/user.jpg'}
          name={fullName}
          size={96}
          style={{ border: '3px solid var(--trumpet-avatar-ring)' }}
        />

        <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
          <div style={{
            fontSize:      40,
            fontWeight:    800,
            letterSpacing: -1.4,
            color:         tokens.fg,
            lineHeight:    1.05,
          }}>
            {fullName}
          </div>

          {role && (
            <div style={{ fontSize: 18, fontWeight: 500, color: tokens.muted }}>
              {role}
            </div>
          )}

          {email && (
            <div style={{
              fontSize:  14,
              color:     tokens.muted,
              marginTop: 2,
              opacity:   0.7,
            }}>
              {email}
            </div>
          )}
        </div>
      </div>

      {/* ── Appearance ── */}
      <AppearanceRow />

      {/* ── Note ── */}
      <p style={{
        fontSize:   14,
        color:      tokens.muted,
        margin:     0,
        lineHeight: 1.65,
        opacity:    0.65,
      }}>
        Click your photo or name in the sidebar to update them. Role is managed in your Lemma account settings.
      </p>
    </div>
  );
}

function AppearanceRow() {
  const [theme, setTheme] = React.useState<ThemeMode>(() => getTheme());

  const toggle = (t: ThemeMode) => {
    applyTheme(t);
    setTheme(t);
  };

  return (
    <div style={{
      background:   'var(--trumpet-surface)',
      border:       '1px solid var(--trumpet-divider)',
      borderRadius: 16,
      overflow:     'hidden',
    }}>
      <div style={{
        padding:        '18px 28px',
        borderBottom:   '1px solid var(--trumpet-divider)',
        fontSize:       11,
        fontWeight:     700,
        letterSpacing:  1.2,
        textTransform:  'uppercase' as const,
        color:          tokens.muted,
      }}>
        Appearance
      </div>

      <div style={{
        display:        'flex',
        alignItems:     'center',
        justifyContent: 'space-between',
        padding:        '20px 28px',
      }}>
        <div style={{ fontSize: 15, fontWeight: 500, color: tokens.fg }}>
          Theme
        </div>

        <div style={{
          display:      'flex',
          alignItems:   'center',
          gap:          4,
          background:   'var(--trumpet-surface-md)',
          border:       '1px solid var(--trumpet-edge)',
          borderRadius: 10,
          padding:      4,
        }}>
          {(['light', 'dark'] as const).map(opt => (
            <button
              key={opt}
              onClick={() => toggle(opt)}
              style={{
                padding:      '7px 18px',
                borderRadius: 7,
                border:       'none',
                cursor:       'pointer',
                fontSize:     13,
                fontWeight:   600,
                fontFamily:   tokens.font,
                background:   theme === opt ? 'var(--trumpet-sel-bg)' : 'transparent',
                color:        theme === opt ? tokens.fg : tokens.muted,
                transition:   'background 0.15s, color 0.15s',
              }}
            >
              {opt === 'light' ? 'Light' : 'Dark'}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
