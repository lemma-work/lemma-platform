import { call } from "../bridge";

/** The web's `@/data` as the editor sees it on the phone: the few reads and
 *  writes it makes, each asked of the app. `label` is set once the app says
 *  whether this is a live account or sample data, before the page draws. */

export type FileContent = {
    name: string;
    path: string;
    mime: string;
    size: number;
    kind: "markdown" | "html" | "image" | "video" | "audio" | "pdf" | "text" | "binary";
    text?: string;
    note?: string;
    appUrl?: string;
    rawUrl?: string;
};

export type LibraryItem = {
    id: string;
    name: string;
    kind: "file" | "folder" | "table";
    path: string;
    updated: string;
    detail: string;
    status?: string;
};

export type ResourcePage<T> = { items: T[]; next?: string | null };

export const source = {
    label: "live" as "live" | "sample",
    readFile: (podId: string, path: string) => call<FileContent>("readFile", podId, path),
    writeFile: (podId: string, path: string, text: string) => call<void>("writeFile", podId, path, text),
    runQuery: (podId: string, sql: string) => call<{ items: Record<string, unknown>[]; truncated: boolean }>("runQuery", podId, sql),
    tableRows: (podId: string, name: string, page?: string) => call<ResourcePage<Record<string, unknown>>>("tableRows", podId, name, page ?? null),
    listLibrary: (podId: string, kind: "files" | "tables", directory: string) => call<ResourcePage<LibraryItem>>("listLibrary", podId, kind, directory),
    tableColumns: (podId: string, name: string) => call<{ name: string }[]>("tableColumns", podId, name),
};
