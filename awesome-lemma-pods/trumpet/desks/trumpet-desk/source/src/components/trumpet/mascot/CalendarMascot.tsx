import * as React from 'react';
import gsap from 'gsap';
import { getCalendarMood } from '@/lib/calendar-mood';
import type { CalendarMood } from '@/lib/calendar-mood';
import { tokens } from '@/lib/tokens';
import type { ScheduleItem, ScheduleStatus } from '@/hooks/useSchedule';

interface CalendarMascotProps {
  items: ScheduleItem[];
  status: ScheduleStatus;
  style?: React.CSSProperties;
}

export function CalendarMascot({ items, status, style }: CalendarMascotProps) {
  const rootRef = React.useRef<HTMLDivElement>(null);
  const mascotRef = React.useRef<HTMLImageElement>(null);
  const bubbleRef = React.useRef<HTMLDivElement>(null);
  const textRef = React.useRef<HTMLSpanElement>(null);
  const mood = getCalendarMood(items);
  const mascot = MASCOT_BY_MOOD[mood];
  const dialogue = mascot.dialogue;

  React.useLayoutEffect(() => {
    if (status === 'loading') return;

    const ctx = gsap.context(() => {
      const tl = gsap.timeline({ defaults: { force3D: true } });

      tl.fromTo(
        mascotRef.current,
        { autoAlpha: 0, y: 18, scale: 0.92 },
        { autoAlpha: 1, y: 0, scale: 1, duration: 0.34, ease: 'back.out(1.7)' },
      );

      tl.fromTo(
        bubbleRef.current,
        { autoAlpha: 0, scale: 0.72, x: -10, y: 12, transformOrigin: '0% 100%' },
        { autoAlpha: 1, scale: 1, x: 0, y: 0, duration: 0.24, ease: 'back.out(2.3)' },
        '-=0.1',
      );

      tl.fromTo(
        textRef.current,
        { clipPath: 'inset(0 100% 0 0)' },
        { clipPath: 'inset(0 0% 0 0)', duration: 0.54, ease: `steps(${dialogue.length})` },
        '-=0.03',
      );

      tl.to(bubbleRef.current, {
        y: -3,
        duration: 1.15,
        repeat: -1,
        yoyo: true,
        ease: 'sine.inOut',
      });
    }, rootRef);

    return () => ctx.revert();
  }, [dialogue, mood, status]);

  if (status === 'loading') return null;

  return (
    <div
      ref={rootRef}
      aria-label={`${mascot.label} mascot`}
      style={{
        position: 'absolute',
        left: 76,
        bottom: 100,
        width: 660,
        height: 360,
        pointerEvents: 'none',
        zIndex: 45,
        ...style,
      }}
    >
      <div ref={bubbleRef} style={bubbleStyle}>
        <span ref={textRef} style={textStyle}>
          {dialogue}
        </span>
        <span style={bubbleTailStyle} />
      </div>
      <img
        key={mood}
        ref={mascotRef}
        src={mascot.src}
        alt=""
        aria-hidden="true"
        style={{
          position: 'absolute',
          left: 46,
          bottom: -8,
          width: 340,
          height: 340,
          objectFit: 'contain',
          filter: 'drop-shadow(0 18px 22px rgba(0,0,0,0.22))',
        }}
      />
    </div>
  );
}

const bubbleStyle: React.CSSProperties = {
  position: 'absolute',
  left: 330,
  bottom: 206,
  width: 260,
  minHeight: 64,
  padding: '12px 14px',
  background: '#fff7d7',
  color: tokens.ink,
  border: `3px solid ${tokens.ink}`,
  boxShadow: `6px 6px 0 ${tokens.ink}`,
  fontFamily: "'Nunito', ui-monospace, SFMono-Regular, Menlo, monospace",
  fontSize: 17,
  fontWeight: 800,
  lineHeight: 1.1,
  letterSpacing: 0,
  textTransform: 'lowercase',
};

const MASCOT_BY_MOOD: Record<CalendarMood, { label: string; dialogue: string; src: string }> = {
  chill: {
    label: 'Chill day',
    dialogue: 'should have some things to do',
    src: '/mascot/trumpet-chill-stable-v2-loop2.gif',
  },
  average: {
    label: 'Average day',
    dialogue: 'just enough time to squeeze in squats',
    src: '/mascot/trumpet-average-stable-v2-loop2.gif',
  },
  overwhelmed: {
    label: 'Overwhelmed day',
    dialogue: 'looks like we gotta take care of some of these folks',
    src: '/mascot/trumpet-overwhelmed-stable-v2-loop2.gif',
  },
};

const textStyle: React.CSSProperties = {
  display: 'inline-block',
  overflow: 'hidden',
};

const bubbleTailStyle: React.CSSProperties = {
  position: 'absolute',
  left: -17,
  bottom: 16,
  width: 18,
  height: 18,
  background: '#fff7d7',
  borderBottom: `3px solid ${tokens.ink}`,
  borderLeft: `3px solid ${tokens.ink}`,
  transform: 'rotate(45deg)',
};
