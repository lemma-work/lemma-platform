import type { ConnectOutcome } from "../connect/outcome";
import { accountTrouble, type ConnectorAccount } from "@web/data/accounts";
import type { AccountBefore, InstallStep, PendingSignIn, SignInResult } from "./source";

/** Reading a provider sign-in back off the accounts, and saying how it went.
 *
 *  The provider's page opens in the phone's browser and, today, ends on the
 *  web's own page, which the person closes (`connect/round-trip.ts`). So
 *  what the sign-in did is read off the accounts: one usable now that was
 *  not before (new, or brought back to health: `findAccount`'s rule), or a
 *  new one that cannot reach anything yet, which `findAccount` alone would
 *  miss and report as nothing. Pure, so `tests/` can hold it. */

export { PHONE_RETURN } from "../connect/outcome";

export function snapshot(accounts: readonly ConnectorAccount[]): AccountBefore[] {
    return accounts.map((account) => ({ id: account.id, usable: account.usable, status: account.status, installState: account.installState }));
}

export function readSignIn(pending: PendingSignIn, after: readonly ConnectorAccount[], outcome: ConnectOutcome | null = null): SignInResult {
    if (outcome?.connect === "error") return { kind: "refused", reason: outcome.reason || "The account was not connected." };
    const was = new Map(pending.before.map((one) => [one.id, one]));
    const here = after.filter((account) => account.connectorId === pending.connectorId
        && (!pending.authConfigId || !account.authConfigId || account.authConfigId === pending.authConfigId));
    const fresh = here.find((account) => account.usable && was.get(account.id)?.usable !== true);
    if (fresh) return { kind: "connected", accountId: fresh.id };
    /* New, or changed, and still unable to reach anything: a GitHub App
       authorised and not installed, or waiting on an owner. */
    const moved = here.find((account) => {
        if (account.usable) return false;
        const held = was.get(account.id);
        return !held || held.status !== account.status || held.installState !== account.installState;
    }) ?? (outcome?.account ? here.find((account) => account.id === outcome.account && !account.usable) : undefined);
    if (moved) return { kind: "unfinished", accountId: moved.id, trouble: accountTrouble(moved) };
    return { kind: "nothing", pending };
}

/** How a sign-in went, said to a person, and whether it went wrong. */
export function signInWords(result: SignInResult, title: string): { text: string; bad: boolean } {
    switch (result.kind) {
        case "connected": return { text: `Connected to ${title}.`, bad: false };
        case "unfinished": return { text: `Almost there. ${result.trouble}.`, bad: false };
        case "nothing": return { text: `Nothing new yet. If you finished signing in to ${title}, it may still be on its way.`, bad: false };
        case "refused": return { text: result.reason, bad: true };
        case "no-page": return { text: "This one offered no way to sign in. It may need an app of your own first.", bad: true };
    }
}

/** How finishing an install went, said to a person. */
export function installWords(step: InstallStep, title: string): { text: string; bad: boolean } {
    switch (step.kind) {
        case "installed": return { text: "Installation found. It can reach what it was given now.", bad: false };
        case "choices": return { text: step.choices.length ? "It can reach more than one installation. Pick the one it speaks for." : "No installations to choose from.", bad: !step.choices.length };
        case "not-yet": return { text: `Not installed yet. If you finished on ${title}, check again in a moment.`, bad: false };
        case "no-page": return { text: `${title} offered no install page.`, bad: true };
    }
}

/** "All repositories" or "Selected repositories", for a GitHub installation. */
export function repositoriesWords(selection: string | null): string | null {
    if (selection === "all") return "All repositories";
    if (selection === "selected") return "Selected repositories";
    return null;
}
