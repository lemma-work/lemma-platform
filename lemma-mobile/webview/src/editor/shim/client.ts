import { call, CallError } from "../bridge";

/** The web's `lemma(podId)` as the editor sees it on the phone: only the
 *  three file calls the editor and its blocks make, each asked of the app,
 *  which makes them with its own session. Anything else is refused here, so
 *  the page can never ask the app for more than these. */

const refuse = (name: string) => () => Promise.reject(new CallError(name + " is not available on the phone.", null));

export function lemma(_podId?: string) {
    void _podId;
    return {
        files: {
            get: (path: string) => call("fileDetail", path),
            getUrl: (path: string) => call<{ url?: string; app_url?: string }>("fileUrl", path),
            upload: (file: Blob, options: { name?: string; directoryPath?: string; searchEnabled?: boolean }) => call<{ path?: string; name?: string }>("upload", file, options),
            update: refuse("files.update"),
            delete: refuse("files.delete"),
        },
        request: refuse("request"),
    };
}
