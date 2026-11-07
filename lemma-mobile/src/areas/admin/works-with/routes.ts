import { part, underLemma } from "../../paths";

/** Works-with's own screen past its part screen: letting another Lemma ask
 *  this one, at `(in)/lemma/[podId]/settings/works-with/connect`, opened on
 *  one already allowed (`asker`) to change what it reads. */
export const worksWithRoutes = {
    connect: (podId: string, asker?: string) => underLemma(podId, "settings", "works-with", "connect") + (asker ? `?asker=${part(asker)}` : ""),
};
