import { useState, type CSSProperties, type ReactNode } from "react";

export interface AppIdentity {
  name?: string;
  description?: string;
  icon?: ReactNode;
  iconUrl?: string;
}

export interface AppAppearance {
  accent?: string;
  accentForeground?: string;
  background?: string;
  surface?: string;
  text?: string;
  muted?: string;
  border?: string;
  radius?: number | string;
}

export interface AppUserIdentity {
  email?: string | null;
  name?: string | null;
  first_name?: string | null;
  last_name?: string | null;
}

export interface AppLoaderProps {
  app?: AppIdentity;
  appearance?: AppAppearance;
  message?: string;
  fullScreen?: boolean;
}

export interface AppSignInProps {
  app?: AppIdentity;
  appearance?: AppAppearance;
  onSignIn: () => void;
  fullScreen?: boolean;
}

export type AppAccessStatus = "missing" | "pending" | "error";

export interface AppAccessProps {
  app?: AppIdentity;
  appearance?: AppAppearance;
  status: AppAccessStatus;
  user?: AppUserIdentity | null;
  isSubmitting?: boolean;
  error?: Error | string | null;
  onRequestAccess?: () => void | Promise<void>;
  onRefresh?: () => void | Promise<void>;
  onSwitchAccount?: () => void | Promise<void>;
  fullScreen?: boolean;
}

