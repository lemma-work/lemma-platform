/**
 * Handing a pod an unfinished sentence instead of a sent message.
 *
 * `assistantMessage` was the only way to arrive at a pod with intent, and it
 * always fires: the layout reads it and sends immediately. That is right when
 * the user has already said what they want, and wrong every other time — it
 * spends a model turn on a sentence they never wrote, in a pod they have not
 * seen yet.
 *
 * A composer launch is the quiet version. `composerDraft` seeds the pod's own
 * composer and stops there, cursor at the end, while `conversationInstructions`
 * waits with it and rides along on whatever the user eventually sends. It never
 * survives that first message: background framing for the build is not standing
 * policy for the pod.
 */

export const COMPOSER_DRAFT_PARAM = "composerDraft";
export const CONVERSATION_INSTRUCTIONS_PARAM = "conversationInstructions";
export const CONVERSATION_METADATA_PARAM = "conversationMetadata";

export const COMPOSER_LAUNCH_PARAMS = [
  COMPOSER_DRAFT_PARAM,
  CONVERSATION_INSTRUCTIONS_PARAM,
  CONVERSATION_METADATA_PARAM,
] as const;

export const ASSISTANT_MESSAGE_PARAM = "assistantMessage";

/**
 * The loud version's params. `assistantMessage` sends on arrival and the other
 * two are the framing that rides with it; all three are spent by that one send.
 */
export const ASSISTANT_LAUNCH_PARAMS = [
  ASSISTANT_MESSAGE_PARAM,
  CONVERSATION_INSTRUCTIONS_PARAM,
  CONVERSATION_METADATA_PARAM,
] as const;

export interface ComposerLaunch {
  /** An unfinished sentence for the user to complete. Never sent on its own. */
  draft: string;
  /** Background framing carried by the first message only. */
  instructions?: string;
  metadata?: Record<string, unknown>;
}

/**
 * Shared by every route that reads a conversation launch out of the URL.
 * Anything that is not a JSON object is dropped rather than guessed at — a
 * malformed param should cost the metadata, not the navigation.
 */
export function parseConversationMetadataParam(
  value: string | null,
): Record<string, unknown> | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

/** Pod home, with the composer already holding the start of a sentence. */
export function buildComposerLaunchHref(
  podId: string,
  launch: ComposerLaunch,
): string {
  const params = new URLSearchParams({
    [COMPOSER_DRAFT_PARAM]: launch.draft,
  });
  if (launch.instructions) {
    params.set(CONVERSATION_INSTRUCTIONS_PARAM, launch.instructions);
  }
  if (launch.metadata) {
    params.set(CONVERSATION_METADATA_PARAM, JSON.stringify(launch.metadata));
  }

  return `/pod/${encodeURIComponent(podId)}?${params.toString()}`;
}

/** Reads a launch back out of a pod-home URL. Null when there is nothing to seed. */
export function readComposerLaunch(
  params: URLSearchParams,
): ComposerLaunch | null {
  const draft = params.get(COMPOSER_DRAFT_PARAM);
  const instructions = params.get(CONVERSATION_INSTRUCTIONS_PARAM);
  if (!draft && !instructions) return null;

  return {
    draft: draft || "",
    instructions: instructions || undefined,
    metadata:
      parseConversationMetadataParam(params.get(CONVERSATION_METADATA_PARAM)) ??
      undefined,
  };
}

function withoutParams(
  params: URLSearchParams,
  remove: readonly string[],
): string {
  const next = new URLSearchParams(params.toString());
  for (const param of remove) next.delete(param);
  return next.toString();
}

/**
 * The same URL with the launch params removed, for the `router.replace` that
 * follows seeding. Without it a refresh re-seeds a draft the user already
 * cleared, and a second conversation inherits the first one's framing.
 */
export function stripComposerLaunchParams(params: URLSearchParams): string {
  return withoutParams(params, COMPOSER_LAUNCH_PARAMS);
}

/**
 * The same for a send-on-arrival launch, and it must run when the message is
 * dispatched rather than when the answer lands. A turn is minutes long: held
 * that whole time, the launch URL is still on screen for a reload to replay,
 * and the replace that finally clears it arrives wherever the reader has since
 * navigated to.
 */
export function stripAssistantLaunchParams(params: URLSearchParams): string {
  return withoutParams(params, ASSISTANT_LAUNCH_PARAMS);
}
