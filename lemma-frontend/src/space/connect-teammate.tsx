"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type LinkShare, type Pod, type PodLink } from "@/data";
import { isForbidden } from "@/session/auth-state";
import { Modal } from "@/shell/modal";
import { TeammateFace } from "./teammate-face";

/** Letting another teammate ask this one, and seeing who can.
 *
 *  Made from the side being asked, because that side decides: its admin lets
 *  the other teammate in, and picks which of its own tables and folders the
 *  other may read. The other then asks with nobody present -- on a schedule, in
 *  a workflow -- and reads only what was shared plus what is Public here. */

function statusOf(error: unknown): number | undefined {
    if (!error || typeof error !== "object") return undefined;
    const status = (error as { statusCode?: unknown }).statusCode;
    return typeof status === "number" ? status : undefined;
}

function sameShare(a: LinkShare, b: LinkShare): boolean {
    return a.kind === b.kind && a.name === b.name;
}

/** "Reads invoices and /reports", or what it reads when nothing was shared. */
export function sharedLine(shared: LinkShare[]): string {
    if (shared.length === 0) return "Reads only what is Public here";
    return "Reads " + shared.map((one) => one.name).join(", ");
}

export function ConnectTeammate({ pod, onClose }: { pod: Pod; onClose: () => void }) {
    const cache = useQueryClient();
    const [asker, setAsker] = useState("");
    const [shared, setShared] = useState<LinkShare[]>([]);
    const [problem, setProblem] = useState("");

    const pods = useQuery({ queryKey: ["pods", pod.orgId], queryFn: () => source.listPods(pod.orgId) });
    const tables = useQuery({
        queryKey: ["link-shareable", pod.id, "tables"],
        queryFn: () => source.listLibrary(pod.id, "tables", "/"),
    });
    const folders = useQuery({
        queryKey: ["link-shareable", pod.id, "files"],
        queryFn: () => source.listLibrary(pod.id, "files", "/"),
    });

    const others = (pods.data ?? []).filter((other) => other.id !== pod.id);
    const shareable: LinkShare[] = [
        ...(tables.data?.items ?? []).filter((item) => item.kind === "table").map((item) => ({ kind: "table" as const, name: item.name })),
        ...(folders.data?.items ?? []).filter((item) => item.kind === "folder").map((item) => ({ kind: "folder" as const, name: item.path })),
    ];
    const chosen = others.find((other) => other.id === asker);

    const connect = useMutation({
        mutationFn: () => source.connectPod(pod.id, asker, shared),
        onSuccess: () => {
            void cache.invalidateQueries({ queryKey: ["pod-links", pod.id] });
            void cache.invalidateQueries({ queryKey: ["askable-pods", asker] });
            onClose();
        },
        onError: (error) => setProblem(
            isForbidden(error)
                ? "Only an admin of " + pod.name + " can let another teammate ask it."
                : statusOf(error) === 409
                  ? "You can only connect a teammate you’re in."
                  : "That couldn’t be connected. Try again.",
        ),
    });

    const toggle = (one: LinkShare) => setShared((was) =>
        was.some((item) => sameShare(item, one)) ? was.filter((item) => !sameShare(item, one)) : [...was, one]);

    return (
        <Modal
            title={"Let a teammate ask " + pod.name}
            subtitle="It can ask with nobody present, and reads only what you share and what is Public."
            narrow
            onClose={onClose}
        >
            <div className="connect">
                <p className="connect__label">Who can ask</p>
                {pods.isPending && <p className="aboutpage__quiet">Loading…</p>}
                {!pods.isPending && others.length === 0 && (
                    <p className="aboutpage__quiet">There’s no other teammate here to connect.</p>
                )}
                <ul className="connect__choices">
                    {others.map((other) => (
                        <li key={other.id}>
                            <label className="check">
                                <input type="radio" name="asker" checked={asker === other.id} onChange={() => setAsker(other.id)} />
                                <TeammateFace pod={other} size={24} />
                                <span>{other.name}</span>
                            </label>
                        </li>
                    ))}
                </ul>

                <p className="connect__label">What it can read</p>
                {shareable.length === 0 && !tables.isPending && !folders.isPending && (
                    <p className="aboutpage__quiet">Nothing here to share yet. It will read only what is Public.</p>
                )}
                <ul className="connect__choices">
                    {shareable.map((one) => (
                        <li key={one.kind + ":" + one.name}>
                            <label className="check">
                                <input type="checkbox" checked={shared.some((item) => sameShare(item, one))} onChange={() => toggle(one)} />
                                <span>{one.name}<em>{one.kind === "table" ? "Table" : "Folder"}, to read</em></span>
                            </label>
                        </li>
                    ))}
                </ul>

                {problem && <p className="reachrow__error" role="alert">{problem}</p>}
                <div className="modal__acts">
                    <button className="linkish" onClick={onClose}>Cancel</button>
                    <button
                        className="btn btn--primary"
                        disabled={!chosen || connect.isPending}
                        onClick={() => { setProblem(""); connect.mutate(); }}
                    >
                        {connect.isPending ? "Connecting…" : chosen ? "Let " + chosen.name + " ask" : "Connect"}
                    </button>
                </div>
            </div>
        </Modal>
    );
}

/** The teammates connected to this one, which can ask it with nobody present. */
export function AskedBy({ pod, onOpen }: { pod: Pod; onOpen: (podId: string) => void }) {
    const cache = useQueryClient();
    const [problem, setProblem] = useState("");
    const links = useQuery({ queryKey: ["pod-links", pod.id], queryFn: () => source.podLinks(pod.id), staleTime: 60_000 });
    const disconnect = useMutation({
        mutationFn: (link: PodLink) => source.disconnectPod(pod.id, link.podId),
        onSuccess: (_, link) => {
            void cache.invalidateQueries({ queryKey: ["pod-links", pod.id] });
            void cache.invalidateQueries({ queryKey: ["askable-pods", link.podId] });
        },
        onError: (error) => setProblem(
            isForbidden(error) ? "Only an admin of " + pod.name + " can disconnect a teammate." : "That couldn’t be disconnected. Try again.",
        ),
    });
    if (!links.data || links.data.length === 0) return null;
    return (
        <div className="asked-by">
            <p className="connect__label">Can ask {pod.name}</p>
            <ul className="agentlist">
                {links.data.map((link) => (
                    <li key={link.podId}>
                        <div className="agentlist__row asked-by__row">
                            <button className="asked-by__who" onClick={() => onOpen(link.podId)}>
                                <TeammateFace pod={{ id: link.podId, name: link.name, iconUrl: link.iconUrl }} size={32} />
                                <span className="agentlist__body">
                                    <span className="agentlist__title"><b>{link.name}</b></span>
                                    <small>
                                        {sharedLine(link.shared)}
                                        {link.stewardName ? " · connected by " + link.stewardName : ""}
                                    </small>
                                </span>
                            </button>
                            <button className="linkish" disabled={disconnect.isPending} onClick={() => { setProblem(""); disconnect.mutate(link); }}>
                                Disconnect
                            </button>
                        </div>
                    </li>
                ))}
            </ul>
            {problem && <p className="reachrow__error" role="alert">{problem}</p>}
        </div>
    );
}
