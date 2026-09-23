/** Binding a conversation to the thing it is about.
 *
 *  A form changes a value; it cannot say why. The conversation a resource
 *  carries is where its structural changes are asked for and where the reasons
 *  stay — so "why does this table have a `launch_phase` column" has an answer
 *  that is not archaeology.
 *
 *  The binding lives in conversation metadata, which the list endpoint filters
 *  on directly (`?metadata.lemma_resource=table:invoices`). That is the whole
 *  mechanism: no second store, no naming convention to keep, and finding the
 *  conversation for a resource is one request.
 */

/** Resources that can carry one. Deliberately not "anything with an id" — a
 *  conversation per row would be thousands of conversations nobody opened. */
export type ResourceKind = "table" | "file" | "app" | "workflow" | "agent" | "function" | "schedule";

/** The metadata key. Prefixed because the server puts its own keys in the same
 *  object — `cwd` is already there — and a bare `resource` would be a collision
 *  waiting for whoever adds the next one. */
export const RESOURCE_KEY = "lemma_resource";

/** What a resource is called in metadata.
 *
 *  Lowercased, because it is an identity rather than a label: a table looked up
 *  as `Invoices` and bound as `invoices` would quietly get two conversations,
 *  and the second one would look empty for no visible reason.
 *
 *  For a file the name has to be its full path. Two `notes.md` in two folders
 *  are two files, and binding them by basename would give one conversation
 *  that claims to be about both.
 */
export function resourceKey(kind: ResourceKind, name: string): string {
    return kind + ":" + name.trim().toLowerCase();
}

/** Whether a conversation is the one bound to this resource. */
export function isBoundTo(
    conversation: { metadata?: Record<string, unknown> | null } | null | undefined,
    kind: ResourceKind,
    name: string,
): boolean {
    const held = conversation?.metadata?.[RESOURCE_KEY];
    return typeof held === "string" && held === resourceKey(kind, name);
}

/** What a resource is *called*, as distinct from what identifies it.
 *
 *  A file is identified by its full path and called by its last segment. Using
 *  the path for both put `/me/launch/notes/2026/q1/summary.md · file` in the
 *  history panel, where every row is one line and that one is mostly folders.
 */
export function resourceLabel(kind: ResourceKind, name: string): string {
    if (kind !== "file") return name;
    return name.split("/").filter(Boolean).pop() || name;
}

/** What the conversation is called in the history panel. */
export function resourceTitle(kind: ResourceKind, name: string): string {
    const label: Record<ResourceKind, string> = {
        table: "table",
        file: "file",
        app: "app",
        workflow: "workflow",
        agent: "agent",
        function: "function",
        schedule: "schedule",
    };
    return resourceLabel(kind, name) + " · " + label[kind];
}

/** What the teammate is told, once, when the conversation is made.
 *
 *  Says what the conversation is about and nothing about how to do anything.
 *  The agent already has its tools and knows them better than this does;
 *  instructions that describe a method go stale the moment the toolset changes,
 *  and a stale instruction is worse than none because it is believed.
 */
export function resourceInstructions(kind: ResourceKind, name: string): string {
    const noun: Record<ResourceKind, string> = {
        table: "the table `" + name + "`",
        file: "the file `" + name + "`",
        app: "the app `" + name + "`",
        workflow: "the workflow `" + name + "`",
        agent: "the agent `" + name + "`",
        function: "the function `" + name + "`",
        schedule: "the schedule `" + name + "`",
    };
    return (
        "This conversation is about " + noun[kind] + " in this pod. " +
        "When something is asked for here without naming what it applies to, it applies to that. " +
        "It is a standing conversation: the person will come back to it, so what was decided and why is worth keeping in what you say."
    );
}

/** The query the list endpoint takes to find it. */
export function findQuery(kind: ResourceKind, name: string): Record<string, string | number> {
    return {
        ["metadata." + RESOURCE_KEY]: resourceKey(kind, name),
        /* `PROJECT` is the type for something standing rather than asked once,
           and it keeps these out of the ordinary CHAT history where they would
           otherwise sit between two real conversations. */
        type: "PROJECT",
        limit: 1,
    };
}
