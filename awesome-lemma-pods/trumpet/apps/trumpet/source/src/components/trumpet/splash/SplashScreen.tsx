import * as React from 'react';
import gsap from 'gsap';

interface Props {
  onDone: () => void;
}

const CANVAS_W = 1512;
const CANVAS_H = 1008;

export function SplashScreen({ onDone }: Props) {
  const overlayRef = React.useRef<HTMLDivElement>(null);
  const canvasRef  = React.useRef<HTMLDivElement>(null);

  // Scale canvas to fit viewport (same formula as Stage)
  React.useLayoutEffect(() => {
    function fit() {
      const c = canvasRef.current;
      if (!c) return;
      const s = Math.min(window.innerWidth / CANVAS_W, window.innerHeight / CANVAS_H);
      c.style.transform = `scale(${s})`;
    }
    fit();
    window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
  }, []);

  React.useEffect(() => {
    const root = canvasRef.current;
    if (!root) return;

    // Set initial hidden state
    gsap.set('#spl-q-tl', { x: -756, y: -504 });
    gsap.set('#spl-q-tr', { x:  756, y: -504 });
    gsap.set('#spl-q-bl', { x: -756, y:  504 });
    gsap.set('#spl-q-br', { x:  756, y:  504 });
    gsap.set('#spl-div-h, #spl-div-v', { autoAlpha: 0 });
    gsap.set('.spl-q-name, .spl-q-sub', { autoAlpha: 0 });
    gsap.set('#spl-glow',   { autoAlpha: 0, scale: 0 });
    gsap.set('#spl-center', { xPercent: -50, yPercent: -50, autoAlpha: 0, scale: 0 });
    gsap.set('#spl-tagline', { autoAlpha: 0, y: 14 });
    gsap.set('#spl-brand',   { autoAlpha: 0 });

    // Preload all GIFs before animating
    const imgs = Array.from(root.querySelectorAll('img'));
    const loaded = imgs.map(img =>
      img.complete && img.naturalWidth > 0
        ? Promise.resolve()
        : new Promise<void>(resolve => {
            img.addEventListener('load',  () => resolve(), { once: true });
            img.addEventListener('error', () => resolve(), { once: true });
          })
    );

    let cancelled = false;
    let tl: gsap.core.Timeline;

    Promise.all(loaded).then(() => {
      if (cancelled) return;
      tl = gsap.timeline({ delay: 0.3 });

      // SLAM IN
      tl.to('#spl-q-tl', { x: 0, y: 0, duration: 0.62, ease: 'expo.out' }, 0);
      tl.to('#spl-q-tr', { x: 0, y: 0, duration: 0.62, ease: 'expo.out' }, 0.08);
      tl.to('#spl-q-bl', { x: 0, y: 0, duration: 0.62, ease: 'expo.out' }, 0.16);
      tl.to('#spl-q-br', { x: 0, y: 0, duration: 0.62, ease: 'expo.out' }, 0.24);

      // Dividers
      tl.to('#spl-div-h, #spl-div-v', { autoAlpha: 1, duration: 0.12, ease: 'none' }, 0.44);

      // Section names
      tl.to('#spl-q-tl .spl-q-name', { autoAlpha: 1, duration: 0.32, ease: 'power2.out' }, 0.72);
      tl.to('#spl-q-tr .spl-q-name', { autoAlpha: 1, duration: 0.32, ease: 'power2.out' }, 0.80);
      tl.to('#spl-q-bl .spl-q-name', { autoAlpha: 1, duration: 0.32, ease: 'power2.out' }, 0.88);
      tl.to('#spl-q-br .spl-q-name', { autoAlpha: 1, duration: 0.32, ease: 'power2.out' }, 0.96);

      // Sub-labels
      tl.to('#spl-q-tl .spl-q-sub', { autoAlpha: 1, duration: 0.28, ease: 'power2.out' }, 0.88);
      tl.to('#spl-q-tr .spl-q-sub', { autoAlpha: 1, duration: 0.28, ease: 'power2.out' }, 0.96);
      tl.to('#spl-q-bl .spl-q-sub', { autoAlpha: 1, duration: 0.28, ease: 'power2.out' }, 1.04);
      tl.to('#spl-q-br .spl-q-sub', { autoAlpha: 1, duration: 0.28, ease: 'power2.out' }, 1.12);

      // BREATHE — panels stay visible with GIF loops

      // COLLAPSE — each panel's center converges to canvas center
      tl.to('#spl-q-tl', { x:  378, y:  252, scale: 0.04, autoAlpha: 0, duration: 0.52, ease: 'power3.in' }, 2.5);
      tl.to('#spl-q-tr', { x: -378, y:  252, scale: 0.04, autoAlpha: 0, duration: 0.52, ease: 'power3.in' }, 2.5);
      tl.to('#spl-q-bl', { x:  378, y: -252, scale: 0.04, autoAlpha: 0, duration: 0.52, ease: 'power3.in' }, 2.5);
      tl.to('#spl-q-br', { x: -378, y: -252, scale: 0.04, autoAlpha: 0, duration: 0.52, ease: 'power3.in' }, 2.5);
      tl.to('#spl-div-h, #spl-div-v', { autoAlpha: 0, duration: 0.28, ease: 'power2.in' }, 2.5);

      // Glow blooms
      tl.to('#spl-glow', { autoAlpha: 1, scale: 1, duration: 0.55, ease: 'power2.out' }, 2.98);

      // Mr Toot pops in
      tl.to('#spl-center', { autoAlpha: 1, scale: 1, duration: 0.52, ease: 'back.out(1.7)' }, 3.04);

      // Tagline
      tl.to('#spl-tagline', { autoAlpha: 1, y: 0, duration: 0.40, ease: 'power2.out' }, 3.46);

      // Brand
      tl.to('#spl-brand', { autoAlpha: 1, duration: 0.32, ease: 'power2.out' }, 3.72);

      // Hold for 0.8s, then dissolve the overlay
      tl.to(overlayRef.current, { autoAlpha: 0, duration: 0.5, ease: 'power2.inOut' }, 4.6);
      tl.call(onDone, [], 5.1);
    });

    return () => { cancelled = true; tl?.kill(); };
  }, [onDone]);

  return (
    <div
      ref={overlayRef}
      style={{
        position: 'fixed',
        inset: 0,
        background: '#0b0a0a',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 9999,
        overflow: 'hidden',
        fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
      }}
    >
      <div
        ref={canvasRef}
        style={{
          position: 'relative',
          width: CANVAS_W,
          height: CANVAS_H,
          flexShrink: 0,
          overflow: 'hidden',
          transformOrigin: 'center center',
        }}
      >
        {/* Dividers */}
        <div id="spl-div-h" style={{ position: 'absolute', top: 504, left: 0, width: 1512, height: 2, background: 'rgba(0,0,0,0.18)', zIndex: 5, pointerEvents: 'none' }} />
        <div id="spl-div-v" style={{ position: 'absolute', top: 0, left: 756, width: 2, height: 1008, background: 'rgba(0,0,0,0.18)', zIndex: 5, pointerEvents: 'none' }} />

        {/* TL: Schedule — average */}
        <div id="spl-q-tl" style={{ ...quad, background: '#cfe7cf' }}>
          <img src="/mascot/trumpet-average-stable-v2-loop2.gif" alt="" style={gifStyle} />
          <div className="spl-q-name" style={nameStyle}>Schedule</div>
          <div className="spl-q-sub" style={subStyle}>events · habits</div>
        </div>

        {/* TR: Commits — overwhelmed */}
        <div id="spl-q-tr" style={{ ...quad, left: 756, background: '#f6d775' }}>
          <img src="/mascot/trumpet-overwhelmed-stable-v2-loop2.gif" alt="" style={gifStyle} />
          <div className="spl-q-name" style={nameStyle}>Commits</div>
          <div className="spl-q-sub" style={subStyle}>what you owe · what&apos;s owed</div>
        </div>

        {/* BL: Notes — chill */}
        <div id="spl-q-bl" style={{ ...quad, top: 504, background: '#d9cef0' }}>
          <img src="/mascot/trumpet-chill-stable-v2-loop2.gif" alt="" style={gifStyle} />
          <div className="spl-q-name" style={nameStyle}>Notes</div>
          <div className="spl-q-sub" style={subStyle}>thoughts · ideas · context</div>
        </div>

        {/* BR: People — chill */}
        <div id="spl-q-br" style={{ ...quad, top: 504, left: 756, background: '#d8d4cc' }}>
          <img src="/mascot/trumpet-chill-stable-v2-loop2.gif" alt="" style={gifStyle} />
          <div className="spl-q-name" style={nameStyle}>People</div>
          <div className="spl-q-sub" style={subStyle}>contacts · relationships</div>
        </div>

        {/* Glow */}
        <div id="spl-glow" style={{
          position: 'absolute', top: '50%', left: '50%',
          transform: 'translate(-50%,-50%)',
          width: 700, height: 700, borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(239,233,219,0.09) 0%, transparent 68%)',
          zIndex: 10, pointerEvents: 'none',
        }} />

        {/* Center reveal */}
        <div id="spl-center" style={{
          position: 'absolute', top: '50%', left: '50%',
          display: 'flex', flexDirection: 'column', alignItems: 'center',
          zIndex: 20, textAlign: 'center', pointerEvents: 'none',
        }}>
          <img
            src="/mascot/trumpet-average-stable-v2-loop2.gif"
            alt=""
            style={{ width: 390, height: 390, objectFit: 'contain', filter: 'drop-shadow(0 28px 60px rgba(0,0,0,0.7))' }}
          />
          <div id="spl-tagline" style={{
            fontSize: 38, fontWeight: 700, color: '#f3efe6',
            letterSpacing: '-0.5px', lineHeight: 1.45, marginTop: 16,
          }}>
            everything that matters,<br />in one place.
          </div>
          <div id="spl-brand" style={{
            fontSize: 15, fontWeight: 700, letterSpacing: 6,
            textTransform: 'uppercase', color: 'rgba(243,239,230,0.5)', marginTop: 24,
          }}>
            Trumpet
          </div>
        </div>
      </div>
    </div>
  );
}

const quad: React.CSSProperties = {
  position: 'absolute',
  top: 0,
  left: 0,
  width: 756,
  height: 504,
  overflow: 'hidden',
};

const gifStyle: React.CSSProperties = {
  position: 'absolute',
  width: 270,
  height: 270,
  objectFit: 'contain',
  left: '50%',
  top: '44%',
  transform: 'translate(-50%, -50%)',
};

const nameStyle: React.CSSProperties = {
  position: 'absolute',
  bottom: 40,
  left: 40,
  fontSize: 66,
  fontWeight: 800,
  letterSpacing: -2,
  lineHeight: 1,
  color: '#1a1815',
};

const subStyle: React.CSSProperties = {
  position: 'absolute',
  bottom: 16,
  left: 43,
  fontSize: 17,
  fontWeight: 600,
  letterSpacing: 0.1,
  color: 'rgba(26,24,21,0.52)',
};
