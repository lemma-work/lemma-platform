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
