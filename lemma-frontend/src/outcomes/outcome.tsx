"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type ConversationRef, type Pod } from "@/data";
import { usePodMay } from "@/connect/pod-connect";
import { ContactsPage } from "@/space/contacts-page";
import { ChatsPage } from "@/space/chats-page";
import { AppCover } from "@/stage/app-cover";
import { useMaySeeContacts } from "@/space/contact-queries";
import { ageOf, chatView, outcomeView, type Ask, type Go, type OutcomeTab, type QueueItem } from "./model";
import { mcpUrl } from "./data";
import { Empty, Icon, to } from "./parts";
import type { FrameActions } from "./actions";
import { appName, appsOf } from "./lemma-apps";
import { AboutTab } from "./about";
import { AddAppPage } from "./add-app";
import { AgentsPage, AutomationsPage } from "./lemma-work";
import { FilesPage, MemoryPage, SkillsPage } from "./lemma-knows";
import { sectionLabel } from "./lemma-bar";
import { LemmaPage, LemmaPageHead } from "./lemma-page";
import { PagesTab, PeopleTab, TablesTab } from "./lemma-tabs";
import { Stage } from "./stage";
import { PageActsProvider } from "./page-acts";
import { Calm, Problem } from "./calm";
import { TryAgain, codeOf } from "./read-again";
import { SkeletonCards } from "@/ui/skeleton";

async function copy(text: string): Promise<boolean> {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch {
        return false;
    }
}

/** One Lemma. At its own address, its page: the Lemma itself, alive, with
 *  the box to ask it, and everything it holds underneath (`LemmaPage`). No
 *  tabs: each thing it holds or does -- its apps, automations, agents,
 *  memory, skills, tables, files and pages -- is a section there, and each
 *  opens a page of its own here, under the way back to it and a line from
 *  the Lemma about what is on it. About and its parts (people, contacts)
 *  are reached from its menu. (Settings is drawn by the shell, full height,
 *  under the bar the things it holds wear.) */
export function OutcomePage({ pod, pods, orgName, tab, section = null, activity, queue, me, go, onAsk, actions }: {
    pod: Pod;
    /** Every Lemma in the organization, for the queues that read across them. */
    pods: readonly Pod[];
    orgName: string;
    tab: OutcomeTab;
    /** One view of the tab, where the tab has views (Automations). */
    section?: string | null;
    activity: readonly ConversationRef[];
    queue: readonly QueueItem[];
    me: string;
    go: Go;
    /** Start a chat with this Lemma with these words, and open it. */
    onAsk: Ask;
    actions: FrameActions;
}) {
    const contacts = useMaySeeContacts(pod.id);
    if (tab === "home") {
        return (
            <Stage pod={pod} go={go} ownBar>
                <LemmaPage pod={pod} activity={activity} queue={queue} me={me} go={go} onAsk={onAsk} actions={actions} />
            </Stage>
        );
    }
    /* Every other page of it names itself under the way back to it, with a
       line from the Lemma about what is here. */
    const title = PAGE_TITLES[tab] ?? sectionLabel(tab as Exclude<OutcomeTab, "home">);
    return (
        <PageActsProvider>
            <Stage pod={pod} go={go} head={<LemmaPageHead pod={pod} tab={tab} title={title} says={pageSays(tab, pod.name)} go={go} actions={actions} />}>
                <div className="ox-page ox-section" data-section={tab}>
                    {tab === "about" && <AboutTab pod={pod} orgName={orgName} go={go} actions={actions} />}
                    {tab === "chats" && (
                        <ChatsPage
                            pod={pod}
                            openId={null}
                            initialFilter={section === "channels" ? "channels" : "all"}
                            onOpen={(id) => go(chatView({ kind: "chat", podId: pod.id, id }))}
                            onOpenChannel={(id) => go(chatView({ kind: "shared", podId: pod.id, id }))}
                            onOpenRun={(runId) => go({ name: "run", podId: pod.id, runId })}
                            onNew={() => go(outcomeView(pod.id))}
                            onConnect={() => actions.connect(pod.id)}
                            onReach={() => actions.connect(pod.id)}
                            onPages={() => go(outcomeView(pod.id, "pages"))}
                            onAsk={(text) => actions.prompt(pod.id, text)}
                        />
                    )}
                    {tab === "pages" && <PagesTab pod={pod} go={go} actions={actions} />}
                    {tab === "tables" && <TablesTab pod={pod} go={go} actions={actions} />}
                    {tab === "files" && <FilesPage pod={pod} go={go} actions={actions} />}
                    {tab === "apps" && <AppsTab key={pod.id} pod={pod} go={go} actions={actions} />}
                    {tab === "people" && <PeopleTab pod={pod} orgName={orgName} />}
                    {tab === "contacts" && (contacts
                        ? <ContactsPage pod={pod} onConnect={() => actions.connect(pod.id)} />
                        : <Calm icon="lock" title="Contacts are for whoever looks after them">Ask an admin of {pod.name} if you should be one.</Calm>)}
                    {tab === "automations" && <AutomationsPage pod={pod} pods={pods} section={section} queue={queue} go={go} actions={actions} />}
                    {tab === "agents" && <AgentsPage pod={pod} go={go} actions={actions} />}
                    {tab === "memory" && <MemoryPage pod={pod} go={go} actions={actions} />}
                    {tab === "skills" && <SkillsPage pod={pod} go={go} actions={actions} />}
                    {tab === "add" && <AddAppPage pod={pod} pods={pods} go={go} actions={actions} />}
                </div>
            </Stage>
        </PageActsProvider>
    );
}

