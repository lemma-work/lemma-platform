/**
 * An app framed inside an AI tool's conversation — an MCP Apps host such as
 * ChatGPT — where Lemma's session cookie never arrives: the frame sits in
 * another site's page, and the cookie is `SameSite=Lax`. The Lemma view that
 * frames the app hands it what it needs by `postMessage` instead: a short-lived
 * token for the API and, for a private app, a ticket to its own files.
 *
 * The view marks the app's address with `lemma_embed=mcp`, so an app framed
 * anywhere else — the Lemma workspace frames apps too — never waits on a
 * parent that will not answer. These names are the view's as well
 * (lemma-backend/app/modules/agent/services/pod_mcp_app_view.html).
 */

export const EMBED_PARAM = "lemma_embed";
export const EMBED_VALUE = "mcp";
export const TOKEN_REQUEST = "lemma:token-request";
export const TOKEN_MESSAGE = "lemma:token";
export const ACCESS_REQUEST = "lemma:app-access-request";
export const ACCESS_MESSAGE = "lemma:app-access";

const REMEMBERED_KEY = "lemma:embedded";
const ANSWER_TIMEOUT_MS = 20_000;
/** Ask for the next token this long before the current one stops working. */
const RENEW_BEFORE_MS = 60_000;

/**
 * True for an app the Lemma view framed. Remembered for the frame's life: an
 * app routes away from the address it was opened at, and a reload there still
 * has no session to fall back on.
 */
export function isEmbeddedInHost(): boolean {
  if (typeof window === "undefined" || window.parent === window) return false;
  let marked = false;
  try {
    marked = new URL(window.location.href).searchParams.get(EMBED_PARAM) === EMBED_VALUE;
  } catch {
    marked = false;
  }
  try {
    if (marked) window.sessionStorage.setItem(REMEMBERED_KEY, "1");
    else marked = window.sessionStorage.getItem(REMEMBERED_KEY) === "1";
  } catch {
    // Storage refused: the address alone decides.
  }
  return marked;
}

/** The view answered, and the answer was no: the app is not this person's to open here. */
export class HostRefusedError extends Error {}

interface HostAnswer {
  type?: unknown;
  id?: unknown;
  error?: unknown;
  [key: string]: unknown;
}

let asked = 0;

/** Ask the framing view one question and wait for its answer to that question. */
function askHost(requestType: string, answerType: string): Promise<HostAnswer> {
  const id = `${requestType}:${Date.now().toString(36)}:${(asked++).toString(36)}`;
  return new Promise<HostAnswer>((resolve, reject) => {
    const timer = setTimeout(() => {
      window.removeEventListener("message", listen);
      reject(new Error("The Lemma view did not answer"));
    }, ANSWER_TIMEOUT_MS);
    function listen(event: MessageEvent): void {
      // Only the frame's own parent: nothing else on the page can be its source.
      if (event.source !== window.parent) return;
      const data = event.data as HostAnswer | null;
      if (!data || data.type !== answerType || data.id !== id) return;
      clearTimeout(timer);
      window.removeEventListener("message", listen);
      if (typeof data.error === "string") reject(new HostRefusedError(data.error));
      else resolve(data);
    }
    window.addEventListener("message", listen);
    // The question carries nothing; the view answers only the frame it made.
    window.parent.postMessage({ type: requestType, id }, "*");
  });
}

/** The token a framed app's requests carry, kept fresh by asking the view. */
export class EmbeddedCredentials {
  private token: string | null = null;
  private expiresAt = Number.NaN;
  private renewing: Promise<string> | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;

  current(): string | null {
    return this.token;
  }

  /** The token, asking the view for the first one. */
  ready(): Promise<string> {
    return this.token ? Promise.resolve(this.token) : this.renew();
  }

  /** A fresh token. Callers asking at once share one question. */
  renew(): Promise<string> {
    if (!this.renewing) {
      this.renewing = askHost(TOKEN_REQUEST, TOKEN_MESSAGE)
        .then((answer) => {
          if (typeof answer.token !== "string" || !answer.token) {
            throw new Error("The Lemma view sent no token");
          }
          this.token = answer.token;
          this.expiresAt = typeof answer.expiresAt === "string" ? Date.parse(answer.expiresAt) : Number.NaN;
          this.renewBeforeExpiry();
          return answer.token;
        })
        .finally(() => {
          this.renewing = null;
        });
    }
    return this.renewing;
  }

  private renewBeforeExpiry(): void {
    clearTimeout(this.timer);
    if (!Number.isFinite(this.expiresAt)) return;
    const wait = Math.max(0, this.expiresAt - Date.now() - RENEW_BEFORE_MS);
    this.timer = setTimeout(() => {
      // A miss here is retried by the next request's 401.
      this.renew().catch(() => undefined);
    }, wait);
  }
}

/** For a private app's sign-in page: a ticket to this app host's files. */
export async function askHostForAppAccess(): Promise<string> {
  const answer = await askHost(ACCESS_REQUEST, ACCESS_MESSAGE);
  if (typeof answer.ticket !== "string" || !answer.ticket) {
    throw new Error("The Lemma view sent no ticket");
  }
  return answer.ticket;
}
