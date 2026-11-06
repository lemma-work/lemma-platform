import { redirect } from "next/navigation";
import { HOME, readTrialView, writeView } from "@/outcomes/address";

export const metadata = { robots: { index: false, follow: false } };

/** Where the app lived before its addresses were plain words: every `/t`
 *  link -- from email, Slack, WhatsApp, a bookmark -- sent on to the same
 *  place. The frame's own first grammar (`/t/outcome/{id}/…`) is read here
 *  and written as the new address; a workspace address (`/t/{pod}/…`) goes
 *  to the same path under `/l`, where the app reads the workspace's grammar
 *  and settles on its own. The query goes with it: arrivals such as
 *  `?org=` and `?settings=` are read where they land. */
export default async function OldAppRoute({ params, searchParams }: {
    params: Promise<{ path?: string[] }>;
    searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
    const { path = [] } = await params;
    const query = new URLSearchParams();
    for (const [name, value] of Object.entries(await searchParams)) {
        for (const one of Array.isArray(value) ? value : value === undefined ? [] : [value]) query.append(name, one);
    }
    const tail = path.map(encodeURIComponent).join("/");
    const view = readTrialView("/t/" + tail);
    const target = view ? writeView(view) : tail ? "/l/" + tail : HOME;
    const search = query.toString();
    redirect(target + (search ? "?" + search : ""));
}
