'use client';

import { use } from 'react';

import { SignInEmbed } from '@/components/workspace/sign-in-embed';

/**
 * Where the link in "please sign in to this site" lands.
 *
 * Inside `(dashboard)`, so it inherits `ProtectedRoute`. That matters more here
 * than anywhere: the whole point is that somebody opens this on a phone, from a
 * message, quite possibly signed out — and the previous version, which sat
 * outside the shell with no guard, told them the request had expired or was not
 * theirs. Which was a lie, twice over.
 *
 * The id in the URL grants nothing. Every call resolves it against the caller's
 * own session, so a forwarded link answers exactly as an invented one does.
 *
 * This route is for arriving from *outside* a conversation — Slack, Telegram,
 * email. Clicking the card inside the app opens the same sign-in in the
 * computer panel instead, without taking the conversation away; both render
 * `SignInEmbed`, so the browser and the answer controls are the same either way.
 */
export default function SignInToSitePage({
    params,
}: {
    // Addressed by the pause it is for. There is no request id because there is
    // no request row: the paused tool call carries the origin and the reason,
    // and whether it is still unresolved is what "waiting" means.
    params: Promise<{ conversationId: string; toolCallId: string }>;
}) {
    const { conversationId, toolCallId } = use(params);

    return (
        <div className="mx-auto flex h-full w-full max-w-[1100px] flex-col p-4">
            <SignInEmbed
                conversationId={conversationId}
                toolCallId={toolCallId}
                variant="page"
            />
        </div>
    );
}
