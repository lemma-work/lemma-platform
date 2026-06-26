/**
 * Dock — macOS-style magnification dock using GSAP quickTo.
 * Neighbours of the hovered item scale up proportionally via Gaussian falloff.
 */
import * as React from 'react';
import gsap from 'gsap';
import { tokens } from '@/lib/tokens';
import { HomeIcon, HandshakeIcon, CalendarIcon, NotebookIcon, PersonIcon } from '@/components/trumpet/shared/TrumpetIcons';

export type Tab = 'home' | 'commits' | 'schedule' | 'notes' | 'you';

export const TAB_ORDER: Tab[] = ['home', 'commits', 'schedule', 'notes', 'you'];

interface DockItem {
  id:       Tab;
  icon:     string | React.ReactNode;
  label:    string;
  bg:       string;
  iconColor?: string;
}

const ITEMS: DockItem[] = [
  { id: 'home',     icon: <HomeIcon     size={28} />, label: 'Home',     bg: '#efe9db', iconColor: tokens.ink },
  { id: 'commits',  icon: <HandshakeIcon size={28} />, label: 'Commits', bg: '#f6d775', iconColor: tokens.ink },
  { id: 'schedule', icon: <CalendarIcon  size={28} />, label: 'Schedule', bg: '#cfe7cf', iconColor: tokens.ink },
  { id: 'notes',    icon: <NotebookIcon  size={28} />, label: 'Notes',    bg: '#d9cef0', iconColor: tokens.ink },
  { id: 'you',      icon: <PersonIcon    size={28} />, label: 'You',      bg: '#d8d4cc', iconColor: tokens.ink },
];

// Gaussian: scale decays with distance from cursor
const SIGMA    = 120; // px — spread of the magnification
const MAX_SCAL = 1.5;
const MIN_SCAL = 1.0;

interface DockProps {
  activeTab:     Tab;
  onNavigate:    (tab: Tab) => void;
  commitAlerts?: number;
}

export function Dock({ activeTab, onNavigate, commitAlerts = 0 }: DockProps) {
  const dockRef  = React.useRef<HTMLDivElement>(null);
  const circRefs = React.useRef<(HTMLDivElement | null)[]>([]);

  // GSAP quickTo scalers — one per item
  type Scalers = { sx: gsap.QuickToFunc; sy: gsap.QuickToFunc; y: gsap.QuickToFunc };
  const scalers = React.useRef<Scalers[]>([]);

  React.useEffect(() => {
    scalers.current = circRefs.current.map(el => {
      if (!el) return { sx: () => {}, sy: () => {}, y: () => {} } as unknown as Scalers;
      return {
        sx: gsap.quickTo(el, 'scaleX', { duration: 0.28, ease: 'power2.out' }),
        sy: gsap.quickTo(el, 'scaleY', { duration: 0.28, ease: 'power2.out' }),
        y:  gsap.quickTo(el, 'y',      { duration: 0.28, ease: 'power2.out' }),
      };
    });
  }, []);

  const handleMouseMove = React.useCallback((e: React.MouseEvent) => {
    const cx = e.clientX;
    circRefs.current.forEach((el, i) => {
      if (!el) return;
      const rect  = el.getBoundingClientRect();
      const itemX = rect.left + rect.width / 2;
      const dist  = Math.abs(cx - itemX);
      const g     = Math.exp(-(dist * dist) / (2 * SIGMA * SIGMA));
      const sc    = MIN_SCAL + (MAX_SCAL - MIN_SCAL) * g;
      const yOff  = -(sc - 1) * 22;
      scalers.current[i]?.sx(sc);
      scalers.current[i]?.sy(sc);
      scalers.current[i]?.y(yOff);
    });
  }, []);

  const handleMouseLeave = React.useCallback(() => {
    scalers.current.forEach(s => { s?.sx(1); s?.sy(1); s?.y(0); });
  }, []);

  return (
    <div
      ref={dockRef}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      style={{
        position:      'absolute',
        bottom:        40,
        left:          '50%',
        transform:     'translateX(-50%)',
        display:       'flex',
        alignItems:    'center',
        padding:       '12px 14px',
        gap:           2,
        background:    'var(--trumpet-dock-bg)',
        border:        '1px solid var(--trumpet-dock-edge)',
        borderRadius:  100,
        boxShadow:     'var(--trumpet-dock-shadow)',
        zIndex:        50,
      }}
    >
      {ITEMS.map((item, i) => (
        <DockItemEl
          key={item.id}
          item={item}
          isActive={activeTab === item.id}
          circRef={el => { circRefs.current[i] = el; }}
          onClick={() => onNavigate(item.id)}
          alerting={item.id === 'commits' && commitAlerts > 0}
        />
      ))}
    </div>
  );
}

interface DockItemElProps {
  item:      DockItem;
  isActive:  boolean;
  circRef:   React.Ref<HTMLDivElement>;
  onClick:   () => void;
  alerting?: boolean;
}

function DockItemEl({ item, isActive, circRef, onClick, alerting }: DockItemElProps) {
  const [hovered, setHovered] = React.useState(false);
  const iconRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    const el = iconRef.current;
    if (!el) return;
    if (alerting) {
      gsap.to(el, { scale: 1.12, duration: 1.2, repeat: -1, yoyo: true, ease: 'sine.inOut' });
    } else {
      gsap.killTweensOf(el);
      gsap.to(el, { scale: 1, duration: 0.3, ease: 'power2.out' });
    }
    return () => { gsap.killTweensOf(el); };
  }, [alerting]);

  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        position:        'relative',
        cursor:          'pointer',
        padding:         '4px 18px',
        display:         'flex',
        alignItems:      'center',
        justifyContent:  'center',
      }}
    >
      {/* Tooltip */}
      <div style={{
        position:         'absolute',
        top:              -42,
        left:             '50%',
        transform:        `translateX(-50%) translateY(${hovered ? 0 : 6}px)`,
        background:       'var(--trumpet-tip-bg)',
        color:            'var(--trumpet-tip-fg)',
        fontSize:         15,
        fontWeight:       600,
        padding:          '5px 11px',
        borderRadius:     8,
        whiteSpace:       'nowrap',
        opacity:          hovered ? 1 : 0,
        pointerEvents:    'none',
        transition:       'opacity 0.15s, transform 0.15s',
        boxShadow:        '0 8px 20px -8px rgba(0,0,0,0.7)',
        fontFamily:       tokens.font,
      }}>
        {item.label}
      </div>

      {/* Circle */}
      <div
        ref={circRef}
        style={{
          width:          62,
          height:         62,
          borderRadius:   '50%',
          display:        'flex',
          alignItems:     'center',
          justifyContent: 'center',
          fontSize:       32,
          background:     item.bg,
          outline:        isActive ? '2.5px solid var(--trumpet-dock-ring)' : 'none',
          outlineOffset:  3,
          boxShadow:      '0 6px 14px -6px rgba(0,0,0,0.5), 0 1px 0 rgba(255,255,255,0.4) inset',
          transformOrigin: 'bottom center',
          fontFamily:     "'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji',sans-serif",
          userSelect:     'none',
          color:          item.iconColor ?? 'inherit',
        }}
      >
        <div ref={iconRef} style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          {item.icon}
        </div>
      </div>

      {/* Active dot */}
      {isActive && (
        <div style={{
          position:     'absolute',
          bottom:       0,
          left:         '50%',
          transform:    'translateX(-50%)',
          width:        5,
          height:       5,
          borderRadius: '50%',
          background:   'var(--trumpet-dock-dot)',
        }} />
      )}
    </div>
  );
}
