/**
 * NotesTab — Phase 3
 *
 * Layout (all absolute on the 1512×1008 canvas):
 *   Header:      top 78,  left 100, right 60  — static
 *   Stacks row:  top 188, left 0              — GSAP scroll-scrubbed fade+drift
 *   Pin strip:   top 174, left 0              — GSAP scroll-scrubbed fade+drift (separate element!)
 *   Notes grid:  top 512→250, left 100        — GSAP-animated top on scroll
 *
 * Scroll-collapse:
 *   Stacks and pin strip are TWO independent absolutely-positioned elements.
 *   No shared wrapper. No clip-path. This avoids the clip-from-bottom killing the pins,
 *   and avoids an invisible div blocking the notes grid.
 *   Pure opacity + y-drift scrubbed 1:1 against grid scrollTop.
 */
import * as React from 'react';
import gsap from 'gsap';
import { AnimatePresence } from 'framer-motion';
import { tokens } from '@/lib/tokens';
import {
  useNotes,
  relativeTime,
  CATEGORY_CONFIG,
} from '@/hooks/useNotes';
import type { NoteCategory, NoteRecord, FolderGroup } from '@/hooks/useNotes';
import { TrumpetSkeleton } from '../shared/TrumpetSkeleton';
import { NoteEditor }  from './NoteEditor';

// ─── Layout constants ─────────────────────────────────────────────────────────

// Folder card dimensions
const STACK_W           = 278;
const STACK_H           = 232;
const CARD_TOP          = 50;
const STACK_CONTAINER_H = STACK_H + CARD_TOP;   // 282

// Canvas edge margins
const L = 100;
const R = 60;

// Fixed positions on the 1512×1008 canvas
const HEADER_TOP  = 78;
const FOLDER_TOP  = 188;
const FOLDER_BOTTOM = FOLDER_TOP + STACK_CONTAINER_H;  // 470
const LABEL_TOP   = FOLDER_BOTTOM + 42;                // 512
const GRID_BOTTOM = 110;

// Scroll-collapse animation
const PIN_H           = 56;    // height of the collapsed folder pin strip
const COLLAPSE_AT     = 200;   // px of grid scroll to fully collapse
const FOLDER_PIN_TOP  = HEADER_TOP + 96;                      // 174 — pinned seat (just below header)
const GRID_TOP_FULL   = LABEL_TOP;                            // 512 — grid top when folders expanded
const GRID_TOP_PINNED = FOLDER_PIN_TOP + PIN_H + 4;           // 234 — grid top when folders pinned (tight against pins)

// ─── Folder Stack Card ────────────────────────────────────────────────────────

