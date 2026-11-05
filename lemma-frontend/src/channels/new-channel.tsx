"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Member, type Pod } from "@/data";
import { useMe } from "@/session/use-me";
import { Modal } from "@/shell/modal";
import { channelApi } from "./api";
import { keepChannel } from "./queries";

/** A chat shared with people in the space: a name and who is in it. The
 *  teammate is in it from the start, answering whoever names it. Private
 *  unless the maker lets anyone in the space find it and join. Nothing
 *  outside feeds it until a group is brought in. */
export function NewSharedChat({ pod, onClose, onMade }: {
    pod: Pod;
    onClose: () => void;
    onMade: (channelId: string) => void;
}) {
    const me = useMe();
    const ids = useId();
    const cache = useQueryClient();
    const [title, setTitle] = useState("");
    const [members, setMembers] = useState<Set<string>>(new Set());
    const [open, setOpen] = useState(false);
    const nameField = useRef<HTMLInputElement>(null);
    const teammate = pod.teammate?.name || pod.name;
    const agents = useQuery({ queryKey: ["agents", pod.id], queryFn: () => source.listAgents(pod.id), staleTime: 5 * 60_000 });
    const front = agents.data?.find((agent) => agent.front && agent.id) ?? null;
    useEffect(() => { nameField.current?.focus(); }, []);
    /* The pod's members were read when the space opened, and are kept a
       while: anybody who joined since is asked for again here, where they
       are offered. */
    useEffect(() => { void cache.invalidateQueries({ queryKey: ["pod-detail", pod.id], exact: true }); }, [cache, pod.id]);
    /* People, not teammates; and not the reader, who is in it already. */
    const people = pod.members.filter((member): member is Member & { userId: string } =>
        member.kind !== "teammate" && Boolean(member.userId) && member.userId !== me);
    const make = useMutation({
        mutationFn: () => channelApi(pod.id).create({
            title: title.trim(),
            topic: null,
            members: [...members],
            visibility: open ? "PUBLIC" : "PRIVATE",
            agents: front ? [{ id: front.id, answers: "MENTIONED" }] : [],
        }),
        onSuccess: (made) => { keepChannel(cache, pod.id, made); onMade(made.id); },
    });
    const submit = (event: FormEvent) => {
        event.preventDefault();
        if (title.trim() && !make.isPending) make.mutate();
    };
    const toggle = (userId: string, on: boolean) => setMembers((was) => {
        const next = new Set(was);
        if (on) next.add(userId); else next.delete(userId);
        return next;
    });
    return (
        <Modal title="New shared chat" subtitle={"With people in " + pod.name + ". Anyone in it can ask " + teammate + "."} onClose={onClose}>
            <form className="gsheet" onSubmit={submit}>
                <label className="gsheet__field">
                    <span className="gsheet__label">Name</span>
                    <input ref={nameField} value={title} maxLength={200} required placeholder="Launch plan" onChange={(event) => setTitle(event.target.value)} />
                </label>
                <fieldset className="cnew__people">
                    <legend className="gsheet__label">Who is in it</legend>
                    {people.length === 0 ? (
                        <p className="gsheet__hint">Nobody else is in {pod.name} yet. Share {pod.name} first, then add them here.</p>
                    ) : (
                        <ul className="cnew__list">
                            {people.map((member) => (
                                <li key={member.userId}>
                                    <label className="gsheet__check" htmlFor={ids + "-member-" + member.userId}>
                                        <input id={ids + "-member-" + member.userId} type="checkbox" aria-label={member.name} checked={members.has(member.userId)} onChange={(event) => toggle(member.userId, event.target.checked)} />
                                        <span>{member.name}</span>
                                    </label>
                                </li>
                            ))}
                        </ul>
                    )}
                </fieldset>
                <label className="gsheet__check">
                    <input type="checkbox" checked={open} onChange={(event) => setOpen(event.target.checked)} />
                    <span>Anyone in {pod.name} can find it and join</span>
                </label>
                {make.isError && <p className="gsheet__problem" role="alert">{make.error instanceof Error && make.error.message ? make.error.message : "Couldn’t start the chat. Try again."}</p>}
                <div className="gsheet__foot">
                    <button type="button" className="ghost-pill" onClick={onClose}>Cancel</button>
                    <button type="submit" className="pill-button" disabled={make.isPending || !title.trim()}>{make.isPending ? "Starting…" : "Start chat"}</button>
                </div>
            </form>
        </Modal>
    );
}
