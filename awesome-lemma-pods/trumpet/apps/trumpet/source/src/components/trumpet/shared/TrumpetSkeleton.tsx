import * as React from 'react';

interface SkeletonProps {
  width?:  number | string;
  height?: number | string;
  radius?: number;
  style?:  React.CSSProperties;
}

export function TrumpetSkeleton({ width = '100%', height = 20, radius = 8, style }: SkeletonProps) {
  return (
    <div style={{
      width,
      height,
      borderRadius:  radius,
      background:    'var(--trumpet-chip-bg)',
      animation:     'trumpet-pulse 1.5s ease-in-out infinite',
      ...style,
    }} />
  );
}

// Inject keyframe once
const KEYFRAME_ID = '__trumpet_pulse_kf';
if (typeof document !== 'undefined' && !document.getElementById(KEYFRAME_ID)) {
  const s = document.createElement('style');
  s.id = KEYFRAME_ID;
  s.textContent = `
    @keyframes trumpet-pulse {
      0%, 100% { opacity: 0.4; }
      50%       { opacity: 0.8; }
    }
  `;
  document.head.appendChild(s);
}