const APP_GATE_CSS = `
.lemma-app-gate {
  --lap-accent: #6757f5;
  --lap-accent-foreground: #ffffff;
  --lap-background: #f5f5f2;
  --lap-surface: rgba(255, 255, 255, .82);
  --lap-text: #17171b;
  --lap-muted: #6f6f78;
  --lap-border: rgba(23, 23, 27, .1);
  --lap-radius: 28px;
  position: relative;
  isolation: isolate;
  box-sizing: border-box;
  width: 100%;
  min-height: 100%;
  overflow: hidden;
  color: var(--lap-text);
  background:
    radial-gradient(circle at 10% 8%, color-mix(in srgb, var(--lap-accent) 12%, transparent), transparent 31rem),
    radial-gradient(circle at 92% 86%, color-mix(in srgb, var(--lap-accent) 8%, transparent), transparent 30rem),
    var(--lap-background);
  font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  -webkit-font-smoothing: antialiased;
}
.lemma-app-gate[data-full-screen="true"] { min-height: 100vh; }
.lemma-app-gate *, .lemma-app-gate *::before, .lemma-app-gate *::after { box-sizing: border-box; }
.lemma-app-gate::before {
  content: "";
  position: absolute;
  z-index: -1;
  inset: 0;
  opacity: .2;
  background-image: linear-gradient(rgba(22, 22, 27, .11) 1px, transparent 1px), linear-gradient(90deg, rgba(22, 22, 27, .11) 1px, transparent 1px);
  background-size: 48px 48px;
  mask-image: radial-gradient(circle at center, black, transparent 72%);
}
.lemma-app-stage {
  width: min(100%, 1080px);
  min-height: inherit;
  margin: 0 auto;
  padding: clamp(28px, 6vw, 72px);
  display: grid;
  place-items: center;
}
.lemma-app-panel {
  width: min(100%, 472px);
  animation: lemma-panel-in .56s cubic-bezier(.2, .85, .24, 1) both;
}
.lemma-app-card {
  position: relative;
  padding: clamp(28px, 5vw, 42px);
  overflow: hidden;
  border: 1px solid var(--lap-border);
  border-radius: var(--lap-radius);
  background: var(--lap-surface);
  box-shadow: 0 32px 90px rgba(18, 18, 24, .1), 0 2px 8px rgba(18, 18, 24, .04);
  backdrop-filter: blur(22px) saturate(1.15);
}
.lemma-app-card::after {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  border-radius: inherit;
  box-shadow: inset 0 1px rgba(255, 255, 255, .8);
}
.lemma-app-eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
  padding: 0 11px;
  border: 1px solid var(--lap-border);
  border-radius: 999px;
  color: var(--lap-muted);
  background: color-mix(in srgb, var(--lap-surface) 72%, transparent);
  font-size: 11px;
  font-weight: 680;
  letter-spacing: .08em;
  text-transform: uppercase;
}
.lemma-app-eyebrow-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--lap-accent);
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--lap-accent) 13%, transparent);
}
.lemma-app-identity { display: flex; align-items: center; gap: 16px; margin: 28px 0 22px; }
.lemma-app-icon {
  width: 58px;
  height: 58px;
  flex: 0 0 58px;
  display: grid;
  place-items: center;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--lap-accent) 20%, var(--lap-border));
  border-radius: 18px;
  color: var(--lap-accent-foreground);
  background: linear-gradient(145deg, color-mix(in srgb, var(--lap-accent) 82%, white), var(--lap-accent));
  box-shadow: 0 14px 30px color-mix(in srgb, var(--lap-accent) 24%, transparent), inset 0 1px rgba(255,255,255,.28);
  font-size: 19px;
  font-weight: 760;
  letter-spacing: -.04em;
}
.lemma-app-icon img { width: 100%; height: 100%; object-fit: cover; }
.lemma-app-card > .lemma-app-identity:first-child { margin-top: 0; }
.lemma-app-title { margin: 0; color: var(--lap-text); font-size: clamp(25px, 5vw, 34px); font-weight: 690; letter-spacing: -.045em; line-height: 1.08; }
.lemma-app-copy { margin: 12px 0 0; color: var(--lap-muted); font-size: 15px; line-height: 1.62; }
.lemma-app-actions { display: grid; gap: 10px; margin-top: 28px; }
.lemma-app-button {
  min-height: 50px;
  width: 100%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 0 18px;
  border: 0;
  border-radius: 15px;
  color: var(--lap-accent-foreground);
  background: var(--lap-accent);
  box-shadow: 0 12px 24px color-mix(in srgb, var(--lap-accent) 24%, transparent), inset 0 1px rgba(255,255,255,.2);
  font: inherit;
  font-size: 14px;
  font-weight: 680;
  cursor: pointer;
  transition: transform .18s ease, box-shadow .18s ease, filter .18s ease;
}
.lemma-app-button:hover:not(:disabled) { transform: translateY(-1px); filter: brightness(1.035); box-shadow: 0 16px 30px color-mix(in srgb, var(--lap-accent) 29%, transparent), inset 0 1px rgba(255,255,255,.2); }
.lemma-app-button:active:not(:disabled) { transform: translateY(0); }
.lemma-app-button:disabled { cursor: wait; opacity: .66; }
.lemma-app-button svg { width: 17px; height: 17px; transition: transform .18s ease; }
.lemma-app-button:hover:not(:disabled) svg { transform: translateX(2px); }
.lemma-app-secondary {
  min-height: 42px;
  border: 0;
  border-radius: 12px;
  color: var(--lap-muted);
  background: transparent;
  font: inherit;
  font-size: 13px;
  font-weight: 620;
  cursor: pointer;
}
.lemma-app-secondary:hover { color: var(--lap-text); background: color-mix(in srgb, var(--lap-text) 5%, transparent); }
.lemma-app-button:focus-visible, .lemma-app-secondary:focus-visible { outline: 3px solid color-mix(in srgb, var(--lap-accent) 30%, transparent); outline-offset: 3px; }
.lemma-app-user {
  display: flex;
  align-items: center;
  gap: 11px;
  margin-top: 22px;
  padding: 12px 13px;
  border: 1px solid var(--lap-border);
  border-radius: 15px;
  background: color-mix(in srgb, var(--lap-background) 54%, transparent);
}
.lemma-app-avatar { width: 32px; height: 32px; display: grid; place-items: center; border-radius: 10px; color: var(--lap-text); background: color-mix(in srgb, var(--lap-accent) 12%, var(--lap-background)); font-size: 12px; font-weight: 720; }
.lemma-app-user-copy { min-width: 0; }
.lemma-app-user-name, .lemma-app-user-email { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lemma-app-user-name { color: var(--lap-text); font-size: 13px; font-weight: 650; }
.lemma-app-user-email { margin-top: 2px; color: var(--lap-muted); font-size: 12px; }
.lemma-app-alert { margin-top: 18px; padding: 12px 14px; border: 1px solid color-mix(in srgb, #d84b4b 26%, transparent); border-radius: 14px; color: #9f2f35; background: color-mix(in srgb, #e65050 8%, transparent); font-size: 13px; line-height: 1.5; }
.lemma-app-lemma-mark { width: 16px; height: 16px; display: inline-flex; align-items: flex-end; justify-content: center; gap: 2px; color: currentColor; }
.lemma-app-lemma-bar { width: 3px; border-radius: 1.5px; background: currentColor; }
.lemma-app-lemma-bar:nth-child(1) { height: 7px; }
.lemma-app-lemma-bar:nth-child(2) { height: 11px; }
.lemma-app-lemma-bar:nth-child(3) { height: 15px; }
.lemma-app-button .lemma-app-lemma-mark { width: 15px; height: 15px; margin-right: 1px; }
.lemma-app-button .lemma-app-lemma-bar { width: 2.5px; }
.lemma-app-loader { width: min(100%, 440px); text-align: center; animation: lemma-panel-in .5s ease both; }
.lemma-app-loader .lemma-app-icon { margin: 0 auto 28px; }
.lemma-app-loader-title { margin: 0; color: var(--lap-text); font-size: 18px; font-weight: 680; letter-spacing: -.025em; }
.lemma-app-loader-copy { margin: 8px 0 0; color: var(--lap-muted); font-size: 13px; }
.lemma-app-trail { position: relative; width: 284px; height: 62px; margin: 30px auto 0; display: flex; align-items: center; justify-content: space-between; }
.lemma-app-trail::before { content: ""; position: absolute; left: 25px; right: 25px; top: 30px; height: 1px; background: linear-gradient(90deg, transparent, var(--lap-border) 12%, var(--lap-border) 88%, transparent); }
.lemma-app-node { position: relative; z-index: 1; width: 48px; height: 42px; display: grid; place-items: center; border: 1px solid var(--lap-border); border-radius: 13px; color: var(--lap-muted); background: color-mix(in srgb, var(--lap-surface) 92%, transparent); box-shadow: 0 8px 22px rgba(20,20,26,.06); }
.lemma-app-node svg { width: 21px; height: 21px; }
.lemma-app-pulse { position: absolute; z-index: 2; left: 22px; top: 27px; width: 7px; height: 7px; border-radius: 50%; background: var(--lap-accent); box-shadow: 0 0 0 6px color-mix(in srgb, var(--lap-accent) 13%, transparent), 0 0 22px color-mix(in srgb, var(--lap-accent) 70%, transparent); animation: lemma-travel 2.2s cubic-bezier(.55,0,.28,1) infinite; }
.lemma-app-node:nth-of-type(1) { animation: lemma-node-glow 2.2s ease-in-out infinite 0s; }
.lemma-app-node:nth-of-type(2) { animation: lemma-node-glow 2.2s ease-in-out infinite .7s; }
.lemma-app-node:nth-of-type(3) { animation: lemma-node-glow 2.2s ease-in-out infinite 1.4s; }
@keyframes lemma-panel-in { from { opacity: 0; transform: translateY(10px) scale(.985); } to { opacity: 1; transform: translateY(0) scale(1); } }
@keyframes lemma-travel { 0% { transform: translateX(0); opacity: 0; } 8% { opacity: 1; } 92% { opacity: 1; } 100% { transform: translateX(232px); opacity: 0; } }
@keyframes lemma-node-glow { 0%, 25%, 100% { color: var(--lap-muted); border-color: var(--lap-border); } 10% { color: var(--lap-accent); border-color: color-mix(in srgb, var(--lap-accent) 42%, var(--lap-border)); box-shadow: 0 10px 28px color-mix(in srgb, var(--lap-accent) 13%, transparent); } }
@media (prefers-color-scheme: dark) {
  .lemma-app-gate {
    --lap-background: #111114;
    --lap-surface: rgba(27, 27, 32, .84);
    --lap-text: #f2f1f4;
    --lap-muted: #aaa8b1;
    --lap-border: rgba(255,255,255,.1);
  }
  .lemma-app-card::after { box-shadow: inset 0 1px rgba(255,255,255,.08); }
  .lemma-app-alert { color: #ffb8bb; }
}
@media (max-width: 540px) {
  .lemma-app-stage { align-items: end; padding: 18px; }
  .lemma-app-card { padding: 28px 23px; border-radius: 24px; }
  .lemma-app-panel { width: 100%; }
  .lemma-app-trail { width: 250px; }
  .lemma-app-pulse { animation-name: lemma-travel-mobile; }
  @keyframes lemma-travel-mobile { 0% { transform: translateX(0); opacity: 0; } 8% { opacity: 1; } 92% { opacity: 1; } 100% { transform: translateX(198px); opacity: 0; } }
}
@media (prefers-reduced-motion: reduce) {
  .lemma-app-panel, .lemma-app-loader, .lemma-app-pulse, .lemma-app-node { animation: none; }
  .lemma-app-button { transition: none; }
}
`;

