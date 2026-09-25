import { useState } from "react";
import { source } from "@/data";
import type { SharedLink } from "@/data";
import { CheckIcon, CopyIcon, ExternalIcon } from "@/ui/icons";
import { Modal } from "@/shell/modal";
import { copyText } from "@/desktop/clipboard";

/** Sharing a document.
 *
 *  The dialog asks who, not how. There are two link types underneath and they
 *  are not interchangeable — one is an authenticated deep link into the
 *  workspace, the other is a public capability code — but nobody opens a share
 *  dialog wanting to choose a mechanism. They want to send a document to a
 *  person, and the only question that matters is whether that person is inside
 *  the pod.
 *
 *  Inside, it is the permanent app link: they already have the right to read
 *  it, and a link that never expires is correct. Outside, it is a minted code —
 *  and then the bounds are the headline rather than the small print, because
 *  the bounds are the reason this is safe to offer at all. Three hours and
 *  fifty opens by default; the platform clamps both, so what the dialog shows
 *  is what came back, never what was asked for. */

const LIVES: { label: string; seconds: number }[] = [
    { label: "1 hour", seconds: 3600 },
    { label: "3 hours", seconds: 10800 },
    { label: "24 hours", seconds: 86400 },
];

const OPENS: { label: string; hits: number }[] = [
    { label: "10 opens", hits: 10 },
    { label: "50 opens", hits: 50 },
    { label: "100 opens", hits: 100 },
];

function when(iso: string): string {
    if (!iso) return "";
    const at = new Date(iso);
    if (Number.isNaN(at.getTime())) return "";
    const hours = Math.max(0, Math.round((at.getTime() - Date.now()) / 3_600_000));
    if (hours < 1) return "under an hour";
    return hours === 1 ? "an hour" : hours + " hours";
}

function Copyable({ url, label }: { url: string; label: string }) {
    const [copied, setCopied] = useState(false);
    return (
        <div className="sharelink">
            <input readOnly value={url} aria-label={label} onFocus={(event) => event.target.select()} />
            <button
                className="btn"
                onClick={() => {
                    copyText(url)
                        .then(() => {
                            setCopied(true);
                            window.setTimeout(() => setCopied(false), 1400);
                        })
                        .catch(() => undefined);
                }}
            >
                {copied ? <CheckIcon size={15} /> : <CopyIcon size={15} />}
                {copied ? "Copied" : "Copy"}
            </button>
        </div>
    );
}

export function ShareDialog({
    podId,
    path,
    name,
    appUrl,
    onClose,
}: {
    podId: string;
    path: string;
    name: string;
    appUrl?: string;
    onClose: () => void;
}) {
    const [audience, setAudience] = useState<"pod" | "anyone">("pod");
    const [life, setLife] = useState(10800);
    const [opens, setOpens] = useState(50);
    const [link, setLink] = useState<SharedLink | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    async function mint() {
        setBusy(true);
        setError(null);
        try {
            setLink(await source.shareFile(podId, path, { expiresSeconds: life, maxHits: opens }));
        } catch (problem) {
            setError(problem instanceof Error ? problem.message : "That link could not be made.");
        } finally {
            setBusy(false);
        }
    }

    return (
        <Modal narrow title={"Share " + name} subtitle="Who is this going to?" onClose={onClose}>
            <div className="share">
                <div className="share__who">
                    <button
                        className="share__opt"
                        aria-pressed={audience === "pod"}
                        onClick={() => setAudience("pod")}
                    >
                        <b>People with access to this teammate</b>
                        <span>Recipients must sign in and have access to this file.</span>
                    </button>
                    <button
                        className="share__opt"
                        aria-pressed={audience === "anyone"}
                        onClick={() => setAudience("anyone")}
                    >
                        <b>Anyone with the link</b>
                        <span>Anyone who receives this link can open it without signing in, until it expires or reaches its view limit.</span>
                    </button>
                </div>

                {audience === "pod" ? (
                    appUrl ? (
                        <Copyable url={appUrl} label="Internal link" />
                    ) : (
                        <p className="empty-row">This document has no workspace link yet.</p>
                    )
                ) : (
                    <>
                        {!link && (
                            <>
                                <div className="share__bounds">
                                    <label>
                                        <span>Lasts</span>
                                        <select value={life} onChange={(event) => setLife(Number(event.target.value))}>
                                            {LIVES.map((item) => (
                                                <option key={item.seconds} value={item.seconds}>{item.label}</option>
                                            ))}
                                        </select>
                                    </label>
                                    <label>
                                        <span>Stops after</span>
                                        <select value={opens} onChange={(event) => setOpens(Number(event.target.value))}>
                                            {OPENS.map((item) => (
                                                <option key={item.hits} value={item.hits}>{item.label}</option>
                                            ))}
                                        </select>
                                    </label>
                                </div>
                                <button className="btn btn--primary" onClick={() => void mint()} disabled={busy}>
                                    {busy ? "Making a link…" : "Make a link"}
                                </button>
                            </>
                        )}

                        {link && (
                            <>
                                <Copyable url={link.readUrl} label="Public link" />
                                {/* What came back, not what was asked for — both
                                    bounds are clamped server-side. */}
                                <p className="share__bounded">
                                    Works for <b>{when(link.expiresAt)}</b> and <b>{link.maxHits} opens</b>.
                                    Every view counts, including yours.
                                </p>
                                <a className="linkish" href={link.readUrl} target="_blank" rel="noreferrer">
                                    See what they will see <ExternalIcon size={13} />
                                </a>
                            </>
                        )}
                    </>
                )}

                {error && <p className="hire__error">{error}</p>}
            </div>
        </Modal>
    );
}
