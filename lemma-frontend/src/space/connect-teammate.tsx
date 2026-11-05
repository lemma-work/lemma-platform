"use client";

import { useState } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type LinkShare, type Pod, type PodLink } from "@/data";
import { isForbidden } from "@/session/auth-state";
import { Modal } from "@/shell/modal";
import { useCanManageMembers } from "@/shell/add-people";
import { ChevronDownIcon, ChevronRightIcon } from "@/ui/icons";
import { TeammateFace } from "./teammate-face";
import {
    askerChoices,
    everyPage,
    sameShare,
    seededShares,
    shareRows,
    sharedLine,
    sharesNotListed,
    toggled,
    type ShareRow,
} from "./pod-links";

/** Letting another teammate ask this one, and seeing who can.
 *
 *  Made from the side being asked, because that side decides: its admin lets
 *  the other teammate in, and picks which of its own tables and folders the
 *  other may read. The other then asks with nobody present -- on a schedule, in
 *  a workflow -- and reads only what was shared plus what is Public here.
 *
 *  Who may do either is the server's `pod.member.manage` here, asked through
 *  `useCanManageMembers`: everybody else sees who can ask, and no way to
 *  change it. */

/** Whether the signed-in person may connect, edit or disconnect a teammate
 *  here: `pod.member.manage`, as the server says. Unknown counts as no. The
 *  sample source connects for real, in memory, and has no server to ask, so
 *  there the answer is yes -- as it is for deleting. */
export function useMayConnect(podId: string): boolean {
    const { allowed } = useCanManageMembers(podId);
    return source.label === "sample" || allowed;
}

function statusOf(error: unknown): number | undefined {
    if (!error || typeof error !== "object") return undefined;
    const status = (error as { statusCode?: unknown }).statusCode;
    return typeof status === "number" ? status : undefined;
}