/** Names for the pages the row of sections never had. */
const PAGE_TITLES: Partial<Record<OutcomeTab, string>> = {
    agents: "Agents",
    memory: "Memory",
    skills: "Skills",
    add: "Add an app",
};

/** One line from the Lemma at the top of each of its pages, about what is
 *  there: first person, plain, true of every Lemma. */
function pageSays(tab: OutcomeTab, name: string): string | null {
    switch (tab) {
        case "automations": return "What I do on my own: on a schedule, when something arrives, or step by step.";
        case "agents": return `Helpers I hand work to. You talk to me, ${name}; they work behind me.`;
        case "memory": return "What I’ve learned while working, written down so I don’t ask twice.";
        case "skills": return "How you do things, written down. I follow these whenever the work comes up.";
        case "files": return "Everything handed to me. Drop files here and I read each one.";
        case "tables": return "What I keep track of, row by row.";
        case "pages": return "What I’ve written up, and what you’ve written for me.";
        case "apps": return "The apps everyone in here uses me through.";
        case "add": return "Pick one ready to go, or describe your own and I’ll build it.";
        case "chats": return "Every conversation with me, wherever it started.";
        default: return null;
    }
}

/* ── apps ─────────────────────────────────────────────────────────────── */

function AppsTab({ pod, go, actions }: { pod: Pod; go: Go; actions: FrameActions }) {
    const tabs = useQuery({ queryKey: ["outcome-apps", pod.id], queryFn: () => source.listTabs(pod.id), staleTime: 5 * 60_000 });
    /* The one it opens on leads, as it leads everywhere else. */
    const apps = appsOf(tabs.data, pod.homeApp);
    const [copied, setCopied] = useState<string | null>(null);
    const mayChoose = usePodMay(pod.id, "pod.update").allowed;
    const cache = useQueryClient();
    const choose = useMutation({
        mutationFn: (name: string) => source.setHomeApp(pod.id, name),
        /* Returned, so the choice stays pending until the list shows it: a
           second tap cannot send it again in between. */
        onSuccess: () => cache.invalidateQueries({ queryKey: ["pods", pod.orgId] }),
    });
    const url = mcpUrl(pod.id);
    return (
        <div>
            <section className="ox-reach">
                <div>
                    <p className="ox-reach__t">Use {pod.name} from Claude or ChatGPT</p>
                    <p className="ox-vs">One address gives an assistant {pod.name}’s tables, files and apps, with the access of whoever connects it.</p>
                </div>
                {url && <button type="button" className="ox-btn" onClick={async () => setCopied((await copy(url)) ? "mcp" : null)}>{copied === "mcp" ? "Address copied" : "Copy the address"}</button>}
            </section>
            {tabs.isPending ? <SkeletonCards label={`Reading ${pod.name}’s apps`} count={3} slow={`Still reading ${pod.name}’s apps. It’s slower than usual.`} /> : tabs.isError ? (
                <Calm tone="warn" title={`Couldn’t read ${pod.name}’s apps`} note={codeOf(tabs.error)} actions={<TryAgain busy={tabs.isFetching} onClick={() => void tabs.refetch()} />}>
                    Nothing in them changed. The list of them didn’t come through just now.
                </Calm>
            ) : apps.length === 0 ? (
                <Empty title="No apps yet.">
                    <p>Apps are how people work with {pod.name}, and how people outside it use what it makes.</p>
                    <button type="button" className="ox-btn ox-btn--lemma" onClick={() => actions.prompt(pod.id, "Build an app that ")}>Ask {pod.name} to build one</button>
                </Empty>
            ) : (
                <>
                {choose.isError && (
                    <Problem retrying={choose.isPending} onRetry={() => { if (choose.variables) choose.mutate(choose.variables); }}>
                        Couldn’t change which app {pod.name} opens on.
                    </Problem>
                )}
                <div className="ox-apps">
                    {apps.map((app, index) => {
                        const name = appName(app);
                        const inFrame = to({ name: "app", podId: pod.id, app: name }, go);
                        const opensFirst = index === 0 && apps.length > 1;
                        return (
                            <article key={app.id} className="ox-app" aria-label={app.label}>
                                <a className="ox-app__cover" {...inFrame} aria-label={"Open " + app.label}>
                                    <AppCover url={app.url} fallback={<span className="ox-app__blank"><Icon name="app" size={22} /></span>} />
                                </a>
                                <p className="ox-app__name">{app.label}</p>
                                <p className="ox-app__meta">{[opensFirst && "opens first", app.visibility && app.visibility.toLowerCase(), app.updated && `updated ${ageOf(Date.parse(app.updated), Date.now())} ago`].filter(Boolean).join(" · ")}</p>
                                <div className="ox-app__acts">
                                    <a className="ox-btn ox-btn--primary" {...inFrame} aria-label={"Open " + app.label}>Open</a>
                                    <button type="button" className="ox-btn" aria-label={copied === app.id ? undefined : "Copy link to " + app.label} onClick={async () => setCopied((await copy(app.url)) ? app.id : null)}>{copied === app.id ? "Link copied" : "Copy link"}</button>
                                    {mayChoose && index > 0 && (
                                        <button type="button" className="ox-btn" disabled={choose.isPending} title={`Open ${pod.name} on ${app.label}`} onClick={() => choose.mutate(name)}>
                                            {choose.isPending && choose.variables === name ? "Saving…" : "Make it first"}
                                        </button>
                                    )}
                                    <a className="ox-btn ox-btn--icon" href={app.url} target="_blank" rel="noreferrer" aria-label={`Open ${app.label} in a new tab`} title="Open in a new tab"><Icon name="out" size={14} /></a>
                                </div>
                            </article>
                        );
                    })}
                </div>
                </>
            )}
        </div>
    );
}
