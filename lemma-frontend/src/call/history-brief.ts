import { interactionHeading } from "@/thread/approval";
import { resourceLabel } from "@/thread/display-resource";
import type { Turn } from "@/thread/turns";

/** The written conversation, compressed for a voice model's context.
 *
 *  Three rules, all learned from what the transcript actually holds:
 *
 *  Working notes are dropped. Fifty-seven tool calls tell a voice nothing it
 *  can use and would crowd out the part that matters.
 *
 *  The newest turns win. When the budget runs out it is the oldest that go,
 *  because a call continues the end of a conversation, not its beginning.
 *
 *  And it is capped. An oversized turn payload is a way to make the live
 *  session fall over, which is the same reason a spoken answer is truncated.
 *
 *  What is NOT dropped is anything that ended up on screen. A widget is not a
 *  working note — it is the part of the answer that was shown rather than
 *  said, and a voice that does not know it exists will read the table out. */

const BUDGET = 3200;
const PER_MESSAGE = 600;
const MAX_TURNS = 8;

function clip(text: string, limit = PER_MESSAGE): string {
    const clean = text.replace(/\s+/g, " ").trim();
    return clean.length > limit ? clean.slice(0, limit) + "…" : clean;
}

export function historyBrief(turns: Turn[], teammate: string, title?: string): string {
    const lines: string[] = [];

    for (const turn of turns.slice(-MAX_TURNS)) {
        if (turn.notice) {
            lines.push("· " + clip(turn.notice, 240));
            continue;
        }
        if (turn.human) lines.push("Them: " + clip(turn.human.text));
        for (const item of turn.items) {
            if (item.kind === "text") lines.push(teammate + ": " + clip(item.text));
            /* Named, not reproduced. The person can see it; the voice only
               needs to know it is there so it can point rather than recite. */
            else if (item.kind === "resource") lines.push("· on screen: " + clip(resourceLabel(item.resource), 80));
            /* A pause is worth a line: a call that does not know the run is
               blocked on an approval will cheerfully report it is still
               working. */
            else if (item.kind === "interaction" && item.interaction.open) {
                lines.push(
                    "· waiting on you: " +
                        clip(
                            interactionHeading(
                                item.interaction.kind,
                                item.interaction.details.title,
                                teammate,
                                false,
                            ),
                            120,
                        ),
                );
            }
        }
    }

    if (lines.length === 0) return "";

    /* Trim from the top until it fits — the end of the conversation is the
       part a call is continuing. */
    while (lines.length > 1 && lines.join("\n").length > BUDGET) {
        lines.shift();
    }

    const header = title ? `Conversation: "${clip(title, 80)}"` : "";
    return [header, ...lines].filter(Boolean).join("\n");
}