function FolderStackCard({
  group,
  isActive,
  onClick,
}: {
  group:    FolderGroup;
  isActive: boolean;
  onClick:  () => void;
}) {
  const cfg = CATEGORY_CONFIG[group.category];

  const backRef = React.useRef<(HTMLDivElement | null)[]>([]);
  const [hovered,     setHovered]     = React.useState(false);
  const [showPreview, setShowPreview] = React.useState(false);
  const timerRef = React.useRef<ReturnType<typeof setTimeout>>();

  const REST  = [{ x: -5,  y: -7,  r: -4  }, { x: 5,  y: -10, r: 4  }];
  const HOVER = [{ x: -28, y: -8,  r: -14 }, { x: 28, y: -8,  r: 14 }];

  const animate = React.useCallback((isHover: boolean) => {
    backRef.current.forEach((el, i) => {
      if (!el) return;
      const t = isHover ? HOVER[i] : REST[i];
      gsap.to(el, {
        x: t.x, y: t.y, rotation: t.r,
        duration:  isHover ? 0.35 : 0.25,
        ease:      isHover ? 'back.out(1.2)' : 'power3.out',
        overwrite: 'auto',
      });
    });
  }, []);

  const onEnter = () => {
    setHovered(true);
    animate(true);
    timerRef.current = setTimeout(() => setShowPreview(true), 80);
  };
  const onLeave = () => {
    setHovered(false);
    animate(false);
    clearTimeout(timerRef.current);
    setShowPreview(false);
  };
  React.useEffect(() => () => clearTimeout(timerRef.current), []);

  const tagWords = cfg.label.toLowerCase().split(' ').slice(0, 2);

  return (
    <div
      onClick={onClick}
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      style={{
        position:  'relative',
        width:     STACK_W,
        height:    STACK_CONTAINER_H,
        cursor:    'pointer',
        flexShrink: 0,
        zIndex:    hovered ? 10 : 1,
        overflow:  'visible',
      }}
    >
      {/* Backing cards (GSAP fan-out) */}
      {[0, 1].map(i => (
        <div
          key={i}
          ref={el => { backRef.current[i] = el; }}
          style={{
            position:        'absolute',
            top:             CARD_TOP,
            left:            0,
            width:           STACK_W,
            height:          STACK_H,
            borderRadius:    20,
            background:      `${cfg.folderColor}cc`,
            boxShadow:       '0 4px 16px -8px rgba(0,0,0,0.25)',
            transformOrigin: 'bottom center',
            transform:       `translateX(${REST[i].x}px) translateY(${REST[i].y}px) rotate(${REST[i].r}deg)`,
            willChange:      'transform',
          }}
        />
      ))}

      {/* Main card */}
      <div style={{
        position:       'absolute',
        top:            CARD_TOP,
        left:           0,
        width:          STACK_W,
        height:         STACK_H,
        borderRadius:   20,
        background:     cfg.folderColor,
        boxShadow:      isActive
          ? `0 0 0 2.5px ${tokens.fg}, 0 12px 32px -12px rgba(0,0,0,0.55)`
          : hovered
          ? '0 14px 36px -14px rgba(0,0,0,0.55)'
          : '0 8px 24px -12px rgba(0,0,0,0.4)',
        padding:        '22px 22px 18px',
        display:        'flex',
        flexDirection:  'column',
        justifyContent: 'space-between',
        transform:      hovered ? 'translateY(-3px)' : 'translateY(0)',
        transition:     'box-shadow 0.18s, transform 0.18s',
        zIndex:         2,
      }}>
        <div>
          <div style={{
            fontSize:     34,
            lineHeight:   1,
            marginBottom: 10,
            fontFamily:   "'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji',sans-serif",
          }}>
            {cfg.emoji}
          </div>
          <div style={{
            fontSize:      20,
            fontWeight:    700,
            color:         cfg.textColor,
            fontFamily:    tokens.font,
            letterSpacing: -0.3,
            lineHeight:    1.25,
          }}>
            {cfg.label}
          </div>
          <div style={{ fontSize: 15, fontWeight: 500, color: `${cfg.textColor}99`, fontFamily: tokens.font, marginTop: 4 }}>
            {group.count} note{group.count !== 1 ? 's' : ''}
          </div>
        </div>

        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {tagWords.map(t => (
            <span key={t} style={{
              fontSize:     11,
              fontWeight:   600,
              padding:      '3px 8px',
              borderRadius: 99,
              background:   `${cfg.textColor}18`,
              color:        `${cfg.textColor}bb`,
              fontFamily:   tokens.font,
            }}>
              {t}
            </span>
          ))}
        </div>
      </div>

      {/* Hover preview bubble */}
      {showPreview && group.recentNotes.length > 0 && (
        <div style={{
          position:      'absolute',
          top:           42,
          left:          STACK_W + 16,
          width:         220,
          background:    'var(--trumpet-tip-bg)',
          borderRadius:  14,
          padding:       '12px 14px',
          boxShadow:     '0 16px 36px -12px rgba(0,0,0,0.5)',
          border:        '1px solid var(--trumpet-edge-strong)',
          zIndex:        30,
          animation:     'fadeSlideIn 0.15s ease',
          pointerEvents: 'none',
        }}>
          <div style={{
            fontSize:      11,
            fontWeight:    600,
            letterSpacing: 1,
            textTransform: 'uppercase',
            color:         tokens.muted,
            fontFamily:    tokens.font,
            marginBottom:  8,
          }}>
            Recent
          </div>
          {group.recentNotes.map(n => (
            <div key={n.id} style={{
              padding:      '7px 0',
              borderBottom: '1px solid var(--trumpet-edge-sm)',
              display:      'flex',
              alignItems:   'center',
              gap:          8,
            }}>
              <span style={{ fontSize: 14, color: tokens.muted }}>—</span>
              <span style={{
                fontSize:     14,
                fontWeight:   600,
                color:        tokens.fg,
                fontFamily:   tokens.font,
                whiteSpace:   'nowrap',
                overflow:     'hidden',
                textOverflow: 'ellipsis',
                flex:         1,
              }}>
                {n.title}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Folder Stacks Row ────────────────────────────────────────────────────────

function FolderStacksRow({
  folders,
  activeCategory,
  onSelect,
}: {
  folders:        FolderGroup[];
  activeCategory: NoteCategory | null;
  onSelect:       (cat: NoteCategory | null) => void;
}) {
  return (
    <div style={{ display: 'flex', gap: 120, alignItems: 'flex-end', overflow: 'visible' }}>
      {folders.map(group => (
        <FolderStackCard
          key={group.category}
          group={group}
          isActive={activeCategory === group.category}
          onClick={() => onSelect(activeCategory === group.category ? null : group.category)}
        />
      ))}
    </div>
  );
}

// ─── Folder Pin Strip (collapsed state) ──────────────────────────────────────

const FolderPinStrip = React.forwardRef<
  HTMLDivElement,
  {
    folders:        FolderGroup[];
    activeCategory: NoteCategory | null;
    onSelect:       (cat: NoteCategory | null) => void;
  }
>(function FolderPinStrip({ folders, activeCategory, onSelect }, ref) {
  return (
    <div
      ref={ref}
      style={{
        position:      'absolute',
        top:           FOLDER_PIN_TOP,   // placed directly on canvas — NOT inside a parent
        left:          0,
        right:         0,
        paddingLeft:   L,
        height:        PIN_H,
        display:       'flex',
        alignItems:    'center',
        gap:           10,
        opacity:       0,              // GSAP-driven
        pointerEvents: 'none',         // GSAP-driven
        background:    tokens.bg,      // solid — prevents notes scrolling through underneath
        zIndex:        20,
      }}
    >
      {folders.map(group => {
        const cfg      = CATEGORY_CONFIG[group.category];
        const isActive = activeCategory === group.category;
        return (
          <button
            key={group.category}
            onClick={() => onSelect(activeCategory === group.category ? null : group.category)}
            style={{
              display:       'flex',
              alignItems:    'center',
              gap:           9,
              padding:       '10px 18px 10px 14px',
              borderRadius:  99,
              background:    cfg.folderColor,   // full opacity — always clearly visible
              border:        isActive
                               ? `2.5px solid ${cfg.textColor}60`
                               : '2px solid transparent',
              boxShadow:     isActive
                               ? `0 0 0 3px ${cfg.folderColor}55`
                               : '0 3px 14px -5px rgba(0,0,0,0.55)',
              cursor:        'pointer',
              fontSize:      15,
              fontWeight:    700,
              letterSpacing: -0.2,
              color:         cfg.textColor,
              fontFamily:    tokens.font,
              transition:    'box-shadow 0.15s',
              whiteSpace:    'nowrap',
            }}
          >
            <span style={{ fontFamily: "'Apple Color Emoji','Segoe UI Emoji',sans-serif", fontSize: 17 }}>
              {cfg.emoji}
            </span>
            {cfg.label}
            {/* count separated by a thin rule */}
            <span style={{
              fontSize:      13,
              fontWeight:    600,
              opacity:       0.65,
              marginLeft:    2,
              paddingLeft:   8,
              borderLeft:    `1.5px solid ${cfg.textColor}35`,
            }}>
              {group.count}
            </span>
          </button>
        );
      })}
    </div>
  );
});

// ─── Sticky Note Card ─────────────────────────────────────────────────────────

function StickyNote({ note, onClick }: { note: NoteRecord; onClick: () => void }) {
  const [hovered, setHovered] = React.useState(false);
  const cfg = CATEGORY_CONFIG[note.category];

  const firstTag = note.keywords.split(',').map(t => t.trim()).filter(Boolean)[0] ?? '';

  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        position:       'relative',
        background:     note.color,
        borderRadius:   18,
        padding:        '22px 22px 18px',
        // Hairline ring lifts the light card off the black canvas so its edge
        // never dissolves into the background; layered drop shadow adds depth.
        boxShadow:      hovered
          ? '0 0 0 1px rgba(255,255,255,0.10), 0 18px 44px -12px rgba(0,0,0,0.75), 0 1px 0 rgba(255,255,255,0.5) inset'
          : '0 0 0 1px rgba(255,255,255,0.07), 0 10px 28px -10px rgba(0,0,0,0.6), 0 1px 0 rgba(255,255,255,0.4) inset',
        transform:      hovered ? 'translateY(-5px) scale(1.015)' : 'translateY(0) scale(1)',
        transition:     'all 0.22s cubic-bezier(0.25, 0.46, 0.45, 0.94)',
        cursor:         'pointer',
        overflow:       'hidden',
        height:         210,
        display:        'flex',
        flexDirection:  'column',
        justifyContent: 'space-between',
      }}
    >
      {/* Folded corner */}
      <div style={{
        position:        'absolute',
        bottom:          0,
        right:           0,
        width:           28,
        height:          28,
        borderRadius:    '18px 0 18px 0',
        backgroundImage: 'linear-gradient(135deg, transparent 50%, rgba(0,0,0,0.08) 50%)',
      }} />

      {note.pinned && (
        <div style={{
          position:   'absolute',
          top:        14,
          right:      16,
          fontSize:   15,
          opacity:    0.55,
          fontFamily: "'Apple Color Emoji','Segoe UI Emoji',sans-serif",
        }}>
          📌
        </div>
      )}

      <div>
        <div style={{
          fontSize:        21,
          fontWeight:      700,
          color:           cfg.textColor,
          fontFamily:      tokens.font,
          letterSpacing:   -0.5,
          lineHeight:      1.22,
          paddingRight:    note.pinned ? 22 : 0,
          marginBottom:    10,
          display:         '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
          overflow:        'hidden',
        }}>
          {note.title}
        </div>

        <div style={{
          fontSize:        13.5,
          color:           `${cfg.textColor}a6`,
          fontFamily:      tokens.font,
          lineHeight:      1.55,
          overflow:        'hidden',
          maxHeight:       hovered ? 78 : 54,
          transition:      'max-height 0.22s ease',
          maskImage:       'linear-gradient(to bottom, black 55%, transparent 100%)',
          WebkitMaskImage: 'linear-gradient(to bottom, black 55%, transparent 100%)',
        }}>
          {note.summary}
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 8 }}>
        <span style={{
          fontSize:     11,
          fontWeight:   600,
          padding:      '3px 8px',
          borderRadius: 99,
          background:   `${cfg.textColor}18`,
          color:        `${cfg.textColor}99`,
          fontFamily:   tokens.font,
        }}>
          {cfg.label}
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {firstTag && (
            <span style={{ fontSize: 11, color: `${cfg.textColor}60`, fontFamily: tokens.font }}>
              {firstTag}
            </span>
          )}
          <span style={{ fontSize: 11, fontWeight: 500, color: `${cfg.textColor}60`, fontFamily: tokens.font }}>
            {relativeTime(note.updated_at)}
          </span>
        </div>
      </div>
    </div>
  );
}

// ─── Skeletons ────────────────────────────────────────────────────────────────

function GridSkeleton() {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 24 }}>
      {[0,1,2,3,4,5].map(i => (
        <TrumpetSkeleton key={i} width="100%" height={210} radius={18} />
      ))}
    </div>
  );
}