export function ConnectTeammate({ pod, asker: editing, onClose }: {
    pod: Pod;
    /** Open on a teammate already connected, to change what it reads. */
    asker?: string;
    onClose: () => void;
}) {
    const cache = useQueryClient();
    const [asker, setAsker] = useState(editing ?? "");
    /* Null until somebody ticks or unticks something: until then it is what
       the chosen teammate reads already, read off its connection. Connecting
       again replaces every share, so starting from an empty list would take
       away whatever it read before without anyone choosing to. */
    const [picked, setPicked] = useState<LinkShare[] | null>(null);
    const [opened, setOpened] = useState<string[]>([]);
    const [problem, setProblem] = useState("");

    const askable = useQuery({ queryKey: ["askable-pods", pod.id], queryFn: () => source.askablePods(pod.id), staleTime: 60_000 });
    const links = useQuery({ queryKey: ["pod-links", pod.id], queryFn: () => source.podLinks(pod.id), staleTime: 60_000 });
    const tables = useQuery({
        queryKey: ["link-shareable", pod.id, "tables"],
        queryFn: () => everyPage((page) => source.listLibrary(pod.id, "tables", "/", page)),
    });
    /* The root, and every folder somebody has opened: one listing each, all
       of it, so a share can be any folder however deep. */
    const folderPaths = ["/", ...opened];
    const folderLists = useQueries({
        queries: folderPaths.map((path) => ({
            queryKey: ["link-shareable", pod.id, "files", path],
            queryFn: () => everyPage((page) => source.listLibrary(pod.id, "files", path, page)),
        })),
    });
    const folderRows = new Map(folderPaths.map((path, at) => [path, shareRows(folderLists[at]?.data ?? [])]));

    const choices = askerChoices(askable.data ?? [], links.data ?? []);
    const chosen = choices.find((other) => other.id === asker);
    const shared = picked ?? seededShares(links.data ?? [], asker);
    const tableRows = shareRows(tables.data ?? []);
    const listed = [...tableRows, ...[...folderRows.values()].flat()].map((row) => row.share);
    const unlisted = sharesNotListed(shared, listed);
    const listing = tables.isPending || folderLists[0]?.isPending;

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
                  : statusOf(error) === 400
                    ? "Something ticked can’t be shared. Untick it and try again."
                    : "That couldn’t be saved. Try again.",
        ),
    });

    const choose = (id: string) => { setAsker(id); setPicked(null); setProblem(""); };
    const toggle = (one: LinkShare) => setPicked(toggled(shared, one));
    const open = (path: string) => setOpened((was) => (was.includes(path) ? was.filter((one) => one !== path) : [...was, path]));

    const row = (one: ShareRow) => {
        const key = one.share.kind + ":" + one.share.name;
        const isOpen = one.folder && opened.includes(one.share.name);
        const children = isOpen ? folderRows.get(one.share.name) : undefined;
        const childAt = folderPaths.indexOf(one.share.name);
        return (
            <li key={key}>
                <div className="connect__share">
                    <label className="check">
                        <input type="checkbox" checked={shared.some((item) => sameShare(item, one.share))} onChange={() => toggle(one.share)} />
                        <span>
                            {one.share.name}
                            <em>
                                {one.share.kind === "table" ? "Table" : "Folder, and everything in it"}
                                {one.public && <> · <b className="connect__public">Public</b> it can read this already</>}
                            </em>
                        </span>
                    </label>
                    {one.folder && (
                        <button
                            className="connect__open"
                            aria-expanded={isOpen}
                            aria-label={(isOpen ? "Close " : "Open ") + one.share.name}
                            onClick={() => open(one.share.name)}
                        >
                            {isOpen ? <ChevronDownIcon size={14} /> : <ChevronRightIcon size={14} />}
                        </button>
                    )}
                </div>
                {isOpen && (
                    <ul className="connect__choices connect__choices--nested">
                        {folderLists[childAt]?.isPending && <li className="aboutpage__quiet">Loading…</li>}
                        {folderLists[childAt]?.isError && <li className="aboutpage__quiet">Couldn’t open this folder.</li>}
                        {folderLists[childAt]?.isSuccess && children?.length === 0 && <li className="aboutpage__quiet">No folders inside.</li>}
                        {(children ?? []).map(row)}
                    </ul>
                )}
            </li>
        );
    };

    return (
        <Modal
            title={editing && chosen ? "What " + chosen.name + " can read in " + pod.name : "Let another teammate ask " + pod.name}
            subtitle="It can ask with nobody present, and reads only what you share and what is Public."
            narrow
            onClose={onClose}
        >
            <div className="connect">
                <p className="connect__label">Who can ask</p>
                {(askable.isPending || links.isPending) && <p className="aboutpage__quiet">Loading…</p>}
                {(askable.isError || links.isError) && (
                    <p className="aboutpage__quiet" role="alert">
                        Couldn’t load the teammates you can connect.{" "}
                        <button className="linkish" onClick={() => { void askable.refetch(); void links.refetch(); }}>Try again</button>
                    </p>
                )}
                {editing && askable.isSuccess && links.isSuccess && !chosen && (
                    <p className="aboutpage__quiet">
                        You’re not in {links.data.find((link) => link.podId === editing)?.name ?? "that teammate"}, so you can’t change what it reads. You can still disconnect it.
                    </p>
                )}
                {askable.isSuccess && links.isSuccess && choices.length === 0 && (
                    <p className="aboutpage__quiet">There’s no other teammate you’re in. You can only connect one you’re in yourself.</p>
                )}
                <ul className="connect__choices">
                    {choices.map((other) => (
                        <li key={other.id}>
                            <label className="check">
                                <input type="radio" name="asker" checked={asker === other.id} onChange={() => choose(other.id)} />
                                <TeammateFace pod={other} size={24} />
                                <span>
                                    {other.name}
                                    {other.connected && <em>Already connected. Choosing it changes what it reads.</em>}
                                </span>
                            </label>
                        </li>
                    ))}
                </ul>

                <p className="connect__label">What it can read</p>
                <p className="connect__exposed">
                    {chosen
                        ? "Anyone in " + chosen.name + ", and its automations with nobody present, will be able to read what you tick here, and anything Public in " + pod.name + "."
                        : "Whoever is in the teammate you choose, and its automations, will be able to read what you tick here."}
                </p>
                {listing && <p className="aboutpage__quiet">Loading…</p>}
                {(tables.isError || folderLists[0]?.isError) && (
                    <p className="aboutpage__quiet" role="alert">
                        Couldn’t load everything there is to share.{" "}
                        <button className="linkish" onClick={() => { void tables.refetch(); void folderLists[0]?.refetch(); }}>Try again</button>
                    </p>
                )}
                {!listing && tableRows.length === 0 && (folderRows.get("/") ?? []).length === 0 && unlisted.length === 0 && (
                    <p className="aboutpage__quiet">Nothing here to share yet. It will read only what is Public.</p>
                )}
                <ul className="connect__choices">
                    {/* Shared already and in no list on screen: a folder deeper
                        than anything opened. Drawn ticked so saving keeps it. */}
                    {unlisted.map((one) => row({ share: one, public: false, folder: false }))}
                    {tableRows.map(row)}
                    {(folderRows.get("/") ?? []).map(row)}
                </ul>

                <div className="modal__acts connect__acts">
                    {problem && <p className="reachrow__error" role="alert">{problem}</p>}
                    <button className="linkish" onClick={onClose}>Cancel</button>
                    <button
                        className="btn btn--primary"
                        disabled={!chosen || !links.isSuccess || connect.isPending}
                        onClick={() => { setProblem(""); connect.mutate(); }}
                    >
                        {connect.isPending ? "Saving…" : !chosen ? "Connect" : chosen.connected ? "Save what " + chosen.name + " reads" : "Let " + chosen.name + " ask"}
                    </button>
                </div>
            </div>
        </Modal>
    );
}