function appearanceStyle(appearance?: AppAppearance): CSSProperties {
  const style: Record<string, string> = {};
  if (appearance?.accent) style["--lap-accent"] = appearance.accent;
  if (appearance?.accentForeground) style["--lap-accent-foreground"] = appearance.accentForeground;
  if (appearance?.background) style["--lap-background"] = appearance.background;
  if (appearance?.surface) style["--lap-surface"] = appearance.surface;
  if (appearance?.text) style["--lap-text"] = appearance.text;
  if (appearance?.muted) style["--lap-muted"] = appearance.muted;
  if (appearance?.border) style["--lap-border"] = appearance.border;
  if (appearance?.radius != null) {
    style["--lap-radius"] = typeof appearance.radius === "number" ? `${appearance.radius}px` : appearance.radius;
  }
  return style as CSSProperties;
}

function appName(app?: AppIdentity): string {
  return app?.name?.trim() || "Your Lemma app";
}

function initials(value: string): string {
  const words = value.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "L";
  return `${words[0]?.[0] ?? ""}${words.length > 1 ? words[words.length - 1]?.[0] ?? "" : ""}`.toUpperCase();
}

function AppIcon({ app }: { app?: AppIdentity }) {
  const name = appName(app);
  return (
    <div className="lemma-app-icon" aria-hidden="true">
      {app?.icon ?? (app?.iconUrl ? <img src={app.iconUrl} alt="" /> : initials(name))}
    </div>
  );
}

