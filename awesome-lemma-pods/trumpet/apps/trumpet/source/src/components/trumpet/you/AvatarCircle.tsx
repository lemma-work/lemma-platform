import * as React from 'react';
import { tokens } from '@/lib/tokens';
import { resolveFileUrl, isPodPath } from '@/lib/fileUrl';

const GRADIENTS = [
  'linear-gradient(135deg,#6366f1,#8b5cf6)',
  'linear-gradient(135deg,#f59e0b,#ef4444)',
  'linear-gradient(135deg,#10b981,#3b82f6)',
  'linear-gradient(135deg,#ec4899,#8b5cf6)',
  'linear-gradient(135deg,#f97316,#eab308)',
];
function hashName(n: string) { return n.split('').reduce((a,c) => a + c.charCodeAt(0), 0); }

interface AvatarCircleProps {
  src?:      string;
  name:      string;
  size:      number;
  style?:    React.CSSProperties;
  onUpload?: (file: File) => Promise<void>;
}

export function AvatarCircle({ src, name, size, style, onUpload }: AvatarCircleProps) {
  const [resolvedSrc, setResolvedSrc] = React.useState<string | undefined>(
    src && !isPodPath(src) ? src : undefined
  );
  const [imgFailed,  setImgFailed]  = React.useState(false);
  const [hovered,    setHovered]    = React.useState(false);
  const [uploading,  setUploading]  = React.useState(false);
  const fileInputRef = React.useRef<HTMLInputElement>(null);

  // Resolve pod paths to blob URLs
  React.useEffect(() => {
    if (!src) { setResolvedSrc(undefined); return; }
    if (!isPodPath(src)) { setResolvedSrc(src); setImgFailed(false); return; }
    let live = true;
    resolveFileUrl(src).then(url => { if (live) { setResolvedSrc(url); setImgFailed(false); } });
    return () => { live = false; };
  }, [src]);

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !onUpload) return;
    e.target.value = '';
    setUploading(true);
    try { await onUpload(file); } finally { setUploading(false); }
  };

  const hasSrc    = !!resolvedSrc && !imgFailed;
  const initials  = name.split(' ').map(w => w[0]).filter(Boolean).join('').slice(0,2).toUpperCase();
  const bg        = GRADIENTS[hashName(name) % GRADIENTS.length];
  const isPlaceholder = resolvedSrc && !resolvedSrc.startsWith('/pod/') && resolvedSrc.startsWith('/avatars/avatar');
  const clickable = !!onUpload;

  return (
    <div
      style={{ position: 'relative', display: 'inline-flex', flexShrink: 0 }}
      onMouseEnter={() => clickable && setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <div
        onClick={() => clickable && fileInputRef.current?.click()}
        style={{
          width:          size,
          height:         size,
          borderRadius:   '50%',
          overflow:       'hidden',
          flexShrink:     0,
          background:     hasSrc ? 'var(--trumpet-bg2)' : bg,
          border:         '2px solid var(--trumpet-avatar-ring)',
          display:        'flex',
          alignItems:     'center',
          justifyContent: 'center',
          cursor:         clickable ? 'pointer' : 'default',
          transition:     'opacity 0.15s',
          opacity:        uploading ? 0.5 : 1,
          ...style,
        }}
      >
        {hasSrc ? (
          <img
            src={resolvedSrc}
            alt={name}
            onError={() => setImgFailed(true)}
            style={{
              width:          '100%',
              height:         '100%',
              objectFit:      isPlaceholder ? 'contain' : 'cover',
              objectPosition: isPlaceholder ? 'bottom center' : 'top',
            }}
          />
        ) : (
          <span style={{
            fontSize:   size * 0.36,
            fontWeight: 700,
            color:      'rgba(255,255,255,0.9)',
            fontFamily: tokens.font,
            lineHeight: 1,
            userSelect: 'none',
          }}>
            {initials}
          </span>
        )}
      </div>

      {/* Camera overlay on hover */}
      {clickable && (hovered || uploading) && (
        <div
          onClick={() => fileInputRef.current?.click()}
          style={{
            position:       'absolute',
            inset:          0,
            borderRadius:   '50%',
            background:     'rgba(0,0,0,0.45)',
            display:        'flex',
            alignItems:     'center',
            justifyContent: 'center',
            cursor:         'pointer',
            pointerEvents:  uploading ? 'none' : 'auto',
          }}
        >
          {uploading ? (
            <svg width={size * 0.32} height={size * 0.32} viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2">
              <circle cx="12" cy="12" r="10" strokeOpacity="0.3"/>
              <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round">
                <animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.8s" repeatCount="indefinite"/>
              </path>
            </svg>
          ) : (
            <svg width={size * 0.32} height={size * 0.32} viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round">
              <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
              <circle cx="12" cy="13" r="4"/>
            </svg>
          )}
        </div>
      )}

      {/* Hidden file input */}
      {clickable && (
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          style={{ display: 'none' }}
          onChange={handleFileChange}
        />
      )}
    </div>
  );
}
