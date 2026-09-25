/** A fresh random id for an event or a request.
 *
 *  `crypto.randomUUID` exists only in a secure context, and a Lemma Desktop
 *  installation shared on the local network is served over plain HTTP at a
 *  private address — not one. There it is `undefined`, and calling it threw
 *  from inside the call's event loop. `getRandomValues` has no such
 *  restriction, so the fallback is still a proper version-4 UUID. */
export function newId(): string {
    const source = typeof crypto !== "undefined" ? crypto : undefined;
    if (source?.randomUUID) return source.randomUUID();
    const bytes = new Uint8Array(16);
    if (source?.getRandomValues) source.getRandomValues(bytes);
    else for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
