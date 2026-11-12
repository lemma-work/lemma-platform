import type { SkillsSource } from "./source";

const DAY = 86_400_000;

function ago(ms: number): string {
    return new Date(Date.now() - ms).toISOString();
}

const REFUNDS = `---
name: refund-replies
description: Drafts a reply to a refund request in the house tone, naming the order and the line of the policy it rests on. Use when a customer asks for their money back.
---

# Refund replies

1. Find the order in the orders table by the customer's address.
2. Check the refund policy in /policies/refunds. Orders under 30 days old are refunded in full; older ones get store credit.
3. Draft the reply: thank them, say what happens and when, and name the order.
4. Anything over $200 waits for a person to say yes before it is sent.

Never promise a date the payment provider does not give.
`;

const TONE = `---
name: tone-of-voice
description: How Acme writes to customers, short, warm and plain, with no exclamation marks. Use for any reply a customer will read.
---

# Tone of voice

- Say what happened, then what happens next.
- One idea a sentence. No exclamation marks.
- Sign off with the team, never a person's name.
`;

/* Renamed in its frontmatter and not on disk, so it will not load: the
   row that says why is the point. */
const ESCALATIONS = `---
name: escalate
description: Hands a conversation to a person when a customer is upset or a refund is disputed. Use when the reply would need a judgement call.
---

# Escalations

Write a two-line summary, tag Sam, and tell the customer a person will answer within a day.
`;

const DIGEST = `---
name: weekly-digest
description: Writes Monday's page on what customers asked about last week, with the three biggest themes and the numbers behind them. Use for the weekly digest.
---

# Weekly digest

Read last week's tickets. Group them by what was asked. Write one page under /pages with the three biggest groups, how many asked, and one example each.
`;

const CHECKLIST = `---
name: launch-checklist
description: Walks a launch through its checks, from copy review to the partner quotes. Use when a launch date is set.
---

# Launch checklist

- Copy reviewed by two people
- Partner quotes in
- Pricing page updated the day before
`;

/** The sample Lemmas' skills, as files: Support has four, one of which will
 *  not load; Launch has one; the rest have none yet. Exported so a sample
 *  file viewer can open the same files. */
export const SAMPLE_SKILL_FILES: Record<string, { path: string; text: string; updated: string }[]> = {
    "sample-support-1140": [
        { path: "/skills/refund-replies/SKILL.md", text: REFUNDS, updated: ago(4 * DAY) },
        { path: "/skills/tone-of-voice/SKILL.md", text: TONE, updated: ago(40 * DAY) },
        { path: "/skills/escalations/SKILL.md", text: ESCALATIONS, updated: ago(2 * DAY) },
        { path: "/skills/weekly-digest/SKILL.md", text: DIGEST, updated: ago(400 * DAY) },
    ],
    "sample-launch-1440": [
        { path: "/skills/launch-checklist/SKILL.md", text: CHECKLIST, updated: ago(9 * DAY) },
    ],
};

export function skillsSample(): SkillsSource {
    return {
        list: async (podId, directory) => {
            const files = SAMPLE_SKILL_FILES[podId] ?? [];
            const base = directory.replace(/\/+$/, "");
            const folders = new Map<string, { name: string; kind: string; path: string; updated: string }>();
            for (const file of files) {
                if (!file.path.startsWith(base + "/")) continue;
                const next = file.path.slice(base.length + 1).split("/")[0];
                const path = `${base}/${next}`;
                const isFile = path === file.path;
                if (!folders.has(path)) folders.set(path, { name: next, kind: isFile ? "file" : "folder", path, updated: file.updated });
            }
            return { items: [...folders.values()], next: null };
        },
        read: async (podId, path) => (SAMPLE_SKILL_FILES[podId] ?? []).find((one) => one.path === path)?.text ?? null,
    };
}
