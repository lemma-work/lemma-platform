"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { joinChoices, sayJoinWords, saidAbout, source, type JoinPolicy, type LibraryItem, type Member, type Pod, type SharedLink, type Tab } from "@/data";
import { Modal } from "@/shell/modal";
import { useCanManageMembers } from "@/shell/add-people";
import { ShareDialog } from "@/thread/share-dialog";
import { copyText } from "@/desktop/clipboard";
import { listed } from "@/site/plugins/catalog";
import { CheckIcon, CopyIcon, GlobeIcon, LinkIcon } from "@/ui/icons";

/** What is being shared: the space itself, or one thing in it. */
export type ShareSubject =
    | { kind: "space" }
    | { kind: "file"; path: string; label: string }
    | { kind: "table" | "app"; label: string };

/** What the lists already know about who can open this, looked up in their
 *  cache rather than fetched again: the row you opened it from said so. */
function useKnownAccess(podId: string, subject: ShareSubject): { visibility?: string; rls?: boolean } {
    const cache = useQueryClient();
    if (subject.kind === "space") return {};
    if (subject.kind === "app") {
        const tabs = cache.getQueryData<Tab[]>(["tabs", podId]) ?? [];
        const app = tabs.find((tab): tab is Extract<Tab, { kind: "app" }> => tab.kind === "app" && tab.label === subject.label);
        return { visibility: app?.visibility };
    }
    for (const [, data] of cache.getQueriesData<{ items?: LibraryItem[] }>({ queryKey: ["library", podId] })) {
        const hit = data?.items?.find(item => subject.kind === "file" ? item.path === subject.path : item.kind === "table" && item.name === subject.label);
        if (hit) return { visibility: hit.visibility, rls: hit.rls };
    }
    return {};
}

function whoCanOpen(space: string, known: { visibility?: string; rls?: boolean }): string {
    const said = (known.visibility ?? "").toUpperCase();
    /* PUBLIC waives the space, not sign-in: it is every Lemma account, never
       the open internet. That is what a public link below is for. It is
       also what the space's bots answer people outside it from, in the
       groups where they answer them — so it reaches them too. */
    if (said === "PUBLIC") return "Anyone with a Lemma account can open this, and people outside " + space + " get answers from it in its groups.";
    if (said === "PERSONAL") return "Only you can open this. It is in your private files.";
    if (said === "RESTRICTED") return "Only the people it was shared with can open this.";
    if (known.rls) return "Everyone in " + space + " can open this, and each sees only their own rows.";
    return "Everyone in " + space + " can open this.";
}

function expiresIn(iso: string): string {
    const left = Date.parse(iso) - Date.now();
    if (!Number.isFinite(left)) return "";
    if (left < 3_600_000) return "Expires in under an hour";
    const hours = Math.round(left / 3_600_000);
    if (hours < 48) return "Expires in " + hours + (hours === 1 ? " hour" : " hours");
    return "Expires in " + Math.round(hours / 24) + " days";
}

/** A public link that still works: what it opens, how long it has, and the
 *  two things anybody does with one — send it again, or stop it. */
function LiveLink({ link, onRevoke, revoking }: { link: SharedLink; onRevoke: () => void; revoking: boolean }) {
    const [copied, setCopied] = useState(false);
    return (
        <li className="kd-row">
            <span className="kd-row__words">
                <a href={link.readUrl} target="_blank" rel="noreferrer">{link.readUrl.replace(/^https?:\/\//, "")}</a>
                <small>{expiresIn(link.expiresAt)} · up to {link.maxHits} opens</small>
            </span>
            <span className="kd-row__acts">
                <button type="button" className="b b--s b--secondary" onClick={() => {
                    copyText(link.readUrl).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1400); }).catch(() => undefined);
                }}>{copied ? "Copied" : "Copy"}</button>
                <button type="button" className="b b--s b--secondary b--red" disabled={revoking} onClick={onRevoke}>{revoking ? "Turning off…" : "Turn off"}</button>
            </span>
        </li>
    );
}

/** Who is in it, as faces and first names, with the way to add more. The
 *  roster itself is the Lemma's People; adding is its own dialog, which the
 *  caller opens (`onAddPeople`), so there is one way to add people and not a
 *  second, smaller one in here. */
