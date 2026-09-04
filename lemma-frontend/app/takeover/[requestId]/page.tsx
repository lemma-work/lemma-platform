'use client';

import { use } from 'react';

import { BrowserTakeover } from '@/components/workspace/browser-takeover';

/**
 * Driving the agent's browser yourself, for as long as it takes to log in.
 *
 * Deliberately **not** under `/pod/` — a sandbox is one machine per person, and
 * nothing about this page belongs to a pod. It is reached from a link the agent
 * sends, which is why the whole page is built around saying, unambiguously,
 * which site you are about to type into.
 */
export default function TakeoverPage({
    params,
}: {
    params: Promise<{ requestId: string }>;
}) {
    const { requestId } = use(params);

    return <BrowserTakeover requestId={requestId} />;
}
