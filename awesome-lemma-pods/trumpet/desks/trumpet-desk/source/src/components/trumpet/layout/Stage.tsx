/**
 * Stage — full-screen dark background with a responsive 1512×1008 canvas
 * centred inside it. All Trumpet content lives inside the canvas.
 */
import * as React from 'react';
import { CANVAS_W, CANVAS_H, tokens } from '@/lib/tokens';

interface StageProps {
  children: React.ReactNode;
}

export function Stage({ children }: StageProps) {
  const [scale, setScale] = React.useState(1);

  React.useEffect(() => {
    const fit = () => {
      const w = window.innerWidth  || CANVAS_W;
      const h = window.innerHeight || CANVAS_H;
      setScale(Math.max(0.25, Math.min(w / CANVAS_W, h / CANVAS_H)));
    };
    fit();
    // Two-pass to handle font / layout shifts on first load
    requestAnimationFrame(fit);
    const to = setTimeout(fit, 120);
    window.addEventListener('resize', fit);
    return () => { clearTimeout(to); window.removeEventListener('resize', fit); };
  }, []);

  return (
    <div style={{
      position:        'fixed',
      inset:           0,
      background:      `radial-gradient(120% 120% at 78% 30%, var(--trumpet-bg2) 0%, var(--trumpet-bg) 55%)`,
      display:         'flex',
      alignItems:      'center',
      justifyContent:  'center',
      overflow:        'hidden',
    }}>
      <div style={{
        position:        'relative',
        width:           CANVAS_W,
        height:          CANVAS_H,
        transform:       `scale(${scale})`,
        transformOrigin: 'center center',
        flexShrink:      0,
        overflow:        'hidden',
      }}>
        {children}
      </div>
    </div>
  );
}
