/**
 * "Create this with the assistant", wherever it is pressed.
 *
 * Two places used to build this by hand: the sidebar's New menu and the Apps
 * index. They had drifted — same intent, different wording, and only one of
 * them said to keep the result calm and operational. Both also shipped the
 * string "Lemma app app" into the prompt, a leftover from a repo-wide rename
 * that a second copy made twice as likely to survive. One builder now, so the
 * next edit lands everywhere it should.
 *
 * Unlike a new pod, this fires inside a pod that already has things in it: the
 * user pressed a button, typed a line, and expects the thing to exist. So this
 * builds rather than proposes — the difference from `new-pod-conversation` is
 * deliberate, not an inconsistency.
 */

export type AssistantCreationKind = "agent" | "app" | "workflow" | "table";

const RESOURCE_LABEL: Record<AssistantCreationKind, string> = {
    agent: "agent",
    app: "app",
    workflow: "workflow",
    table: "table",
};

/** What "built properly" means for each kind, in the terms that kind is judged on. */
const BUILD_GUIDANCE: Record<AssistantCreationKind, string> = {
    agent: "Create one agent: instructions that read as a job description — what arrives, what to produce, what to do when unsure — only the resource access it actually needs, and a name that fits this pod.",
    app: "Start from the operator: who opens this, and what decision are they making when they do. Then build the smallest app that answers it — the right data behind it, the pages it needs, nothing else. Keep it calm and operational; no generic dashboard chrome.",
    workflow: "Create one workflow: a clear trigger or manual start, steps that do real work rather than describe it, and a name that fits this pod.",
    table: "Create one table: a practical schema, field names a person can read, and a name that fits this pod. Seed a few believable rows so it means something the moment it opens.",
};

export function buildResourceCreationInstructions(
    kind: AssistantCreationKind,
): string {
    return [
        `They pressed "New ${RESOURCE_LABEL[kind]}" and typed one line describing it. That line is the brief: treat it as the product intent, and never repeat these instructions back to them.`,
        "This pod is not empty. Look at what is already here before you make anything, and extend or reuse what already fits rather than standing a near-duplicate up beside it.",
        BUILD_GUIDANCE[kind],
        "Ask a question only when the alternative is building the wrong thing — one question, short. Otherwise build it, then show them the result with `display_resource` instead of describing it, and say in a line what it does and what to try first.",
    ].join("\n\n");
}

/**
 * The conversation that creation opens in. `source` is the only thing the two
 * call sites disagree about, and it is metadata rather than instruction.
 */
export function buildResourceCreationHref({
    podId,
    kind,
    prompt,
    source,
}: {
    podId: string;
    kind: AssistantCreationKind;
    /** The user's own sentence. Empty when the button carries the whole intent. */
    prompt?: string;
    source: "sidebar_new_menu" | "apps_page";
}): string {
    const params = new URLSearchParams();
    const message = prompt?.trim();
    if (message) params.set("assistantMessage", message);
    params.set("conversationInstructions", buildResourceCreationInstructions(kind));
    params.set(
        "conversationMetadata",
        JSON.stringify({
            source,
            intent: "create_resource",
            resource_type: kind,
        }),
    );

    return `/pod/${encodeURIComponent(podId)}/conversations/new?${params.toString()}`;
}