/** The teammates connected to this one, which can ask it with nobody present.
 *  Anybody here may see who; only those who may manage its people may change
 *  what one reads, or disconnect it. */
export function AskedBy({ pod, onOpen, onEdit }: {
    pod: Pod;
    onOpen: (podId: string) => void;
    /** Open the Connect dialog on this one, with what it reads ticked. */
    onEdit: (podId: string) => void;
}) {
    const cache = useQueryClient();
    const manage = useMayConnect(pod.id);
    const [confirming, setConfirming] = useState<string | null>(null);
    const [problem, setProblem] = useState("");
    const links = useQuery({ queryKey: ["pod-links", pod.id], queryFn: () => source.podLinks(pod.id), staleTime: 60_000 });
    const disconnect = useMutation({
        mutationFn: (link: PodLink) => source.disconnectPod(pod.id, link.podId),
        onSuccess: (_, link) => {
            setConfirming(null);
            void cache.invalidateQueries({ queryKey: ["pod-links", pod.id] });
            void cache.invalidateQueries({ queryKey: ["askable-pods", link.podId] });
        },
        onError: (error) => setProblem(
            isForbidden(error) ? "Only an admin of " + pod.name + " can disconnect a teammate." : "That couldn’t be disconnected. Try again.",
        ),
    });
    if (links.isPending) return null;
    if (links.isError) {
        return (
            <div className="asked-by">
                <p className="aboutpage__quiet" role="alert">
                    Couldn’t load who can ask {pod.name}.{" "}
                    <button className="linkish" onClick={() => void links.refetch()}>Try again</button>
                </p>
            </div>
        );
    }
    if (links.data.length === 0) return null;
    return (
        <div className="asked-by">
            <p className="connect__label">Can ask {pod.name}</p>
            <ul className="agentlist">
                {links.data.map((link) => (
                    <li key={link.podId}>
                        <div className="asked-by__row">
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
                            {manage && confirming !== link.podId && (
                                <span className="asked-by__acts">
                                    <button className="linkish reachrow__quiet" onClick={() => onEdit(link.podId)}>Edit what’s shared</button>
                                    <button className="linkish reachrow__quiet" onClick={() => { setProblem(""); setConfirming(link.podId); }}>Disconnect</button>
                                </span>
                            )}
                        </div>
                        {/* Said in what stops, not "are you sure": the asks it
                            runs on its own, and what it could read. */}
                        {manage && confirming === link.podId && (
                            <div className="asked-by__confirm" role="group" aria-label={"Disconnect " + link.name}>
                                <p>
                                    {link.name} will no longer be able to ask {pod.name}, and an answer it is waiting for won’t come back.
                                    {link.shared.length > 0
                                        ? " It loses what was shared: " + link.shared.map((one) => one.name).join(", ") + "."
                                        : ""}
                                </p>
                                <span className="acct__confirm">
                                    <button className="btn reachrow__drop" disabled={disconnect.isPending} onClick={() => disconnect.mutate(link)}>
                                        {disconnect.isPending ? "Disconnecting…" : "Disconnect " + link.name}
                                    </button>
                                    <button className="linkish" disabled={disconnect.isPending} onClick={() => setConfirming(null)}>Keep</button>
                                </span>
                            </div>
                        )}
                    </li>
                ))}
            </ul>
            {problem && <p className="reachrow__error" role="alert">{problem}</p>}
        </div>
    );
}