function LemmaMark() {
  return (
    <span className="lemma-app-lemma-mark" aria-hidden="true">
      <span className="lemma-app-lemma-bar" />
      <span className="lemma-app-lemma-bar" />
      <span className="lemma-app-lemma-bar" />
    </span>
  );
}

function AppCanvas({
  appearance,
  fullScreen = true,
  children,
}: {
  appearance?: AppAppearance;
  fullScreen?: boolean;
  children: ReactNode;
}) {
  return (
    <main
      className="lemma-app-gate"
      data-full-screen={fullScreen ? "true" : "false"}
      style={appearanceStyle(appearance)}
    >
      <style>{APP_GATE_CSS}</style>
      <div className="lemma-app-stage">{children}</div>
    </main>
  );
}

function ArrowIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M4 10h11M11 6l4 4-4 4" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function AppTrail() {
  return (
    <div className="lemma-app-trail" aria-hidden="true">
      <span className="lemma-app-pulse" />
      <span className="lemma-app-node">
        <svg viewBox="0 0 24 24" fill="none"><path d="M6 5.5h12M6 10h8M6 14.5h12M6 19h7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
      </span>
      <span className="lemma-app-node">
        <svg viewBox="0 0 24 24" fill="none"><rect x="4.5" y="5" width="15" height="14" rx="2.5" stroke="currentColor" strokeWidth="1.5" /><path d="M4.5 10h15M10 10v9" stroke="currentColor" strokeWidth="1.5" /></svg>
      </span>
      <span className="lemma-app-node">
        <svg viewBox="0 0 24 24" fill="none"><path d="M5 6.5h14v9H11l-4 3v-3H5v-9Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" /><path d="M8.5 10h7M8.5 12.8h4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg>
      </span>
    </div>
  );
}

export function AppLoader({
  app,
  appearance,
  message,
  fullScreen = true,
}: AppLoaderProps) {
  const name = appName(app);
  return (
    <AppCanvas appearance={appearance} fullScreen={fullScreen}>
      <div className="lemma-app-loader" role="status" aria-live="polite">
        <AppIcon app={app} />
        <h1 className="lemma-app-loader-title">{name}</h1>
        <p className="lemma-app-loader-copy">{message ?? "Bringing your app into focus"}</p>
        <AppTrail />
      </div>
    </AppCanvas>
  );
}

