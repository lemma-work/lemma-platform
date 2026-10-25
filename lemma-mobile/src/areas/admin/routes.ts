import { lemmaTab, part, underLemma } from "../paths";
import type { LemmaPartId, SettingsSectionId } from "./sections";

/** Settings. The account's and the organization's are `(in)/settings/[section]`,
 *  listed on the account screen; a Lemma's are its Settings tab, and
 *  `(in)/lemma/[podId]/settings/[section]` for a part of their own. A part
 *  that needs more screens keeps its routes in its own folder's `routes.ts`,
 *  under `(in)/settings/<part>/…` or `(in)/lemma/[podId]/settings/<part>/…`. */
export const adminRoutes = {
    section: (section: SettingsSectionId) => `/settings/${part(section)}`,
    lemma: (podId: string) => lemmaTab(podId, "settings"),
    lemmaSection: (podId: string, section: LemmaPartId) => underLemma(podId, "settings", section),
};