function PeopleRow({ pod, people, onAddPeople }: { pod: Pod; people: Member[]; onAddPeople?: () => void }) {
    const may = useCanManageMembers(pod.id);
    const names = people.map((member) => member.name.split(/\s+/)[0]).filter(Boolean);
    return (
        <div className="kd-row">
            {people.length > 0 && (
                <span className="kd-faces" aria-hidden="true">
                    {people.slice(0, 4).map((member) => <span key={member.id} className="kd-face">{member.initials}</span>)}
                </span>
            )}
            <span className="kd-row__words">
                <b>{people.length === 0 ? "Only you so far" : people.length === 1 ? "1 person" : people.length + " people"}</b>
                {names.length > 0 && <small>{listed(names)}</small>}
            </span>
            {onAddPeople && may.allowed && (
                <button type="button" className="b b--s b--secondary" onClick={onAddPeople}>Add people</button>
            )}
        </div>
    );
}

/** Who may let themselves in: the Lemma's own rule, as choice cards. The
 *  same rule and words as `WhoCanJoin` on its page, read and written under
 *  the same key, so the two always agree. The middle rung is offered beside
 *  the closed one; "anyone with a Lemma account" is shown only where it is
 *  already the rule, since most organizations refuse it. Only someone who
 *  runs the Lemma can change it; everyone else sees it, not a control that
 *  can only fail. */
function WhoCanOpen({ pod, orgName }: { pod: Pod; orgName: string }) {
    const cache = useQueryClient();
    const may = useCanManageMembers(pod.id);
    const current = useQuery({ queryKey: ["pod-join", pod.id], queryFn: () => source.getPodJoin(pod.id) });
    const set = useMutation({
        mutationFn: (policy: JoinPolicy) => source.setPodJoin(pod.id, policy),
        onSuccess: (_answer, policy) => {
            cache.setQueryData(["pod-join", pod.id], policy);
            void cache.invalidateQueries({ queryKey: ["pod-join", pod.id] });
        },
    });
    const policy: JoinPolicy = set.isPending && set.variables ? set.variables : (current.data ?? "invited");
    const choices = joinChoices(orgName).filter((choice) => choice.policy !== "anyone" || policy === "anyone");
    const locked = !may.allowed || current.isPending || set.isPending;
    return (
        <div className="kfield" role="radiogroup" aria-label={"Who can join " + pod.name}>
            <span>Who can join {pod.name}</span>
            {current.isError ? (
                <p className="kd-problem" role="alert">
                    Couldn’t read who can join.{" "}
                    <button type="button" className="b b--s b--secondary" onClick={() => void current.refetch()}>Try again</button>
                </p>
            ) : (
                <div className="kopts">
                    {choices.map((choice) => (
                        <label key={choice.policy} className={"kopt kopt--compact" + (locked && policy !== choice.policy ? " kd-opt--off" : "")}>
                            <input
                                type="radio"
                                name={"join-" + pod.id}
                                value={choice.policy}
                                checked={!current.isPending && policy === choice.policy}
                                disabled={locked}
                                onChange={() => set.mutate(choice.policy)}
                            />
                            <span className="kopt__t">{choice.who}</span>
                            <span className="kopt__d">{choice.then}</span>
                            <span className="kopt__dot" aria-hidden="true" />
                        </label>
                    ))}
                </div>
            )}
            {current.isPending && <small role="status">Reading who can join…</small>}
            {may.answered && !may.allowed && <small>Only someone who runs {pod.name} can change this.</small>}
            {set.isError && (
                <p className="kd-problem" role="alert">{sayJoinWords(saidAbout(set.error, "That could not be saved."), orgName)}</p>
            )}
        </div>
    );
}

/** One Share, whatever is on the stage — the way Space has one sheet for a
 *  page and a whole space alike.
 *
 *  Access in Lemma is the space's: anyone in it can open what is in it, so
 *  "who has access" is the space's people for every subject. So the dialog
 *  is three things in order: who is in it (and the way to add more), who else
 *  may join, and the link. A file can also go past the space, as a bounded
 *  public link, which is the one thing only a file can do. */
