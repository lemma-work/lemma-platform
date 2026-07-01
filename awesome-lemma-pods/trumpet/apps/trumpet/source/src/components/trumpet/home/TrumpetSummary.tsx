import * as React from 'react';
import { tokens } from '@/lib/tokens';
import type { TrumpetSummaryData } from '@/hooks/useTrumpetSummary';

interface Props {
  data:       TrumpetSummaryData;
  firstName:  string;
  userPhoto?: string;
}

function GreetingAvatar({ photo, name, size = 56 }: { photo?: string; name: string; size?: number }) {
  const [err, setErr] = React.useState(false);
  const show = photo && !err;

  // Deterministic gradient fallback
  const gradients = [
    'linear-gradient(135deg,#6366f1,#8b5cf6)',
    'linear-gradient(135deg,#f59e0b,#ef4444)',
    'linear-gradient(135deg,#10b981,#3b82f6)',
    'linear-gradient(135deg,#ec4899,#8b5cf6)',
    'linear-gradient(135deg,#f97316,#eab308)',
  ];
  const hash = name.split('').reduce((a, c) => a + c.charCodeAt(0), 0);
  const bg = gradients[hash % gradients.length];

  return (
    <div style={{
      display:        'inline-flex',
      alignItems:     'center',
      justifyContent: 'center',
      width:           size,
      height:          size,
      borderRadius:   '50%',
      overflow:       'hidden',
      background:     show ? 'transparent' : bg,
      border:         '2px solid var(--trumpet-avatar-ring)',
      flexShrink:     0,
    }}>
      {show ? (
        <img
          src={photo}
          alt={name}
          onError={() => setErr(true)}
          style={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'top' }}
        />
      ) : (
        <span style={{
          fontSize:   size * 0.42,
          fontWeight: 700,
          color:      'rgba(255,255,255,0.9)',
          fontFamily: tokens.font,
        }}>
          {name.charAt(0).toUpperCase()}
        </span>
      )}
    </div>
  );
}

export function TrumpetSummary({ data, firstName, userPhoto }: Props) {
  const hasLines = data.lines.length > 0;

  return (
    <>
      {/* ── Greeting — "Sup jin [avatar]" ── */}
      <div style={{
        marginTop:   76,
        display:     'flex',
        alignItems:  'center',
        gap:          12,
      }}>
        <span style={{
          fontSize:      60,
          fontWeight:    700,
          letterSpacing: -1.5,
          color:         tokens.muted,   // muted — not white
          fontFamily:    tokens.font,
          lineHeight:    1,
        }}>
          Sup {firstName}
        </span>
        <GreetingAvatar photo={userPhoto} name={firstName} size={56} />
      </div>

      {/* ── Summary ── */}
      <div style={{
        marginTop:    28,
        fontSize:     34,
        fontWeight:   500,
        lineHeight:   1.72,
        color:        tokens.muted,   // base text = muted
        maxWidth:     680,
        letterSpacing: -0.2,
        fontFamily:   tokens.font,
      }}>
        {!hasLines ? (
          <span>Slate's clean. Nothing on the books yet.</span>
        ) : (
          <>
            {/* Opener in muted */}
            {data.opener}{' '}

            {data.lines.map((line, i) => {
              const isLast = i === data.lines.length - 1;
              const prefix = i === 0
                ? ''
                : isLast
                  ? ', and '
                  : ', ';

              return (
                <React.Fragment key={i}>
                  {prefix}
                  {/* bright: emoji + count + noun */}
                  <span style={{ color: tokens.fg, fontWeight: 700 }}>
                    {line.bright}
                  </span>
                  {/* muted: context after */}
                  {' '}{line.muted}
                </React.Fragment>
              );
            })}
            {'.'}
          </>
        )}
      </div>
    </>
  );
}
