/** Every place in the app, written once. Screens link with these, never by
 *  building paths themselves, so a route that moves moves here. */

/** The id a chat not started yet goes by: it is created on the first send,
 *  never on open, so opening "New chat" leaves nothing behind. */
export const NEW_CHAT = "new";

const part = encodeURIComponent;

export const routes = {
    home: "/" as const,
    lemmas: "/lemmas" as const,
    apps: "/apps" as const,
    needs: "/needs" as const,
    notices: "/notices" as const,
    account: "/account" as const,
    lemma: (podId: string, section?: string) => `/lemma/${part(podId)}${section && section !== "home" ? `/section/${part(section)}` : ""}`,
    chat: (podId: string, id: string, ask?: string) => `/chat/${part(podId)}/${part(id)}${ask ? `?ask=${part(ask)}` : ""}`,
    newChat: (podId: string) => `/new-chat/${part(podId)}`,
    shared: (podId: string, channelId: string) => `/shared/${part(podId)}/${part(channelId)}`,
    app: (podId: string, name: string) => `/app/${part(podId)}/${part(name)}`,
    /** Something the phone has no screen for yet, opened in the workspace on
     *  the web: a table, a workflow run, a form. `tail` is the workspace's
     *  own grammar (`/t/{pod}/table/{name}`). */
    web: (podId: string, tail: string, title: string) => `/web?pod=${part(podId)}&tail=${part(tail)}&title=${part(title)}`,
};

/** The workspace's addresses for the same things (`shell/address.ts`). */
export const workspace = {
    table: (podId: string, name: string) => `/t/${part(podId)}/table/${part(name)}`,
    run: (podId: string, runId: string) => `/t/${part(podId)}/run/${part(runId)}`,
    conversation: (podId: string, id: string) => `/t/${part(podId)}/conversation/${part(id)}`,
    channel: (podId: string, id: string) => `/t/${part(podId)}/channel/${part(id)}`,
    pod: (podId: string) => `/t/${part(podId)}`,
};
