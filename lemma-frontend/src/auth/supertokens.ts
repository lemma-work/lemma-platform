"use client";

import SuperTokens from "supertokens-web-js";
import Session from "supertokens-web-js/recipe/session";
import EmailPassword from "supertokens-web-js/recipe/emailpassword";
import ThirdParty from "supertokens-web-js/recipe/thirdparty";
import EmailVerification from "supertokens-web-js/recipe/emailverification";
import { createRefreshBreaker } from "lemma-sdk";
import { apiUrl, hasApiUrl } from "@/session/client";
import { ST_BASE } from "./config";
import { proofHeader, type Purpose } from "./altcha";
import { invitationIn, pendingDestination } from "./redirects";

/** Which actions the server guards, and what it calls each purpose.
 *
 *  Read off `auth_abuse`: sign-up and password reset are the expensive ones,
 *  and sign-in is guarded as a risk signal rather than on every attempt. An
 *  action absent here sends no proof, which is correct for the ones the server
 *  does not check — attaching one everywhere would cost every visitor a hash
 *  search for nothing. */
const GUARDED: Record<string, Purpose> = {
    EMAIL_PASSWORD_SIGN_UP: "signup",
    EMAIL_PASSWORD_SIGN_IN: "signin-risk",
    SEND_RESET_PASSWORD_EMAIL: "password-reset",
    SUBMIT_NEW_PASSWORD: "password-reset",
    SEND_VERIFY_EMAIL: "verification",
};

function apiBase(): { apiDomain: string; apiBasePath: string } {
    /* The same split `lemma-sdk` makes, and it has to stay the same: a portal
       that signed somebody in against a different base path than the session
       layer watches would set a cookie nothing reads. */
    const raw = hasApiUrl() ? apiUrl() : "";
    if (/^https?:\/\//.test(raw)) {
        const url = new URL(raw);
        const prefix = url.pathname.replace(/\/$/, "");
        return { apiDomain: url.origin, apiBasePath: (prefix === "/" ? "" : prefix) + ST_BASE };
    }
    return {
        apiDomain: typeof window === "undefined" ? "" : window.location.origin,
        apiBasePath: raw.replace(/\/$/, "") + ST_BASE,
    };
}

let started = false;

/* The same page-wide ceiling the SDK puts on the workspace's init, for the
   portal's. The verification screen polls, and a refresh that keeps failing
   there would otherwise be retried on every poll. Nothing here listens for a
   trip: the refused refresh fails the one call, and the screen says so. */
const refreshBreaker = createRefreshBreaker();

export function startAuth(): void {
    if (started || typeof window === "undefined") return;
    started = true;

    const { apiDomain, apiBasePath } = apiBase();

    SuperTokens.init({
        appInfo: { appName: "Lemma", apiDomain, apiBasePath },
        recipeList: [
            Session.init({
                tokenTransferMethod: "cookie",
                maxRetryAttemptsForSessionRefresh: 3,
                preAPIHook: async (context) => {
                    if (context.action === "REFRESH_SESSION") refreshBreaker.admit();
                    return context;
                },
            }),
            EmailPassword.init({
                preAPIHook: async (context) => {
                    const purpose = GUARDED[context.action];
                    const requestInit = context.action === "EMAIL_PASSWORD_SIGN_UP"
                        ? withInvitation(context.requestInit)
                        : context.requestInit;
                    if (!purpose) return { ...context, requestInit };
                    return { ...context, requestInit: await withProof(requestInit, purpose) };
                },
            }),
            ThirdParty.init(),
            EmailVerification.init({
                preAPIHook: async (context) => {
                    const purpose = GUARDED[context.action];
                    if (!purpose) return context;
                    return { ...context, requestInit: await withProof(context.requestInit, purpose) };
                },
            }),
        ],
    });
}

/** The invitation this sign-up is headed to accept, if any, as the API reads it. */
function withInvitation(requestInit: RequestInit): RequestInit {
    const invitation = invitationIn(pendingDestination(window.location.search), window.location.origin);
    if (!invitation) return requestInit;
    const headers = new Headers(requestInit.headers);
    headers.set("x-lemma-invitation", invitation);
    return { ...requestInit, headers };
}

async function withProof(requestInit: RequestInit, purpose: Purpose): Promise<RequestInit> {
    const proof = await proofHeader(purpose);
    if (Object.keys(proof).length === 0) return requestInit;
    const headers = new Headers(requestInit.headers);
    for (const [name, value] of Object.entries(proof)) headers.set(name, value);
    return { ...requestInit, headers };
}

export { EmailPassword, EmailVerification, Session, ThirdParty };
