/** Transcript fragments are independent of voice output: a spoken acknowledgement
 * must never split a user's unfinished instruction into two dispatches. */
export class UtteranceBuffer {
    private text = "";
    private timer: ReturnType<typeof setTimeout> | null = null;
    private commit: (text: string) => void;
    constructor(commit: (text: string) => void) { this.commit = commit; }
    push(text: string, final = false) {
        this.text += text;
        if (this.timer) clearTimeout(this.timer);
        // Final transcription gets a brief correction window. Providers without
        // final markers use quiet time; there is no cap that cuts ongoing speech.
        this.timer = setTimeout(() => this.flush(), final ? 350 : 1100);
    }
    flush() {
        if (this.timer) clearTimeout(this.timer);
        this.timer = null;
        const text = this.text.trim(); this.text = "";
        if (text) this.commit(text);
    }
    close() { if (this.timer) clearTimeout(this.timer); this.timer = null; this.text = ""; }
}
