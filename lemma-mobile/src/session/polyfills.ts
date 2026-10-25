/** What the SDK's stream reader needs and Hermes may not have: a UTF-8
 *  `TextDecoder` that can be fed a stream in pieces. Installed only when the
 *  runtime has none, so a runtime that grows one keeps its own. */

class Utf8Decoder {
    private held: number[] = [];

    get encoding(): string {
        return "utf-8";
    }

    decode(input?: ArrayBuffer | ArrayBufferView, options?: { stream?: boolean }): string {
        const fresh = input === undefined
            ? new Uint8Array(0)
            : input instanceof ArrayBuffer
                ? new Uint8Array(input)
                : new Uint8Array(input.buffer, input.byteOffset, input.byteLength);
        const bytes = this.held.length ? Uint8Array.from([...this.held, ...fresh]) : fresh;
        this.held = [];
        let out = "";
        let index = 0;
        while (index < bytes.length) {
            const first = bytes[index];
            const size = first < 0x80 ? 1 : first >= 0xf0 ? 4 : first >= 0xe0 ? 3 : first >= 0xc0 ? 2 : 1;
            if (index + size > bytes.length) {
                /* A character split across pieces: keep its start for the next. */
                if (options?.stream) this.held = Array.from(bytes.subarray(index));
                else out += "�";
                break;
            }
            let point = size === 1 ? first : first & (0xff >> (size + 1));
            for (let step = 1; step < size; step += 1) point = (point << 6) | (bytes[index + step] & 0x3f);
            out += point > 0xffff ? String.fromCharCode(0xd800 + ((point - 0x10000) >> 10), 0xdc00 + ((point - 0x10000) & 0x3ff)) : String.fromCharCode(point);
            index += size;
        }
        return out;
    }
}

const scope = globalThis as { TextDecoder?: unknown };
if (typeof scope.TextDecoder === "undefined") scope.TextDecoder = Utf8Decoder;

export {};
