"use client";

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { source, type Runtime } from "@/data";
import { ComputerIcon, KeyIcon, PlusIcon } from "@/ui/icons";
import { capitalised, useThisComputer, type ComputerNoun } from "./this-computer";
import {
    LOCAL_SERVER_KEY, addToWorkspace, alreadyInWorkspace, detectLocalServers, friendlyError, operatorProvider, thisMac,
    type DetectedServer, type ProviderDraft,
} from "./this-mac";
import { useThisMacAvailability } from "./this-mac-settings";

/** What this computer can already offer the Models page.
 *
 *  Two kinds of row, both one click from being a provider teammates can pick:
 *
 *  - a model server already answering here — Ollama or LM Studio on its
 *    default port — found by asking it, not by asking the person;
 *  - the AI provider set on this computer before models belonged to the
 *    organization, offered as "Add to workspace" so it can be picked and
 *    managed by name like everything else here.
 *
 *  Nothing is offered twice: a route the organization already has is left out.
 *  The provider set on this computer also stays set after it is added — see
 *  `addToWorkspace` for why clearing it would be the wrong move. */
export function ThisMacModelSuggestions({ orgId, runtimes, onAdded }: { orgId: string; runtimes: Runtime[]; onAdded: () => void }) {
    const noun = useThisComputer();
    const availability = useThisMacAvailability();
    const shown = availability === "shown";
    const snapshot = useQuery({ queryKey: ["this-mac"], queryFn: () => thisMac.snapshot(), enabled: shown, staleTime: 30_000, retry: 0 });
    const servers = useQuery({ queryKey: ["this-mac-model-servers"], queryFn: () => detectLocalServers(), enabled: shown, staleTime: 60_000, retry: 0 });
    if (!shown) return null;

    const operator = snapshot.data ? operatorProvider(snapshot.data) : null;
    const offerOperator = operator && !alreadyInWorkspace(operator.baseUrl, runtimes) ? operator : null;
    const detected = (servers.data ?? []).filter((server) =>
        !alreadyInWorkspace(server.baseUrl, runtimes) && server.baseUrl !== offerOperator?.baseUrl.replace(/\/$/, ""));
    if (!offerOperator && detected.length === 0) return null;

    return (
        <section className="mgroup" aria-label={"Found on " + noun}>
            <div className="mgroup__head">
                <ComputerIcon size={14} />
                <span className="mgroup__name">On {noun}</span>
                <span className="mgroup__meta">Not in this list yet</span>
            </div>
            <ul className="mlist">
                {offerOperator && <OperatorRow draft={offerOperator} orgId={orgId} onAdded={onAdded} noun={noun} />}
                {detected.map((server) => <ServerRow key={server.id} server={server} orgId={orgId} onAdded={onAdded} noun={noun} />)}
            </ul>
        </section>
    );
}

function ServerRow({ server, orgId, onAdded, noun }: { server: DetectedServer; orgId: string; onAdded: () => void; noun: ComputerNoun }) {
    const add = useMutation({
        mutationFn: () => source.addProviderKey(orgId, {
            protocol: "openai", name: server.name, baseUrl: server.baseUrl, apiKey: LOCAL_SERVER_KEY, models: server.models,
        }),
        onSuccess: onAdded,
    });
    return (
        <li className="mrow">
            <span className="mrow__mark"><ComputerIcon size={15} /></span>
            <span className="mrow__body">
                <span className="mrow__line">
                    <span className="mrow__name">{server.name}</span>
                    <span className="mrow__detail">{server.models.length} {server.models.length === 1 ? "model" : "models"} · running on {noun}</span>
                </span>
                <span className="mrow__note">Free, and nothing leaves {noun}.</span>
                {add.isError && <span className="reachrow__error" role="alert">{friendlyError(add.error)}</span>}
            </span>
            <button className="btn mrow__add" disabled={add.isPending} onClick={() => add.mutate()}>
                <PlusIcon size={13} /> {add.isPending ? "Adding…" : "Add as provider"}
            </button>
        </li>
    );
}

function OperatorRow({ draft, orgId, onAdded, noun }: { draft: ProviderDraft; orgId: string; onAdded: () => void; noun: ComputerNoun }) {
    const [asking, setAsking] = useState(false);
    const [apiKey, setApiKey] = useState("");
    const add = useMutation({
        mutationFn: () => addToWorkspace(draft, apiKey, (key) => source.addProviderKey(orgId, key)),
        onSuccess: () => { setAsking(false); setApiKey(""); onAdded(); },
    });
    const start = () => (draft.needsKey && !asking ? setAsking(true) : add.mutate());
    return (
        <li className="mrow thismac-operator">
            <span className="mrow__mark"><KeyIcon size={15} /></span>
            <span className="mrow__body">
                <span className="mrow__line">
                    <span className="mrow__name">{draft.name}</span>
                    <span className="mrow__detail">{draft.models[0]} · set on {noun}</span>
                </span>
                <span className="mrow__note">
                    {capitalised(noun)} keeps using it for titles and summaries; adding it lets teammates pick it by name.
                </span>
                {asking && (
                    <span className="field thismac-operator__key">
                        <label htmlFor="this-mac-operator-key">API key for {draft.name}</label>
                        <input id="this-mac-operator-key" type="password" autoComplete="off" value={apiKey}
                            onChange={(event) => setApiKey(event.target.value)} />
                        <span>Lemma can’t read back the key stored on {noun}, so it is asked for once more.</span>
                    </span>
                )}
                {add.isError && <span className="reachrow__error" role="alert">{friendlyError(add.error)}</span>}
            </span>
            <button className="btn mrow__add" disabled={add.isPending || (asking && !apiKey.trim())} onClick={start}>
                <PlusIcon size={13} /> {add.isPending ? "Adding…" : "Add to workspace"}
            </button>
        </li>
    );
}
