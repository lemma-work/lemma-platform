"use client";

import { useState } from "react";
import { accountName } from "@/data";
import { LoadingIndicator } from "@/ui/loading";
import { RotateDialog } from "./connect-dialog";
import { connectorProblem } from "./install";
import type { OrgConnect } from "./org-connect";
import { useBindInstallation, useFinishInstall, type InstallationChoice } from "./queries";
import { completionPath, hereWith, openAuthorization } from "./round-trip";
import { ServiceMark } from "./service-mark";
import { fixWord, type Need, type Service } from "./services";

/** One thing that needs a person, with the fix beside it.
 *
 *  Each kind of trouble needs a different act, and "reconnect" is advice that
 *  cannot succeed when the account is waiting on an owner or on a GitHub
 *  install — so the button is the one that fixes this kind, and a kind with
 *  nothing to press here says who it waits on instead. The same row stands in
 *  the page's band and at the top of a service's sheet. */
export function NeedRow({ orgId, service, need, flow, onChanged, mark = true }: {
    orgId: string;
    service: Service;
    need: Need;
    flow: OrgConnect;
    onChanged: () => void;
    /** The service's mark at its left: in the page's band, where rows of
     *  different services stand together; not in the service's own sheet. */
    mark?: boolean;
}) {
    const finish = useFinishInstall(orgId, () => completionPath(hereWith()));
    const bind = useBindInstallation(orgId);
    const [choices, setChoices] = useState<InstallationChoice[] | null>(null);
    const [note, setNote] = useState<string | null>(null);
    const [rotating, setRotating] = useState(false);
    const who = need.account ? accountName(need.account, service.entry.title) : "";
    const fix = fixWord(need);

    /* GitHub is asked first: somebody who installed it from GitHub's own page
       is already done. */
    const startFinish = (account: NonNullable<Need["account"]>) => {
        setNote(null);
        finish.mutate(account.id, {
            onSuccess: (step) => {
                if ("done" in step) { setNote("Installation found."); onChanged(); }
                else if ("choices" in step) setChoices(step.choices);
                else if (!step.url) setNote("GitHub offered no install page.");
                else if (openAuthorization(step.url, service.entry.title)) setNote("Finish in the GitHub tab.");
                else window.location.assign(step.url);
            },
            onError: (problem) => setNote(connectorProblem(problem, "The installation could not be started.")),
        });
    };
    const press = () => {
        if (need.kind === "reconnect" || need.kind === "sign-in") flow.signIn(service.entry, need.install?.id ?? null);
        else if (need.kind === "replace") setRotating(true);
        else if (need.account) startFinish(need.account);
    };
    const installing = need.kind === "install" || need.kind === "choose";
    const busy = installing ? finish.isPending : flow.starting(service.entry.id, need.install?.id ?? null);

    return (
        <div className="krow kconn-need">
            <span className="krow__lead">
                {mark && <ServiceMark entry={service.entry} kind={service.kind} />}
                <span className="krow__v">
                    <b>{mark ? service.title : who || service.title}</b>
                    <span>{note ?? (mark && who ? who + " · " + need.said : need.said)}</span>
                </span>
            </span>
            <span className="krow__end">
                {choices ? (
                    <>
                        {choices.length === 0 && <span className="kconn-quiet">No installations to choose from.</span>}
                        {choices.map((choice) => (
                            <button key={choice.installation_id} type="button" className="b b--s b--secondary" disabled={bind.isPending || !need.account}
                                onClick={() => need.account && bind.mutate({ accountId: need.account.id, installationId: choice.installation_id }, {
                                    onSuccess: () => { setChoices(null); onChanged(); },
                                    onError: (problem) => setNote(connectorProblem(problem, "That installation could not be used.")),
                                })}>
                                {choice.account_login || choice.installation_id}
                            </button>
                        ))}
                        <button type="button" className="b b--s b--quiet" onClick={() => setChoices(null)}>Cancel</button>
                    </>
                ) : fix ? (
                    <button type="button" className="b b--s b--primary" disabled={busy} onClick={press}>
                        {busy ? <LoadingIndicator inline label={installing ? "Checking" : "Opening"} /> : fix}
                    </button>
                ) : null}
            </span>
            {rotating && need.account && (
                <RotateDialog
                    orgId={orgId}
                    connector={service.entry}
                    install={need.install ?? service.installs[0] ?? null}
                    accountId={need.account.id}
                    accountName={who}
                    onClose={() => setRotating(false)}
                    onDone={() => { setRotating(false); onChanged(); }}
                />
            )}
        </div>
    );
}
