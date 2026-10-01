"""The page a private app's address shows until its visitor is let in.

Trusted, and the same bytes for every slug: it carries nothing from the app it
guards -- not its name, not whether it exists -- so it can be the answer for a
private app and an absent one alike. The address the person typed is filled in
by the browser, which already knows it.

It borrows the workspace's own sign-in ground (`lemma-frontend/src/styles/
tokens.css`): warm paper on canvas in both appearances, the three-bar mark,
weight never above 500, and the press aubergine for the one primary action,
because this is a screen that belongs to Lemma rather than to any teammate.
The values are copied rather than linked: the page is served from the app's
origin, before any session, and must render with nothing else reachable.
"""

import json
from html import escape

from app.core.config import settings
from app.modules.identity.contracts.app_sessions import app_sign_in_url

_MARK = (
    '<svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true">'
    '<rect x="4" y="19" width="5" height="10" rx="1.5"/>'
    '<rect x="13" y="12" width="5" height="17" rx="1.5"/>'
    '<rect x="22" y="3" width="5" height="26" rx="1.5"/></svg>'
)

_STYLE = """
:root{color-scheme:light dark;
--canvas:#f4f3ed;--paper:#fffefa;--chrome:#ecebe1;--ink:#20211f;--ink-2:#55564e;--ink-3:#6a6b62;
--line:rgb(32 33 31/.05);--line-2:rgb(32 33 31/.10);--brand:#5a3fd4;--accent:#6b4fe0;
--field:#6b2b7a;--field-ink:#fffefa;--bad:#b3261e;
--depth-rest:0 1px 2px rgb(25 23 18/.03),0 4px 14px rgb(25 23 18/.05),0 10px 28px rgb(25 23 18/.04);
--depth-raise:0 1px 2px rgb(25 23 18/.07),0 6px 14px rgb(25 23 18/.09),0 18px 34px rgb(25 23 18/.07);
--edge-lit:inset 0 1px 0 rgb(255 255 255/.30);--edge-dark:inset 0 -1px 0 rgb(25 23 18/.04);
--ui:"Schibsted Grotesk",system-ui,-apple-system,"Segoe UI",sans-serif;
--mono:"DM Mono",ui-monospace,SFMono-Regular,Menlo,monospace}
@media (prefers-color-scheme:dark){:root{
--canvas:#141310;--paper:#1e1c17;--chrome:#272520;--ink:#f6f4ec;--ink-2:#aaa79a;--ink-3:#8b8878;
--line:rgb(246 244 236/.07);--line-2:rgb(246 244 236/.12);--brand:#8b7af5;--accent:#a08cff;--bad:#f08a7e;
--depth-rest:0 1px 2px rgb(0 0 0/.22),0 4px 14px rgb(0 0 0/.26),0 10px 28px rgb(0 0 0/.22);
--depth-raise:0 1px 2px rgb(0 0 0/.35),0 6px 14px rgb(0 0 0/.38),0 18px 34px rgb(0 0 0/.30);
--edge-lit:inset 0 1px 0 rgb(255 255 255/.04);--edge-dark:inset 0 -1px 0 rgb(0 0 0/.18)}}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;display:flex;flex-direction:column;background:var(--canvas);color:var(--ink);
font:16px/1.6 var(--ui);-webkit-font-smoothing:antialiased}
.bar{flex:none;display:flex;align-items:center;gap:9px;padding:26px 32px;color:var(--ink);
font-size:15px;font-weight:500;letter-spacing:-.01em}
.bar svg{fill:var(--brand)}
main{flex:1;display:flex;align-items:center;justify-content:center;padding:16px 16px 12vh}
.card{width:min(100%,440px);background:var(--paper);border:1px solid var(--line-2);border-radius:16px;
box-shadow:var(--depth-rest),var(--edge-lit);padding:32px}
.lock{display:grid;place-items:center;width:40px;height:40px;border-radius:11px;margin-bottom:20px;
background:var(--field);color:var(--field-ink)}
.lock svg{width:20px;height:20px;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
h1{margin:0 0 8px;font-size:26px;font-weight:500;letter-spacing:-.025em;line-height:1.2}
.lede{margin:0;color:var(--ink-2);font-size:15px;line-height:1.6}
.host{display:block;width:fit-content;max-width:100%;margin:18px 0 0;padding:3px 8px;border-radius:8px;
background:var(--chrome);color:var(--ink-2);font:12.5px/1.5 var(--mono);
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.host:empty{display:none}
.actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:26px}
.actions:not(:has(> :not([hidden]))){display:none}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:44px;
padding:0 18px;border-radius:10px;border:1px solid var(--line-2);background:var(--paper);color:var(--ink);
font:500 14px/1 var(--ui);text-decoration:none;cursor:pointer;
box-shadow:var(--depth-rest),var(--edge-lit);transition:box-shadow .14s ease,border-color .14s ease,transform .14s ease}
.btn:hover{border-color:var(--ink-3);box-shadow:var(--depth-raise),var(--edge-lit)}
.btn:active{transform:translateY(1px)}
.btn:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.btn--primary{background:var(--field);border-color:var(--field);color:var(--field-ink);
box-shadow:var(--depth-rest),var(--edge-dark)}
.btn--primary:hover{filter:brightness(1.08);border-color:var(--field);box-shadow:var(--depth-raise),var(--edge-dark)}
.btn svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.btn[hidden]{display:none}
.wait{display:none;width:16px;height:16px;margin:22px 0 0;border-radius:50%;
border:2px solid var(--line-2);border-top-color:var(--ink-3);animation:spin .8s linear infinite}
body[data-state=checking] .wait{display:block}
body[data-state=unavailable] .lock,body[data-state=blocked] .lock{background:var(--chrome);color:var(--bad)}
.foot{margin:22px 0 0;padding-top:18px;border-top:1px solid var(--line);color:var(--ink-3);font-size:12.5px;line-height:1.5}
@keyframes spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.wait{animation:none;border-top-color:var(--ink-3)}.btn{transition:none}}
@media (max-width:520px){.bar{padding:20px 16px}.card{padding:24px 20px}h1{font-size:23px}
.actions .btn{flex:1}main{padding-bottom:6vh}}
"""