// ─── Main Tab ─────────────────────────────────────────────────────────────────

export function NotesTab() {
  const {
    recentNotes,
    folders,
    isLoading,
    activeCategory,
    setActiveCategory,
    refresh,
  } = useNotes();

  const [selectedNote, setSelectedNote] = React.useState<NoteRecord | null>(null);

  // ── Scroll-collapse refs ──
  // NOTE: stacks and pin strip are SEPARATE canvas elements — no shared wrapper.
  const folderStacksRef = React.useRef<HTMLDivElement>(null);
  const pinStripRef     = React.useRef<HTMLDivElement>(null);
  const gridRef         = React.useRef<HTMLDivElement>(null);
  // Spacer at top of grid content — height tracks scrollTop so the first card
  // is always anchored at grid_top+label_height regardless of scroll progress.
  // Math: first_card_screen_y = grid_top + spacer + label_h - scrollTop
  //                           = grid_top + label_h  (spacer and scrollTop cancel)
  const spacerRef       = React.useRef<HTMLDivElement>(null);

  /**
   * Scrub the folder-collapse animation against grid scroll position.
   * t=0 → fully expanded stacks. t=1 → compact pin strip.
   * Pure opacity + y-drift — no clip-path, no shared wrapper, no blocking divs.
   */
  const handleGridScroll = React.useCallback(() => {
    if (!gridRef.current) return;
    const sy = gridRef.current.scrollTop;
    const t  = Math.min(1, Math.max(0, sy / COLLAPSE_AT));

    // Stacks: fade + slight upward drift, pointer-events off when invisible
    if (folderStacksRef.current) {
      gsap.set(folderStacksRef.current, {
        opacity:       Math.max(0, 1 - t * 1.43),   // zero at t≈0.70
        y:             -t * 20,
        pointerEvents: t > 0.68 ? 'none' : 'auto',
      });
    }

    // Pin strip: fade in from t=0.25, slides down slightly into its seat
    if (pinStripRef.current) {
      const pinOpacity = Math.max(0, (t - 0.25) / 0.75);
      gsap.set(pinStripRef.current, {
        opacity:       pinOpacity,
        y:             (1 - Math.min(1, t / 0.75)) * 10,
        pointerEvents: pinOpacity > 0.2 ? 'auto' : 'none',
      });
    }

    // Grid: slide up to fill the space vacated by collapsing folders
    if (gridRef.current) {
      gsap.set(gridRef.current, {
        top: Math.round(GRID_TOP_FULL - t * (GRID_TOP_FULL - GRID_TOP_PINNED)),
      });
    }

    // Spacer: height = min(scrollTop, COLLAPSE_AT) so the first card is always
    // anchored at grid_top+label_h — never clipped during the collapse animation.
    // overflow-anchor:none on the grid prevents the browser from compensating.
    if (spacerRef.current) {
      gsap.set(spacerRef.current, { height: Math.min(sy, COLLAPSE_AT) });
    }
  }, []);

  // Re-run scrub when data loads (user may have already scrolled)
  React.useEffect(() => {
    if (!isLoading) handleGridScroll();
  }, [isLoading, handleGridScroll]);

  const filteredNotes = activeCategory
    ? recentNotes.filter(n => n.category === activeCategory)
    : recentNotes;

  const activeCfg = activeCategory ? CATEGORY_CONFIG[activeCategory] : null;

  const handleClose = (refreshNeeded?: boolean) => {
    setSelectedNote(null);
    if (refreshNeeded) refresh();
  };

  // Human subtitle
  const pileWord = folders.length === 1 ? 'pile' : 'piles';
  const noteWord = recentNotes.length === 1 ? 'note' : 'notes';
  const subtitle = recentNotes.length > 0
    ? `${recentNotes.length} ${noteWord} across ${folders.length} messy little ${pileWord}`
    : 'your blank canvas — start writing';

  const lastNote = recentNotes[0];

  return (
    <>
      {/* ── Header ── */}
      <div style={{
        position:       'absolute',
        top:            HEADER_TOP,
        left:           L,
        right:          R,
        display:        'flex',
        alignItems:     'flex-start',
        justifyContent: 'space-between',
        zIndex:         5,
      }}>
        <div>
          <h1 style={{
            fontSize:      62,
            fontWeight:    800,
            letterSpacing: -2,
            color:         tokens.fg,
            fontFamily:    tokens.font,
            margin:        0,
            lineHeight:    1,
          }}>
            Notes
          </h1>
          <p style={{
            fontSize:   17,
            color:      tokens.muted,
            fontFamily: tokens.font,
            margin:     '8px 0 0',
            fontWeight: 400,
          }}>
            {subtitle}
          </p>
        </div>

        {lastNote && (
          <div style={{
            display:       'flex',
            flexDirection: 'column',
            alignItems:    'flex-end',
            gap:           6,
            paddingTop:    6,
          }}>
            <span style={{
              fontSize:      13,
              color:         tokens.muted,
              fontFamily:    tokens.font,
              fontWeight:    500,
              letterSpacing: 0.2,
            }}>
              Last captured
            </span>
            <span style={{
              fontSize:      22,
              fontWeight:    700,
              color:         tokens.fg,
              fontFamily:    tokens.font,
              letterSpacing: -0.4,
            }}>
              {relativeTime(lastNote.updated_at)}
            </span>
          </div>
        )}
      </div>

      {/* ── Folder stacks — independent canvas element, GSAP fade+drift on scroll ──
          left:0 + paddingLeft:L so backing cards can fan left into the margin zone.
          NO shared wrapper with pin strip — keeps them from blocking each other.  */}
      {!isLoading && folders.length > 0 && (
        <div
          ref={folderStacksRef}
          style={{
            position:    'absolute',
            top:         FOLDER_TOP,
            left:        0,
            right:       0,
            overflow:    'visible',
            paddingLeft: L,
            zIndex:      15,
          }}
        >
          <FolderStacksRow
            folders={folders}
            activeCategory={activeCategory}
            onSelect={setActiveCategory}
          />
        </div>
      )}

      {/* Skeleton stacks */}
      {isLoading && (
        <div style={{
          position:    'absolute',
          top:         FOLDER_TOP,
          left:        0,
          paddingLeft: L,
          display:     'flex',
          gap:         120,
          overflow:    'visible',
          zIndex:      15,
        }}>
          {[0, 1, 2].map(i => (
            <TrumpetSkeleton key={i} width={STACK_W} height={STACK_CONTAINER_H} radius={20} />
          ))}
        </div>
      )}

      {/* ── Pin strip — separate canvas element, fades in as stacks collapse ──
          Has its own solid background so scrolled notes don't bleed through.
          Positioned independently at FOLDER_PIN_TOP — never blocks the grid.   */}
      {!isLoading && folders.length > 0 && (
        <FolderPinStrip
          ref={pinStripRef}
          folders={folders}
          activeCategory={activeCategory}
          onSelect={setActiveCategory}
        />
      )}

      {/* ── Notes grid (scrollable) — top is GSAP-scrubbed upward on scroll ──
          overflow-anchor:none prevents the browser from adjusting scrollTop when
          the dynamic spacer height changes, which would cause visible jumps.      */}
      <div
        ref={gridRef}
        onScroll={handleGridScroll}
        style={{
          position:       'absolute',
          top:            LABEL_TOP,
          left:           L,
          right:          R,
          bottom:         GRID_BOTTOM,
          overflowY:      'auto',
          overflowAnchor: 'none',
          scrollbarWidth: 'none',
          zIndex:         10,
        }}
      >
        {/* Dynamic spacer — height tracks scrollTop (capped at COLLAPSE_AT).
            Combined with the grid top animation, this keeps the first visible
            card anchored at grid_top+label_h throughout the collapse scroll.    */}
        <div ref={spacerRef} style={{ height: 0, flexShrink: 0 }} />

        {!isLoading && (
          <>
            {/* ── Section label ── */}
            <div style={{
              display:      'flex',
              alignItems:   'center',
              gap:          14,
              paddingTop:   8,
              marginBottom: 24,
            }}>
              <span style={{
                fontSize:      20,
                fontWeight:    700,
                letterSpacing: -0.4,
                color:         tokens.fg,
                fontFamily:    tokens.font,
                opacity:       0.9,
              }}>
                {activeCategory ? activeCfg!.label : 'Recent Notes'}
              </span>
              <span style={{
                fontSize:   13,
                fontWeight: 500,
                color:      tokens.muted,
                fontFamily: tokens.font,
              }}>
                {filteredNotes.length} {filteredNotes.length === 1 ? 'note' : 'notes'}
              </span>
              {activeCategory && (
                <button
                  onClick={() => setActiveCategory(null)}
                  style={{
                    display:      'flex',
                    alignItems:   'center',
                    gap:          5,
                    padding:      '4px 11px',
                    borderRadius: 99,
                    background:   'var(--trumpet-surface)',
                    border:       '1px solid var(--trumpet-edge-strong)',
                    color:        tokens.muted,
                    fontSize:     12,
                    fontWeight:   600,
                    fontFamily:   tokens.font,
                    cursor:       'pointer',
                    marginLeft:   'auto',
                  }}
                >
                  {activeCfg?.emoji} clear ×
                </button>
              )}
            </div>

            {filteredNotes.length === 0 ? (
              <div style={{ paddingTop: 32, fontSize: 18, color: tokens.muted, fontFamily: tokens.font }}>
                No notes yet{activeCategory ? ' in this category' : ''}. Ask Trumpet to create one.
              </div>
            ) : (
              <div style={{
                display:             'grid',
                gridTemplateColumns: 'repeat(3, 1fr)',
                gap:                 24,
              }}>
                {filteredNotes.map(note => (
                  <StickyNote key={note.id} note={note} onClick={() => setSelectedNote(note)} />
                ))}
              </div>
            )}

            {isLoading && <GridSkeleton />}
          </>
        )}
      </div>

      {/* ── Note editor overlay (slides in from right) ── */}
      <AnimatePresence>
        {selectedNote && (
          <NoteEditor
            key={selectedNote.id}
            note={selectedNote}
            onClose={handleClose}
          />
        )}
      </AnimatePresence>

      <style>{`
        @keyframes fadeSlideIn {
          from { opacity: 0; transform: translateY(-4px); }
          to   { opacity: 1; transform: translateY(0);    }
        }
      `}</style>
    </>
  );
}
