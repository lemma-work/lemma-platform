import * as React from 'react';
import { client } from '@/lib/client';
import { tokens } from '@/lib/tokens';
import { getOrgId } from '@/lib/org';

interface ConnectBannerProps {
  appName:  string;
  appId:    string;
  message?: string;
}

export function ConnectBanner({ appName, appId, message }: ConnectBannerProps) {
  const handleConnect = React.useCallback(async () => {
    try {
      const orgId = await getOrgId();
      const req = await client.integrations.createConnectRequest(orgId, appId);
      if (req.authorization_url) window.open(req.authorization_url, '_blank');
    } catch { /* ignore */ }
  }, [appId]);

  return (
    <div style={{
      display:       'flex',
      flexDirection: 'column',
      alignItems:    'flex-start',
      gap:           12,
      padding:       '18px 20px',
      background:    'rgba(0,0,0,0.06)',
      borderRadius:  16,
      border:        '1.5px dashed rgba(0,0,0,0.18)',
      marginTop:     14,
    }}>
      <p style={{
        fontSize:   18,
        fontWeight: 600,
        color:      tokens.inkSoft,
        margin:     0,
        lineHeight: 1.4,
      }}>
        {message || `Connect ${appName} to see your events here.`}
      </p>
      <button
        onClick={() => void handleConnect()}
        style={{
          fontSize:     16,
          fontWeight:   700,
          color:        tokens.ink,
          background:   'rgba(0,0,0,0.09)',
          border:       'none',
          borderRadius: 10,
          padding:      '8px 16px',
          cursor:       'pointer',
          fontFamily:   tokens.font,
        }}
      >
        Connect {appName} →
      </button>
    </div>
  );
}
