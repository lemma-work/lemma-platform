'use client';

import { ExternalLink } from '@/components/ui/icons';

import { StepLoader } from '@/components/brand/loader';
import { Button } from '@/components/ui/button';
import { useSlackManifest } from '@/lib/hooks/use-pod-surfaces';

/**
 * Slack hands you an app pre-built from a manifest, so nothing below has to be
 * copied by hand — not the callback URL, not the event URL, not the scopes.
 *
 * What a manifest cannot carry is credentials: Slack has no API that gives a
 * third party another app's client id, client secret or signing secret. They
 * exist only on the app's own Basic Information page. So the floor is one
 * click and three pastes, and this button removes everything above that floor.
 *
 * Lives on its own rather than inside the credential form it used to sit in:
 * making the app is what *produces* those credentials, so it is a peer of the
 * form, not a field in it — and it is the same offer wherever someone first
 * runs out of Slack accounts to pick, which is the surface modal as often as
 * the connector list. Nothing here is scoped to a pod, an org or an account,
 * so rendering it twice is not two of anything: the manifest describes the
 * deployment, and an org may run as many Slack apps as it likes.
 */
export function CreateSlackAppButton({ label = 'Make your Slack app' }: { label?: string }) {
    const { data: manifest, isLoading, isError } = useSlackManifest();

    if (isLoading) {
        return (
            <p className="flex items-center gap-2 text-xs text-[var(--text-tertiary)]">
                <StepLoader size="xs" /> Getting things ready…
            </p>
        );
    }
    if (isError || !manifest) {
        return (
            <p className="text-xs leading-5 text-[var(--text-secondary)]">
                Make your app at api.slack.com, then paste its details below. We can’t set
                it up for you here — Slack needs a web address it can reach, and this copy
                of Lemma doesn’t have one yet.
            </p>
        );
    }

    const href = `https://api.slack.com/apps?new_app=1&manifest_json=${encodeURIComponent(
        JSON.stringify(manifest),
    )}`;

    return (
        <div className="grid gap-1">
            <Button asChild size="sm" variant="secondary" className="w-fit">
                <a href={href} target="_blank" rel="noreferrer">
                    {label} <ExternalLink className="ml-1.5 h-3.5 w-3.5" />
                </a>
            </Button>
            <p className="text-xs leading-5 text-[var(--text-tertiary)]">
                Opens Slack with everything already filled in. Create the app, add it to
                your workspace, then copy the three values Slack shows you.
            </p>
        </div>
    );
}