_LOCK = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<rect x="5" y="10.5" width="14" height="9.5" rx="2.2"/>'
    '<path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/></svg>'
)
_ARROW = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13M13 6l6 6-6 6"/></svg>'


def render_app_access_page() -> str:
    config = json.dumps(
        {
            "apiUrl": settings.api_url,
            "authUrl": app_sign_in_url(),
            "homeUrl": settings.frontend_url,
        }
    ).replace("<", "\\u003c")
    sdk_url = escape(
        settings.api_url.rstrip("/") + "/public/sdk/lemma-client.js", quote=True
    )
    home_url = escape(settings.frontend_url or "/", quote=True)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>Open app · Lemma</title>
<style>{_STYLE}</style></head>
<body data-state="checking">
<header class="bar">{_MARK}<span>Lemma</span></header>
<main>
<section class="card" aria-labelledby="app-access-title">
<div class="lock">{_LOCK}</div>
<h1 id="app-access-title">Opening this app</h1>
<p class="lede" id="app-access-status" role="status">Checking that it's shared with you.</p>
<p class="host" id="app-access-host"></p>
<div class="wait" aria-hidden="true"></div>
<div class="actions">
<a class="btn btn--primary" id="app-access-sign-in" hidden>Sign in to Lemma {_ARROW}</a>
<button class="btn" type="button" id="app-access-retry" hidden>Try again</button>
<a class="btn" id="app-access-home" href="{home_url}" target="_top" hidden>Go to Lemma</a>
</div>
<p class="foot">Private apps open only for the people they’re shared with.</p>
<noscript><p class="lede">Turn on JavaScript to sign in and open this app.</p></noscript>
</section>
</main>
<script src="{sdk_url}"></script><script>
window.LemmaClient.startAppAccess({config});
</script></body></html>"""
