/**
 * Which agent a message addresses.
 *
 * The composer inserts `@Name` as plain text, the same as a table or file
 * mention. This reads it back out so the send can name the agent, because the
 * text alone routes nothing — the server needs the name in the request, and it
 * refuses one that is not already in the conversation.
 *
 * Pure and framework-free so the matching rules are testable without rendering.
 */

/** Longest name first: with both `@ops` and `@ops-lead` present, `@ops-lead`
 *  must win, or the longer name is unreachable. */
function byLengthDescending(a: string, b: string): number {
  return b.length - a.length;
}

function escapeForRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * The first agent from `names` that the text mentions, or null.
 *
 * Case-insensitive, and anywhere in the message rather than only at the start —
 * "can you ask @batman about this" addresses batman as plainly as leading with
 * the name. The mention has to end at a word boundary so `@bat` does not match
 * `@batman`.
 */
export function addressedAgentName(
  text: string,
  names: readonly string[],
): string | null {
  const trimmed = text.trim();
  if (!trimmed.includes("@")) return null;
  for (const name of [...names].sort(byLengthDescending)) {
    if (!name) continue;
    const pattern = new RegExp(`@${escapeForRegExp(name)}(?![\\w-])`, "i");
    if (pattern.test(trimmed)) return name;
  }
  return null;
}
