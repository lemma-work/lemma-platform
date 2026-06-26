import * as React from 'react';
import { useCurrentUser } from 'lemma-sdk/react';
import { client } from '@/lib/client';

const LS_AVATAR_KEY = 'trumpet-me-avatar';

export function useProfile() {
  const { user, isLoading, refresh } = useCurrentUser({ client, autoLoad: true });
  const [localAvatar, setLocalAvatar] = React.useState<string | undefined>(
    () => window.localStorage.getItem(LS_AVATAR_KEY) ?? undefined
  );

  const firstName = user?.first_name?.trim() || user?.email?.split('@')[0] || 'there';

  const u         = user as Record<string, unknown> | undefined;
  const apiAvatar = u?.avatar_url as string | undefined;
  const avatarUrl = localAvatar || apiAvatar;

  const uploadAvatar = React.useCallback(async (file: File) => {
    const ext    = file.name.slice(file.name.lastIndexOf('.'));
    const result = await client.files.upload(file, {
      directoryPath: '/pod/photos',
      name:          `me-avatar${ext}`,
    });
    window.localStorage.setItem(LS_AVATAR_KEY, result.path);
    setLocalAvatar(result.path);
    // also attempt to persist on the user profile (server may support it)
    try {
      await (client.request as Function)('POST', '/users/me/profile', {
        body: { avatar_url: result.path },
      });
    } catch {
      // server may not support avatar_url — local fallback is enough
    }
    await refresh();
  }, [refresh]);

  const updateName = React.useCallback(async (first: string, last: string) => {
    await client.users.upsertProfile({ first_name: first, last_name: last });
    await refresh();
  }, [refresh]);

  return { firstName, user, isLoading, refresh, avatarUrl, uploadAvatar, updateName };
}
