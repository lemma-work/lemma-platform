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
import { SecretInput } from "@/ui/form";

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
        <section className="kset-stack" aria-label={"Found on " + noun}>
            <p className="ksub">On {noun} <span>Not in this list yet</span></p>
            <ul className="klist">
                {offerOperator && <OperatorRow draft={offerOperator} orgId={orgId} onAdded={onAdded} noun={noun} />}
                {detected.map((server) => <ServerRow key={server.id} server={server} orgId={orgId} onAdded={onAdded} noun={noun} />)}
            </ul>
        </section>
    );
}

/** A model server answering on this computer, one click from being a
 *  provider Lemmas can pick. */
function ServerRow({ server, orgId, onAdded, noun }: { server: DetectedServer; orgId: string; onAdded: () => void; noun: ComputerNoun }) {
    const add = useMutation({
        mutationFn: () => source.addProviderKey(orgId, {
            protocol: "openai", name: server.name, baseUrl: server.baseUrl, apiKey: LOCAL_SERVER_KEY, models: server.models,
        }),
        onSuccess: onAdded,
    });
    return (
        <li className="krow">
            <span className="krow__lead">
                <span className="kmark" aria-hidden="true"><ComputerIcon /></span>
                <span className="krow__v">
                    <b>{server.name}</b>
                    <span>{server.models.length} {server.models.length === 1 ? "model" : "models"} · running on {noun}</span>
                    <span className="kset-long">Free, and nothing leaves {noun}.</span>
                    {add.isError && <span className="kset-long kset-said--bad" role="alert">{friendlyError(add.error)}</span>}
                </span>
            </span>
            <span className="krow__end">
                <button type="button" className="b b--s b--secondary" disabled={add.isPending} onClick={() => add.mutate()}>
                    <PlusIcon /> {add.isPending ? "Adding…" : "Add as provider"}
                </button>
            </span>
        </li>
    );
}

/** The provider this computer was set up with, offered to the organization;
 *  its key is asked for once more, since it cannot be read back. */
function OperatorRow({ draft, orgId, onAdded, noun }: { draft: ProviderDraft; orgId: string; onAdded: () => void; noun: ComputerNoun }) {
    const [asking, setAsking] = useState(false);
    const [apiKey, setApiKey] = useState("");
    const add = useMutation({
        mutationFn: () => addToWorkspace(draft, apiKey, (key) => source.addProviderKey(orgId, key)),
        onSuccess: () => { setAsking(false); setApiKey(""); onAdded(); },
    });
    const start = () => (draft.needsKey && !asking ? setAsking(true) : add.mutate());
    return (
        <li className="krow">
            <span className="krow__lead">
                <span className="kmark" aria-hidden="true"><KeyIcon /></span>
                <span className="krow__v">
                    <b>{draft.name}</b>
                    <span>{draft.models[0]} · set on {noun}</span>
                    <span className="kset-long">
                        {capitalised(noun)} keeps using it for titles and summaries; adding it makes it a model you can pick by name.
                    </span>
                    {asking && (
                        <span className="kfield kset-long">
                            <label htmlFor={"operator-key-" + draft.name}>API key for {draft.name}</label>
                            <SecretInput id={"operator-key-" + draft.name} className="kset-input" label={"API key for " + draft.name}
                                value={apiKey} onChange={(event) => setApiKey(event.target.value)} />
                            <small>Lemma can’t read back the key stored on {noun}, so it is asked for once more.</small>
                        </span>
                    )}
                    {add.isError && <span className="kset-long kset-said--bad" role="alert">{friendlyError(add.error)}</span>}
                </span>
            </span>
            <span className="krow__end">
                <button type="button" className="b b--s b--secondary" disabled={add.isPending || (asking && !apiKey.trim())} onClick={start}>
                    <PlusIcon /> {add.isPending ? "Adding…" : "Add to workspace"}
                </button>
            </span>
        </li>
    );
}