export function AppSignIn({
  app,
  appearance,
  onSignIn,
  fullScreen = true,
}: AppSignInProps) {
  const [isRedirecting, setIsRedirecting] = useState(false);
  const name = appName(app);
  const handleSignIn = () => {
    setIsRedirecting(true);
    onSignIn();
  };

  return (
    <AppCanvas appearance={appearance} fullScreen={fullScreen}>
      <div className="lemma-app-panel">
        <section className="lemma-app-card" aria-labelledby="lemma-app-signin-title">
          <div className="lemma-app-identity"><AppIcon app={app} /></div>
          <h1 className="lemma-app-title" id="lemma-app-signin-title">Open {name}</h1>
          <p className="lemma-app-copy">
            {app?.description?.trim() || "Sign in with your Lemma account to continue to this app."}
          </p>
          <div className="lemma-app-actions">
            <button className="lemma-app-button" type="button" onClick={handleSignIn} disabled={isRedirecting}>
              {!isRedirecting ? <LemmaMark /> : null}
              {isRedirecting ? "Taking you to Lemma…" : "Login with Lemma"}
              {!isRedirecting ? <ArrowIcon /> : null}
            </button>
          </div>
        </section>
      </div>
    </AppCanvas>
  );
}

function userName(user?: AppUserIdentity | null): string {
  if (!user) return "Signed-in account";
  if (user.name?.trim()) return user.name.trim();
  const joined = [user.first_name, user.last_name].filter((value) => value?.trim()).join(" ").trim();
  return joined || user.email?.split("@")[0] || "Signed-in account";
}

function errorMessage(error?: Error | string | null): string | null {
  if (!error) return null;
  return typeof error === "string" ? error : error.message;
}

export function AppAccess({
  app,
  appearance,
  status,
  user,
  isSubmitting = false,
  error,
  onRequestAccess,
  onRefresh,
  onSwitchAccount,
  fullScreen = true,
}: AppAccessProps) {
  const name = appName(app);
  const copy = status === "pending"
    ? `Your request to open ${name} is waiting for approval. We’ll let you through as soon as access is granted.`
    : status === "error"
      ? `We couldn’t confirm your access to ${name}. Your account is still signed in.`
      : `This account is signed in, but it doesn’t have access to ${name} yet.`;
  const eyebrow = status === "pending" ? "Request pending" : status === "error" ? "Access check failed" : "Access required";
  const actionLabel = status === "pending" ? "Check access again" : status === "error" ? "Try again" : isSubmitting ? "Sending request…" : "Request access";
  const action = status === "missing" ? onRequestAccess : onRefresh;
  const displayName = userName(user);
  const displayError = errorMessage(error);

  return (
    <AppCanvas appearance={appearance} fullScreen={fullScreen}>
      <div className="lemma-app-panel">
        <section className="lemma-app-card" aria-labelledby="lemma-app-access-title">
          <div className="lemma-app-eyebrow"><span className="lemma-app-eyebrow-dot" />{eyebrow}</div>
          <div className="lemma-app-identity"><AppIcon app={app} /></div>
          <h1 className="lemma-app-title" id="lemma-app-access-title">
            {status === "pending" ? "Your request is in" : status === "error" ? "Let’s try that again" : `Request access to ${name}`}
          </h1>
          <p className="lemma-app-copy">{copy}</p>
          {user ? (
            <div className="lemma-app-user">
              <div className="lemma-app-avatar" aria-hidden="true">{initials(displayName)}</div>
              <div className="lemma-app-user-copy">
                <div className="lemma-app-user-name">{displayName}</div>
                {user.email ? <div className="lemma-app-user-email">{user.email}</div> : null}
              </div>
            </div>
          ) : null}
          {displayError ? <div className="lemma-app-alert" role="alert">{displayError}</div> : null}
          <div className="lemma-app-actions">
            {action ? (
              <button className="lemma-app-button" type="button" onClick={() => void action()} disabled={isSubmitting}>
                {actionLabel}
                {!isSubmitting ? <ArrowIcon /> : null}
              </button>
            ) : null}
            {onSwitchAccount ? (
              <button className="lemma-app-secondary" type="button" onClick={() => void onSwitchAccount()}>
                Use another account
              </button>
            ) : null}
          </div>
        </section>
      </div>
    </AppCanvas>
  );
}
