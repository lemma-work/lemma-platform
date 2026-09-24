"use client";

import SuperTokens from "supertokens-web-js";
import Session from "supertokens-web-js/recipe/session";
import EmailPassword from "supertokens-web-js/recipe/emailpassword";
import ThirdParty from "supertokens-web-js/recipe/thirdparty";
import EmailVerification from "supertokens-web-js/recipe/emailverification";
import { apiUrl, hasApiUrl } from "@/session/client";
import { ST_BASE } from "./config";
import { proofHeader, type Purpose } from "./altcha";

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

export function startAuth(): void {
    if (started || typeof window === "undefined") return;
    started = true;

    const { apiDomain, apiBasePath } = apiBase();

    SuperTokens.init({
        appInfo: { appName: "Lemma", apiDomain, apiBasePath },
        recipeList: [
            Session.init({ tokenTransferMethod: "cookie", maxRetryAttemptsForSessionRefresh: 3 }),
            EmailPassword.init({
                preAPIHook: async (context) => {
                    const purpose = GUARDED[context.action];
                    if (!purpose) return context;
                    return { ...context, requestInit: await withProof(context.requestInit, purpose) };
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

async function withProof(requestInit: RequestInit, purpose: Purpose): Promise<RequestInit> {
    const proof = await proofHeader(purpose);
    if (Object.keys(proof).length === 0) return requestInit;
    const headers = new Headers(requestInit.headers);
    for (const [name, value] of Object.entries(proof)) headers.set(name, value);
    return { ...requestInit, headers };
}

export { EmailPassword, EmailVerification, Session, ThirdParty };
