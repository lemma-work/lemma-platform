"use client";

import { useState } from "react";
import { connect, disconnect, hasToken, upstreamUrl, sameSiteWithApi } from "./client";

/** The way in that does not involve a browser.
 *
 *  Not a failure screen, and that is the point. Showing this whenever the
 *  organization list comes back empty or errored tells a person whose network
 *  blipped, and a person on their first day who belongs to no organization
 *  yet, to go and find a bearer token. Those are three different things and
 *  they have three different screens.
 *
 *  This one is for the case it was always actually about: `localhost`, where
 *  the API's session cookie is cross-site and never sent, and agent testing,
 *  where there is no browser to sign in with. Reached deliberately, from a
 *  link, rather than arrived at by accident.
 */
export function ConnectScreen() {
    const [url, setUrl] = useState(upstreamUrl());
    const [token, setToken] = useState("");
    const [saved, setSaved] = useState(false);
    const holding = hasToken();
    const sameSite = typeof window === "undefined" ? false : sameSiteWithApi();

    return (
        <div className="screen">
            <div className="screen__inner">
                <h2>Connection settings</h2>
                <p>
                    Try signing in normally first. If your deployment cannot use a cookie session,
                    connect with a bearer token. This browser stores the token and sends it to the API
                    to authenticate requests.
                </p>
                {sameSite && !holding && (
                    <p>
                        This page is on the same site as the API. Normal sign-in should work without a token.
                    </p>
                )}
                {/* The exact place, because the obvious guess is wrong: there is
                    no `lemma auth print-token`, which is what this screen used
                    to say. `lemma auth login` puts the token in the config
                    file; this reads it back out. */}
                <p>
                    The Lemma CLI already has one after <code>lemma auth login</code>. It lives in{" "}
                    <code>~/.lemma/config.json</code> under <code>servers.&lt;server&gt;.token</code>, where{" "}
                    <code>&lt;server&gt;</code> is the name you logged in against:
                </p>
                <p>
                    {/* Takes the server name rather than hard-coding one. A
                        snippet naming somebody else's server is a snippet
                        everybody has to edit before it runs, and the name is
                        the one thing this screen cannot know. */}
                    <code className="connect__cmd">
                        python3 -c &quot;import json,pathlib,sys;print(json.loads(pathlib.Path.home().joinpath(&apos;.lemma/config.json&apos;).read_text())[&apos;servers&apos;][sys.argv[1]][&apos;token&apos;])&quot; &lt;server&gt;
                    </code>
                </p>
                <div className="field">
                    <label htmlFor="api">API URL</label>
                    <input id="api" value={url} onChange={(event) => setUrl(event.target.value)} spellCheck={false} />
                </div>
                <div className="field">
                    <label htmlFor="token">Bearer token</label>
                    <input
                        id="token"
                        type="password"
                        value={token}
                        placeholder={holding ? "a token is already saved" : "kept in this browser only"}
                        onChange={(event) => setToken(event.target.value)}
                        spellCheck={false}
                    />
                </div>
                <div className="screen__actions">
                    <button
                        className="btn btn--primary"
                        onClick={() => {
                            connect(url, token);
                            setSaved(true);
                            window.location.assign("/");
                        }}
                        disabled={!url.trim() || saved}
                    >
                        {saved ? "Opening…" : "Save and open"}
                    </button>
                    {holding && (
                        <button
                            className="screen__aside"
                            onClick={() => {
                                /* Only the token. The API URL is a setting, not
                                   a credential, and clearing it too would send
                                   somebody back to the default origin without
                                   saying it had. */
                                disconnect();
                                window.location.assign("/connect");
                            }}
                        >
                            Remove the saved token
                        </button>
                    )}
                    <a className="screen__aside" href="/">
                        Back
                    </a>
                </div>
            </div>
        </div>
    );
}
