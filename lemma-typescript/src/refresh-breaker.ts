/**
 * A ceiling on session refreshes across every request a page makes.
 *
 * `maxRetryAttemptsForSessionRefresh` bounds one request, and only while the
 * refresh keeps answering 200 with the retried request still answering 401.
 * Nothing bounds the page. A refresh that answers 5xx, or fails in transport,
 * leaves the local session in place and throws to the one caller — so the next
 * request to meet a 401 refreshes again, and a screen polling every 1.5 s
 * refreshes every 1.5 s for as long as it stays open. The same holds when the
 * refresh succeeds but the cookie it set never comes back (a cross-site dev
 * origin, a browser holding duplicate session cookies): each request spends its
 * three attempts, and the next one starts over.
 *
 * So this counts refreshes, not outcomes. A healthy session refreshes about
 * once per access-token lifetime, which is an hour by default. `budget`
 * refreshes inside `windowMs` is a session that refreshing is not fixing, and
 * every such case ends the same way: stop calling the endpoint for a while,
 * and tell the app the session is not usable so it stops making the requests
 * that trigger refreshes.
 *
 * Once tripped, further refreshes are refused without touching the network
 * until the cooldown passes, and the cooldown doubles on every trip until the
 * breaker has been quiet for `forgetAfterMs`.
 */

export interface RefreshBreakerOptions {
  /** Refreshes allowed inside one window before the breaker trips. */
  budget?: number;
  windowMs?: number;
  /** The first cooldown; each further trip doubles it up to `maxCooldownMs`. */
  cooldownMs?: number;
  maxCooldownMs?: number;
  /** How long without a trip before the cooldown goes back to `cooldownMs`. */
  forgetAfterMs?: number;
  now?: () => number;
  /** Called once per trip, when refreshing is suspended. */
  onTrip?: (retryAt: number) => void;
}

/** Thrown in place of a refresh the breaker refused. Callers see a failed
 *  request, exactly as they would for a refresh the server refused. */
export class RefreshSuspendedError extends Error {
  readonly retryAt: number;

  constructor(retryAt: number) {
    super("Session refresh is paused after repeated failures.");
    this.name = "RefreshSuspendedError";
    this.retryAt = retryAt;
  }
}

export interface RefreshBreaker {
  /** Throws `RefreshSuspendedError` when a refresh must not be made now. */
  admit(): void;
  /** When refreshing may resume, or null while it is allowed. */
  suspendedUntil(): number | null;
}

/**
 * Four in a minute leaves room for the one legitimate burst: a browser
 * migrating off duplicate cookies spends two refreshes on one request, and a
 * second tab racing the refresh lock can spend a third.
 */
export function createRefreshBreaker(options: RefreshBreakerOptions = {}): RefreshBreaker {
  const budget = options.budget ?? 4;
  const windowMs = options.windowMs ?? 60_000;
  const cooldownMs = options.cooldownMs ?? 30_000;
  const maxCooldownMs = options.maxCooldownMs ?? 5 * 60_000;
  const forgetAfterMs = options.forgetAfterMs ?? 10 * 60_000;
  const now = options.now ?? Date.now;

  let attempts: number[] = [];
  let until = 0;
  let strikes = 0;
  let lastTrip = -Infinity;

  return {
    admit() {
      const at = now();
      if (at < until) throw new RefreshSuspendedError(until);
      if (at - lastTrip > forgetAfterMs) strikes = 0;

      attempts = attempts.filter((when) => at - when < windowMs);
      if (attempts.length >= budget) {
        until = at + Math.min(maxCooldownMs, cooldownMs * 2 ** strikes);
        strikes += 1;
        lastTrip = at;
        attempts = [];
        options.onTrip?.(until);
        throw new RefreshSuspendedError(until);
      }
      attempts.push(at);
    },
    suspendedUntil() {
      return now() < until ? until : null;
    },
  };
}
