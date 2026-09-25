import { isLandingPreview } from "@/marketing/preview-mode";

export const PREFIX = "lemma-app";

const WAS = "lemma-room";

/** The suffixes that already exist in people's browsers.
 *
 *  This is the list the migration walks, so a key added after the rename does
 *  not belong in it — there is nothing under the old prefix to carry.
 */
const CARRIED = [
    "theme",
    "accent",
    "corners",
    "org",
    "tabs",
    "sidebar-collapsed",
    "sidebar-hidden",
    "header-hidden",
    "data",
] as const;

export function key(name: string): string {
    return (isLandingPreview() ? "lemma-tour" : PREFIX) + ":" + name;
}

/** Somewhere keys live, with the DOM taken out so the move can be asserted on. */
export interface KeyValueStore {
    getItem(name: string): string | null;
    setItem(name: string, value: string): void;
    removeItem(name: string): void;
}

/** Carry this browser's preferences onto the new prefix.
 *
 *  Returns the suffixes it actually moved, which is what a test can observe.
 *  An existing value under the new prefix always wins: the migration must be
 *  safe to run on every page load, and a second run must not undo a choice
 *  made after the first one.
 */
export function carryStoredPreferences(store: KeyValueStore): string[] {
    const moved: string[] = [];
    for (const name of CARRIED) {
        const before = WAS + ":" + name;
        const after = PREFIX + ":" + name;
        const value = store.getItem(before);
        if (value === null) continue;
        if (store.getItem(after) === null) {
            store.setItem(after, value);
            moved.push(name);
        }
        store.removeItem(before);
    }
    return moved;
}

/** The same move, as a string the document can run before anything else does.
 *
 *  It has to be inline in `<head>`, not a React effect. The appearance script
 *  beside it reads these keys before first paint, so a migration that waited
 *  for React would hand every returning person one frame of the wrong theme —
 *  once each, which is exactly the kind of bug nobody ever gets round to
 *  reporting.
 *
 *  Generated from `CARRIED` rather than written out, so the list has one home.
 *
 *  It ends in a semicolon, which is load-bearing. Two IIFEs concatenated
 *  without one parse cleanly and then throw at runtime — `(f)()(g)()` calls
 *  the first one's return value — so the appearance script that follows it
 *  silently never runs and every returning person gets a flash of the wrong
 *  theme. Nothing in the build can see that; only a browser can.
 *
 *  Delete this, `WAS` and `carryStoredPreferences` a release after they ship.
 */
export const CARRY_SCRIPT =
    `(function(){try{var s=localStorage,k=${JSON.stringify(CARRIED)},i,o,n,v;` +
    `for(i=0;i<k.length;i++){o='${WAS}:'+k[i];n='${PREFIX}:'+k[i];v=s.getItem(o);` +
    `if(v!==null){if(s.getItem(n)===null)s.setItem(n,v);s.removeItem(o)}}}catch(e){}})();`;

/** Workspace locations belong to an account; appearance belongs to the browser. */
export function retainWorkspaceOwner(store: KeyValueStore, owner: string | null): boolean {
    const ownerKey = key("workspace-owner");
    const changed = store.getItem(ownerKey) !== owner;
    if (changed || owner === null) {
        for (const name of ["org", "tabs", "last-pod"]) {
            store.removeItem(key(name));
            store.removeItem(WAS + ":" + name);
        }
    }
    if (owner === null) store.removeItem(ownerKey);
    else store.setItem(ownerKey, owner);
    return changed;
}

/** Other tabs must discard their captured client and mounted workspace too. */
export function sessionStorageChanged(name: string | null, before: string | null, after: string | null): boolean {
    if (name === null) return true;
    return before !== after && [key("workspace-owner"), "lemma_token", "lemma_api_url"].includes(name);
}
