import { resourceInstructions, type ResourceKind } from "@web/thread/resource-conversation";

/* The instructions a doc's own conversation is made with: the web's
   `instructionsFor` in `lemma-frontend/src/chat/floating-chat.tsx`, copied
   word for word (`tests/web-copies.test.ts` holds them to it), so the agent
   behind a page reads the same brief whichever app asked. */

export function instructionsFor(resource: { kind: ResourceKind; name: string }): string {
    if (resource.kind === "file") {
        return (
            "This conversation is attached to the doc `" + resource.name + "` in this pod, and the person has it open beside you, waiting on the page. " +
            "When they ask for a change without naming where, it is a change to that doc. Make it with `pod_edit_file` (the text to replace and what goes there) — " +
            "not by downloading, rewriting or re-uploading the file — keep everything they did not ask about exactly as it is, and say in a sentence what you changed. " +
            "A passage they quote is the part they mean. " +
            "The doc is markdown the page editor reads as blocks, so keep its conventions: a line holding only a link to a pod path " +
            "(`[Title](/pages/…/Child.md)`) is a sub-page or an attached file, `![alt](/path)` is an image, `- [ ]` / `- [x]` are to-dos. " +
            "A line like `⟦… is writing: …⟧` marks where you were asked to write — replace that whole line with what you write. " +
            "The words after `is writing:` are the person's own, typed fast: take their plainest, most common reading and do not search for another. " +
            "If the subject is in this doc, the doc is the source — do not go looking in tables too; if it is not, try the pod's tables; if it is about the world (news, scores, prices), look it up on the web. " +
            "If it is genuinely ambiguous, pick the likeliest reading and name it in one clause of your reply.\n\n" +
            "A ```lemma-widget fenced block is HTML drawn live in the page (a chart, a diagram, a small tool). What it needs, and all it needs — " +
            "do not load the lemma-widget skill, which describes chat widgets, a different thing: " +
            "one self-contained HTML fragment with inline <style> and <script>; its data written into it, because it cannot reach the pod or the SDK when it is viewed; " +
            "colours from the page theme as `var(--lemma-widget-text, #1f1f1f)`, `--lemma-widget-muted`, `--lemma-widget-border`, `--lemma-widget-surface`, `--lemma-widget-accent` " +
            "and `--lemma-widget-chart-1`…`5`, each with a fallback; the page's width, so fluid layout and an SVG with a viewBox; any height, the frame follows it; " +
            "no backtick characters anywhere inside it, or the fence breaks. Draw charts in SVG by hand rather than loading a library: put the data in a small array in the block's own <script> and let a few lines of that script lay it out — never compute coordinates beforehand in Python or anywhere else. " +
            "Lead with the finding in one line, then the chart; keep it compact, but there is no size limit to trim toward. " +
            "Compose it once, directly in the `pod_edit_file` call — do not draft it in a workspace file first — and trust that call's result: " +
            "no reading the doc back, no screenshots, no rendering, no pixel checks. " +
            "The person is looking at the page, so there is nothing to display and nothing to note in memory for a change like this. " +
            "`window.lemma.compose(text)` puts a message in this chat if a button should ask a follow-up.\n\n" +
            "A ```lemma-view fenced block is a live view of pod tables: an optional `-- Title` line, then one read-only SELECT (joins allowed); " +
            "write one when they want rows, counts or a breakdown from a table rather than copying numbers into the text, and drop any `-- spec:` line when you change its SQL."
        );
    }
    return resourceInstructions(resource.kind, resource.name) +
        " The person has it open beside you while you talk.";
}
