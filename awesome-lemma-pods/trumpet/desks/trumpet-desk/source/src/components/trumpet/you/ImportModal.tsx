import * as React from 'react';
import { tokens } from '@/lib/tokens';

export interface ImportContact {
  name:  string;
  email: string;
}

interface ImportModalProps {
  contacts:  ImportContact[];
  source:    'gmail' | 'slack';
  onImport:  (selected: ImportContact[]) => Promise<void>;
  onClose:   () => void;
}

export function ImportModal({ contacts, source, onImport, onClose }: ImportModalProps) {
  const [checked, setChecked]   = React.useState<Set<string>>(() => new Set());
  const [loading, setLoading]   = React.useState(false);

  const toggle = (email: string) =>
    setChecked(prev => {
      const next = new Set(prev);
      next.has(email) ? next.delete(email) : next.add(email);
      return next;
    });

  const allChecked   = checked.size === contacts.length;
  const toggleAll    = () => setChecked(allChecked ? new Set() : new Set(contacts.map(c => c.email)));

  const selectedList = contacts.filter(c => checked.has(c.email));

  const handleImport = async () => {
    if (selectedList.length === 0) return;
    setLoading(true);
    try { await onImport(selectedList); }
    finally { setLoading(false); }
  };

  return (
    // Overlay
    <div style={{
      position:   'fixed',
      inset:      0,
      background: 'rgba(0,0,0,0.72)',
      display:    'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex:     500,
      fontFamily: tokens.font,
    }}>
      {/* Modal */}
      <div style={{
        width:         560,
        maxHeight:     660,
        background:    '#1c1a18',
        border:        '1px solid rgba(255,255,255,0.1)',
        borderRadius:  24,
        display:       'flex',
        flexDirection: 'column',
        overflow:      'hidden',
        boxShadow:     '0 40px 80px -20px rgba(0,0,0,0.9)',
      }}>

        {/* Header */}
        <div style={{
          padding:        '28px 32px 20px',
          borderBottom:   '1px solid rgba(255,255,255,0.07)',
          flexShrink:     0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <img
              src={source === 'slack' ? '/slack-logo.svg' : '/gmail-logo.svg'}
              alt=""
              style={{ width: 26, height: 26, flexShrink: 0 }}
            />
            <span style={{ fontSize: 22, fontWeight: 800, color: '#f3efe6', letterSpacing: -0.6 }}>
              {source === 'slack' ? 'Import from Slack' : 'Import from Gmail'}
            </span>
          </div>
          <div style={{ fontSize: 14, color: '#7a766d', marginTop: 5 }}>
            {contacts.length === 0
              ? 'No new contacts found.'
              : `Found ${contacts.length} new contact${contacts.length === 1 ? '' : 's'} — pick who to add.`}
          </div>
        </div>

        {contacts.length > 0 && (
          <>
            {/* Select all row */}
            <div
              onClick={toggleAll}
              style={{
                display:        'flex',
                alignItems:     'center',
                gap:            12,
                padding:        '14px 32px',
                borderBottom:   '1px solid rgba(255,255,255,0.05)',
                cursor:         'pointer',
                flexShrink:     0,
                userSelect:     'none',
              }}
            >
              <Checkbox checked={allChecked} />
              <span style={{ fontSize: 13, fontWeight: 600, color: '#7a766d', letterSpacing: 0.4 }}>
                SELECT ALL
              </span>
            </div>

            {/* Contact list */}
            <div style={{ overflowY: 'auto', flex: 1, scrollbarWidth: 'none' }}>
              {contacts.map(c => (
                <div
                  key={c.email}
                  onClick={() => toggle(c.email)}
                  style={{
                    display:     'flex',
                    alignItems:  'center',
                    gap:         14,
                    padding:     '13px 32px',
                    cursor:      'pointer',
                    borderBottom: '1px solid rgba(255,255,255,0.04)',
                    userSelect:  'none',
                  }}
                >
                  <Checkbox checked={checked.has(c.email)} />
                  <div>
                    <div style={{ fontSize: 15, fontWeight: 600, color: '#f3efe6' }}>
                      {c.name}
                    </div>
                    <div style={{ fontSize: 13, color: '#7a766d', marginTop: 2 }}>
                      {c.email}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}

        {/* Footer */}
        <div style={{
          display:         'flex',
          justifyContent:  'flex-end',
          gap:             10,
          padding:         '20px 32px',
          borderTop:       '1px solid rgba(255,255,255,0.07)',
          flexShrink:      0,
        }}>
          <button
            onClick={onClose}
            disabled={loading}
            style={{
              padding:      '10px 22px',
              borderRadius: 12,
              background:   'rgba(255,255,255,0.05)',
              border:       '1px solid rgba(255,255,255,0.1)',
              color:        '#7a766d',
              fontSize:     15,
              fontWeight:   600,
              cursor:       loading ? 'default' : 'pointer',
              fontFamily:   tokens.font,
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleImport}
            disabled={loading || checked.size === 0}
            style={{
              padding:      '10px 22px',
              borderRadius: 12,
              background:   checked.size === 0
                ? 'var(--trumpet-surface)'
                : 'rgba(239,233,219,0.12)',
              border:       '1px solid rgba(255,255,255,0.12)',
              color:        checked.size === 0 ? '#7a766d' : tokens.cream,
              fontSize:     15,
              fontWeight:   700,
              cursor:       (loading || checked.size === 0) ? 'default' : 'pointer',
              fontFamily:   tokens.font,
              transition:   'all 0.15s',
            }}
          >
            {loading
              ? 'Importing…'
              : checked.size === 0
                ? 'Import people'
                : `Import ${checked.size} ${checked.size === 1 ? 'person' : 'people'}`}
          </button>
        </div>
      </div>
    </div>
  );
}

function Checkbox({ checked }: { checked: boolean }) {
  return (
    <div style={{
      width:          18,
      height:         18,
      borderRadius:   5,
      border:         checked ? 'none' : '1.5px solid rgba(255,255,255,0.2)',
      background:     checked ? tokens.cream : 'transparent',
      flexShrink:     0,
      display:        'flex',
      alignItems:     'center',
      justifyContent: 'center',
      transition:     'all 0.12s',
    }}>
      {checked && (
        <svg width="11" height="9" viewBox="0 0 11 9" fill="none">
          <path d="M1 4.5L4 7.5L10 1" stroke={tokens.ink} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}
    </div>
  );
}
