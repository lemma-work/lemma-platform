"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type LibraryItem, type Pod, type SharedLink, type Tab } from "@/data";
import { Modal } from "@/shell/modal";
import { AddPeople } from "@/shell/add-people";
import { ShareDialog } from "@/thread/share-dialog";
import { copyText } from "@/desktop/clipboard";
import { CheckIcon, CopyIcon, GlobeIcon, LockIcon, PlusIcon } from "@/ui/icons";

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
       the open internet. That is what a public link below is for. */
    if (said === "PUBLIC") return "Anyone signed in to Lemma can open this, including people outside " + space + ".";
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
        <li className="ssheet__live">
            <span className="ssheet__live-text">
                <a href={link.readUrl} target="_blank" rel="noreferrer">{link.readUrl.replace(/^https?:\/\//, "")}</a>
                <small>{expiresIn(link.expiresAt)} · up to {link.maxHits} opens</small>
            </span>
            <button className="ssheet__small" onClick={() => {
                copyText(link.readUrl).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1400); }).catch(() => undefined);
            }}>{copied ? "Copied" : "Copy"}</button>
            <button className="ssheet__small ssheet__small--quiet" disabled={revoking} onClick={onRevoke}>{revoking ? "Turning off…" : "Turn off"}</button>
        </li>
    );
}

/** One Share, whatever is on the stage — the way Space has one sheet for a
 *  page and a whole space alike.
 *
 *  Access in Lemma is the space's: anyone in it can open what is in it, so
 *  "who has access" is the space's people for every subject, and adding a
 *  person adds them to the space. A file can also go past the space, as a
 *  bounded public link, which is the one thing only a file can do. */
export function ShareSheet({ pod, orgId, subject, adding: addingFirst = false, onClose }: {
    pod: Pod;
    orgId: string | null;
    subject: ShareSubject;
    /** Open on adding people — asked for by an Invite button. */
    adding?: boolean;
    onClose: () => void;
}) {
    const [adding, setAdding] = useState(addingFirst);
    const [copied, setCopied] = useState(false);
    const [publicLink, setPublicLink] = useState(false);
    const path = subject.kind === "file" ? subject.path : "";
    const file = useQuery({
        queryKey: ["file", pod.id, path],
        queryFn: () => source.readFile(pod.id, path),
        enabled: Boolean(path),
        staleTime: 5 * 60_000,
    });
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
        <Modal narrow title={"Share “" + name + "”"} onClose={onClose}>
            <div className="ssheet">
                {adding ? (
                    <div className="ssheet__adding">
                        <AddPeople pod={pod} orgId={orgId} />
                        <button className="ssheet__back" onClick={() => setAdding(false)}>Done adding</button>
                    </div>
                ) : (
                    <button className="ssheet__add" onClick={() => setAdding(true)}>
                        <PlusIcon size={16} /> Add people to {pod.name}
                    </button>
                )}

                <div className="ssheet__label">Who has access</div>
                <ul className="ssheet__people">
                    {people.map(member => (
                        <li key={member.id}>
                            <span className="ssheet__face">{member.initials}</span>
                            <span className="ssheet__who">
                                <span>{member.name}</span>
                                {member.email && <small>{member.email}</small>}
                            </span>
                            <span className="ssheet__role">{member.role}</span>
                        </li>
                    ))}
                    {people.length === 0 && <li className="ssheet__none">Only you so far.</li>}
                </ul>
                {subject.kind !== "space" && (
                    <p className={"ssheet__note" + (isPublic ? " ssheet__note--public" : "")}>{whoCanOpen(pod.name, known)}</p>
                )}

                <div className="ssheet__label">Link</div>
                <div className="ssheet__link">
                    <span className="ssheet__link-glyph"><LockIcon size={16} /></span>
                    <span className="ssheet__link-text">
                        <span>People in {pod.name}</span>
                        <small>Anyone else is asked to join first</small>
                    </span>
                </div>
                {subject.kind === "file" && (
                    <div className="ssheet__link">
                        <span className="ssheet__link-glyph"><GlobeIcon size={16} /></span>
                        <span className="ssheet__link-text">
                            <span>Anyone with the link</span>
                            <small>{live.length === 0 ? "A read-only link that expires"
                                : live.length === 1 ? "One link is live" : live.length + " links are live"}</small>
                        </span>
                        <button className="ssheet__small" disabled={!file.data} onClick={() => setPublicLink(true)}>{live.length ? "New link" : "Create"}</button>
                    </div>
                )}
                {live.length > 0 && (
                    <ul className="ssheet__lives">
                        {live.map(link => (
                            <LiveLink key={link.code} link={link} revoking={revoke.isPending && revoke.variables === link.code}
                                onRevoke={() => revoke.mutate(link.code)} />
                        ))}
                    </ul>
                )}

                <div className="ssheet__foot">
                    <button className="ssheet__copy" onClick={() => void copy()}>
                        {copied ? <CheckIcon size={16} /> : <CopyIcon size={16} />} {copied ? "Copied" : "Copy link"}
                    </button>
                    <button className="ssheet__done" onClick={onClose}>Done</button>
                </div>
            </div>
        </Modal>
    );
}
