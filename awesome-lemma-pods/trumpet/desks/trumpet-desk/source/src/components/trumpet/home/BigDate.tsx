import * as React from 'react';
import { tokens } from '@/lib/tokens';
import { currentDateNum, formatDateStamp } from '@/lib/time';
import { useNow } from '@/hooks/useNow';

export function BigDate() {
  useNow(); // re-render at midnight
  const num   = currentDateNum();
  const stamp = formatDateStamp();

  return (
    <>
      {/* Date number */}
      <div style={{
        fontSize:      196,
        fontWeight:    800,
        lineHeight:    0.8,
        letterSpacing: -6,
        color:         tokens.fg,
        display:       'inline-flex',
        alignItems:    'flex-end',
        fontFamily:    tokens.font,
      }}>
        {num}
        <span style={{
          width:        26,
          height:       26,
          borderRadius: '50%',
          background:   tokens.accent,
          marginLeft:   6,
          marginBottom: 16,
          flexShrink:   0,
        }} />
      </div>

      {/* Datestamp top-right */}
      <div style={{
        position:      'absolute',
        top:           56,
        right:         60,
        textAlign:     'right',
        lineHeight:    1.18,
        color:         '#9a958c',
        fontWeight:    600,
        fontSize:      25,
        letterSpacing: 0.2,
        fontFamily:    tokens.font,
      }}>
        {stamp.line1}
        <br />
        <span style={{ color: '#6e6a62' }}>{stamp.line2}</span>
      </div>

      {/* Corner dot */}
      <div style={{
        position:     'absolute',
        top:           54,
        left:          56,
        width:         8,
        height:        8,
        borderRadius: '50%',
        background:   '#4a4844',
      }} />
    </>
  );
}
