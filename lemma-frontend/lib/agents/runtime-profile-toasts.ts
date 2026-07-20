/**
 * Pure-logic helpers for the toast messages emitted by the models-settings
 * daemon-add flow. Kept here (not inline in the component) so they're easy to
 * unit test in isolation and so the caller can swap the wording without
 * re-rendering React.
 *
 * Both helpers run synchronously on a single string and have no React
 * dependencies — that keeps the test surface small and avoids pulling the
 * component tree (which mixes lucide-react, sonner, react-query) into unit
 * tests.
 */
import { RuntimeProfileScope } from 'lemma-sdk';

/**
 * Build the user-facing error toast for a failed daemon-add request.
 *
 * The backend rejects Workspace-scope adds by non-editor members with a
 * ``Missing permission org.update`` 403; we surface an actionable hint instead
 * of the raw wire error so the operator knows whether to swap scope, ask an
 * admin, or escalate. Anything else falls back to the actual message.
 */
export function formatAddError(
    displayName: string,
    scope: RuntimeProfileScope,
    message: string,
): string {
    if (scope === RuntimeProfileScope.ORGANIZATION && /org\.update/i.test(message)) {
        return `${displayName}: Workspace connections need org.update — ask an org admin to grant editor access, or choose Personal.`;
    }
    return `Couldn't add ${displayName}: ${message}`;
}

/**
 * Build the inline note appended to the success toast when the optional
 * "make this the default for [pod]" step fails after the profile was already
 * saved. The profile is non-rollbackable at this point, so the message just
 * tells the operator how to finish the promotion (most commonly: they lack
 * ``pod.update`` / ``org.update``, so the action needs Pod Editor access).
 */
export function friendlyPodDefaultError(message: string): string {
    if (/pod\.update/i.test(message) || /org\.update/i.test(message)) {
        return 'needs Pod Editor access';
    }
    return message;
}
