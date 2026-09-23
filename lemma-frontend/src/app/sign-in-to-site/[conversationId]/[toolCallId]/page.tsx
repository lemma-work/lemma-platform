import { SignInHost } from "../../sign-in-host";

/** Where the link in "please sign in to this site" lands.
 *
 *  Addressed by the pause it is for. There is no request id because there is
 *  no request row: the paused tool call carries the site and the reason, and
 *  whether it is still unresolved is what "waiting" means.
 *
 *  Top-level rather than inside `/t`, because this is not a place in the
 *  workspace — it is one errand, arrived at from a message, finished, and
 *  closed. Mounting the workspace to show it would cold-start every open app
 *  tab behind a dialog somebody is about to leave.
 *
 *  Clicking the card inside a conversation opens the same sign-in without
 *  taking the conversation away; both render `SignInPane`, so the browser and
 *  the answer controls are the same either way. That matters more than it
 *  looks: answering is the only route back to the paused run, and a surface
 *  that had the browser but not the buttons would let somebody sign in and
 *  leave the teammate waiting for ever.
 */
export default async function SignInToSiteRoute({ params }: {
    params: Promise<{ conversationId: string; toolCallId: string }>;
}) {
    const { conversationId, toolCallId } = await params;
    return <SignInHost conversationId={conversationId} toolCallId={toolCallId} />;
}
