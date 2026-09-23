'use client';

import { useMemo } from 'react';
import { useAuth } from 'lemma-sdk/react';
import { getLemmaClient } from '@/lib/sdk/lemma-client';

export function useLemmaAuth() {
    const client = useMemo(() => getLemmaClient(), []);
    return useAuth(client);
}