export function ShareSheet({ pod, orgId, subject, onAddPeople, onClose }: {
    pod: Pod;
    orgId: string | null;
    subject: ShareSubject;
    /** Open the dialog that adds people to this Lemma. Without it there is
     *  no Add people button here. */
    onAddPeople?: () => void;
    onClose: () => void;
}) {
    const [copied, setCopied] = useState(false);
    const [publicLink, setPublicLink] = useState(false);
    const path = subject.kind === "file" ? subject.path : "";
    const file = useQuery({
        queryKey: ["file", pod.id, path],
        queryFn: () => source.readFile(pod.id, path),
        enabled: Boolean(path),
        staleTime: 5 * 60_000,
    });
    const orgs = useQuery({ queryKey: ["orgs"], queryFn: () => source.listOrgs(), staleTime: 10 * 60_000 });
    const orgName = orgs.data?.find((one) => one.id === (orgId ?? pod.orgId))?.name ?? "";
    const cache = useQueryClient();
    const linksKey = ["file-links", pod.id, path];
    const links = useQuery({
        queryKey: linksKey,
        queryFn: () => source.fileLinks(pod.id, path),
        enabled: Boolean(path),
    });
    const revoke = useMutation({
        mutationFn: (code: string) => source.revokeFileLink(pod.id, code),
        onSettled: () => cache.invalidateQueries({ queryKey: linksKey }),
    });
    const live = links.data ?? [];
    const name = subject.kind === "space" ? pod.name : subject.label;
    const people = pod.members.filter(member => member.kind === "person");
    const known = useKnownAccess(pod.id, subject);
    const isPublic = (known.visibility ?? "").toUpperCase() === "PUBLIC";
    const [here, setHere] = useState("");
    useEffect(() => setHere(window.location.href), []);

    const copy = async () => {
        try {
            await copyText(window.location.href);
            setCopied(true);
            setTimeout(() => setCopied(false), 1600);
        } catch {
            setCopied(false);
        }
    };

    if (publicLink && file.data) {
        return <ShareDialog podId={pod.id} path={path} name={file.data.name} appUrl={file.data.appUrl} startWith="anyone"
            onClose={() => { setPublicLink(false); void cache.invalidateQueries({ queryKey: linksKey }); }} />;
    }

    return (
        <Modal
            title={"Share " + name}
            subtitle={subject.kind === "space" ? "Who is in it, who else can join, and its link." : "Who can open it, and its link."}
            onClose={onClose}
            footer={<button type="button" className="b b--l b--primary" onClick={onClose}>Done</button>}
        >
            <div className="kd-stack">
                <PeopleRow pod={pod} people={people} onAddPeople={onAddPeople} />
                {subject.kind !== "space" && (
                    <p className={"kd-small" + (isPublic ? " kd-small--warn" : "")}>{whoCanOpen(pod.name, known)}</p>
                )}

                <WhoCanOpen pod={pod} orgName={orgName} />

                <div className="kd-row">
                    <span className="kd-row__glyph" aria-hidden="true"><LinkIcon size={18} /></span>
                    <span className="kd-row__words">
                        <b>{here.replace(/^https?:\/\//, "") || "This page"}</b>
                        <small>The link to {name}</small>
                    </span>
                    <button type="button" className="b b--s b--secondary" onClick={() => void copy()}>
                        {copied ? <CheckIcon size={15} /> : <CopyIcon size={15} />} {copied ? "Copied" : "Copy"}
                    </button>
                </div>

                {subject.kind === "file" && (
                    <div className="kd-row">
                        <span className="kd-row__glyph" aria-hidden="true"><GlobeIcon size={18} /></span>
                        <span className="kd-row__words">
                            <b>Anyone with the link</b>
                            <small>{links.isError ? "Couldn’t read its public links"
                                : live.length === 0 ? "A read-only link that expires"
                                    : live.length === 1 ? "One link is live" : live.length + " links are live"}</small>
                        </span>
                        {links.isError
                            ? <button type="button" className="b b--s b--secondary" onClick={() => void links.refetch()}>Try again</button>
                            : <button type="button" className="b b--s b--secondary" disabled={!file.data} onClick={() => setPublicLink(true)}>{live.length ? "New link" : "Create"}</button>}
                    </div>
                )}
                {live.length > 0 && (
                    <ul className="kd-rows">
                        {live.map(link => (
                            <LiveLink key={link.code} link={link} revoking={revoke.isPending && revoke.variables === link.code}
                                onRevoke={() => revoke.mutate(link.code)} />
                        ))}
                    </ul>
                )}
                {revoke.isError && <p className="kd-problem" role="alert">That link is still live. Try turning it off again.</p>}
            </div>
        </Modal>
    );
}
