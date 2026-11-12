import { lemmaTab, part, underLemma } from "../paths";

/** Files, pages and docs. Screens to add: `(in)/lemma/[podId]/folder`,
 *  `(in)/lemma/[podId]/file`, both taking `?path=`. */
export const filesRoutes = {
    files: (podId: string) => lemmaTab(podId, "files"),
    folder: (podId: string, path: string) => `${underLemma(podId, "folder")}?path=${part(path)}`,
    file: (podId: string, path: string) => `${underLemma(podId, "file")}?path=${part(path)}`,
};
