/** The web's `@/session/storage`, the phone's way, for the web modules the
 *  app runs in place (`web-imports.js`): a key name, and the shape of a
 *  store that answers at once.
 *
 *  The phone keeps these in the platform keystore (`expo-secure-store`),
 *  whose keys allow only `[A-Za-z0-9._-]`: the web's `lemma-app:` prefix
 *  becomes the app's own `lemma.` one (`session/store.ts`), and anything
 *  else becomes an underscore. The keystore answers later, so the install
 *  area's `heldStore` reads it first and hands the web's rules a store over
 *  what it read. Pure, so `tests/` can hold it. */
export function key(name: string): string {
    return "lemma." + name.replace(/[^A-Za-z0-9._-]/g, "_");
}

export interface KeyValueStore {
    getItem(name: string): string | null;
    setItem(name: string, value: string): void;
    removeItem(name: string): void;
}
