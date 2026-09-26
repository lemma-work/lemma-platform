/** Whether a failure means "no AI model is set up here".
 *
 *  The server says so with one stable code, `model_not_configured`, on every
 *  path it can arrive by: a send refused outright (an `ApiError`), a run that
 *  failed on its stream (an `AssistantRunError`), or a conversation reopened
 *  later with the failure stored on it (`last_run_error_code`). The message
 *  text differs by deployment and is written to be read; the code is what a
 *  screen can safely key a "set up a model" link off. */

export const MODEL_NOT_CONFIGURED = "model_not_configured";

function codeOf(signal: unknown): string | null {
    if (typeof signal === "string") return signal;
    if (!signal || typeof signal !== "object") return null;
    if ("code" in signal && typeof signal.code === "string") return signal.code;
    if ("last_run_error_code" in signal && typeof signal.last_run_error_code === "string") {
        return signal.last_run_error_code;
    }
    return null;
}

/** True when any of the signals carries the no-model code. Takes several
 *  because a conversation has several places a failure can be read from, and
 *  whichever one is current is the one that matters. */
export function needsAiModel(...signals: unknown[]): boolean {
    return signals.some((signal) => codeOf(signal) === MODEL_NOT_CONFIGURED);
}

/** What the transcript says when a teammate had no model to run on. The
 *  server's own text is written for whoever reads it on that deployment; this
 *  one names the teammate, which only the page knows. */
export function noModelSentence(teammate: string): string {
    return teammate + " has no model to think with yet. Add one in Settings → Models.";
}

/** The fields of a conversation record a failed run leaves behind. */
export interface RunFailureRecord {
    last_run_error?: string | null;
    last_run_error_code?: string | null;
    last_run_retryable?: boolean;
}

/** The failure on screen, wherever it was read from.
 *
 *  A run that fails while the page is open arrives on the stream; the same
 *  run seen after a reload is only on the conversation record. Reading only
 *  the stream left a reopened conversation saying "That run failed." with
 *  the reason sitting unread on the record, and offering a Retry the server
 *  had already said it would refuse. */
export function runFailure(
    state: "idle" | "running" | "waiting" | "failed",
    streamError: { message: string } | null,
    record: RunFailureRecord | null | undefined,
): { message: string | null; noModel: boolean; retryable: boolean } {
    const failed = state === "failed";
    const fromRecord = failed && !streamError ? record : null;
    const message = streamError?.message ?? fromRecord?.last_run_error ?? null;
    const noModel = streamError ? needsAiModel(streamError) : needsAiModel(fromRecord);
    /* Unknown means offered, as before: a record that predates the field
       should not lose the button. Never for a missing model -- the same run
       would fail the same way. */
    const retryable = !noModel && record?.last_run_retryable !== false;
    return { message, noModel, retryable };
}

/** Whether a failure's own sentence sends the reader to Settings → Models.
 *
 *  The server words provider refusals that way (a rejected key, a model the
 *  provider does not serve) but gives them no code of their own, so the
 *  sentence is what is read. Only to decide whether to draw a button that
 *  goes where the sentence already says; never to change what is said. */
export function pointsAtModels(message: string | null | undefined): boolean {
    return Boolean(message && message.includes("Settings → Models"));
}
