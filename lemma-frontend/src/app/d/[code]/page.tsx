import { configuredApiUrl, MISSING_API_URL } from "@/session/origins";
import { readAs, toRows } from "./kinds";
import { SharedProse } from "./shared-prose";
import { SharedShell } from "./shared-shell";

/** What the recipient of a public link sees.
 *
 *  A short code serves bytes, and bytes are not a document: a shared markdown
 *  report opened as a raw `/s/{code}` is a wall of asterisks in a browser tab,
 *  which is a poor answer for something somebody chose to send a colleague. So
 *  the link people copy points here, and this renders it the way the app does.
 *
 *  Fetched on the server. The code lives on the API's origin, a browser fetch
 *  would be cross-origin, and this page has to render for a reader with no
 *  session and no Lemma account at all. Nothing here is authenticated and
 *  nothing about the pod is exposed: the code is the whole capability, and all
 *  it yields is this one file.
 *
 *  **Opens are the currency.** The link is capped in opens, not in bytes, so a
 *  reader refreshing the page four times would spend four of their fifty. The
 *  fetch is cached for a minute against the code to stop that. Sharing that
 *  cache between readers is correct rather than a leak — the code *is* the
 *  permission, so everyone who can reach this page is already entitled to the
 *  same bytes. */

const CACHE_SECONDS = 60;

/** Inlined rather than fetched again. An `<img>` pointed at the proxy is a
 *  second open for bytes this page already holds; under this size a data URI
 *  costs nothing and the picture is simply there. */
const INLINE_IMAGE_LIMIT = 1_500_000;


function nameFrom(disposition: string | null, code: string): string {
    const match = disposition && /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
    return match ? decodeURIComponent(match[1]) : "Shared file " + code;
}

export default async function SharedDocument({ params }: { params: Promise<{ code: string }> }) {
    const { code } = await params;
    const safe = encodeURIComponent(code);
    const bytesHere = "/d/" + safe + "/file";
    const download = bytesHere + "?save=1";

    /* Said in the reader's terms, not the operator's. Whoever followed this
       link is a stranger to this deployment and cannot act on the name of an
       environment variable; the variable is named in the server log, where
       the person who can act on it is looking. */
    const origin = configuredApiUrl();
    if (!origin) {
        console.error(MISSING_API_URL);
        return <SharedShell title="This link could not be reached" note="This site is not finished being set up. Try again later." />;
    }

    let response: Response;
    try {
        response = await fetch(origin + "/s/" + safe, { next: { revalidate: CACHE_SECONDS } });
    } catch {
        return <SharedShell title="This link could not be reached" note="Couldn’t load the document. Try again in a moment." />;
    }

    if (response.status === 404) {
        return (
            <SharedShell
                title="This link has expired"
                note="Public links stop working after a set time, or after they have been opened a set number of times. Ask whoever sent it for a new one."
            />
        );
    }
    if (!response.ok) {
        return <SharedShell title="This document is not available" note={"The link came back " + response.status + "."} />;
    }

    const type = (response.headers.get("content-type") ?? "").split(";")[0].trim();
    const name = nameFrom(response.headers.get("content-disposition"), code);
    const kind = readAs(type, name);

    if (kind === "image") {
        const buffer = await response.arrayBuffer();
        const source =
            buffer.byteLength <= INLINE_IMAGE_LIMIT
                ? "data:" + (type || "image/png") + ";base64," + Buffer.from(buffer).toString("base64")
                : bytesHere;
        return (
            <SharedShell name={name} download={download} bleed>
                {/* A plain img on purpose: the source is often a data URI, and
                    Next's image pipeline cannot optimise bytes it never sees. */}
                <img className="shared__image" src={source} alt={name} />
            </SharedShell>
        );
    }

    if (kind === "pdf") {
        /* The browser's own PDF viewer, which is better than anything drawn
           here, and the one case that genuinely costs a second open — the
           bytes have to reach the plugin rather than this page. Said out loud
           under the frame, rather than discovered by a reader whose link died
           early and never learned why. */
        return (
            <SharedShell
                name={name}
                download={download}
                bleed
                foot={<span>Opening the PDF counts toward this link&rsquo;s view limit.</span>}
            >
                <iframe className="shared__pdf" src={bytesHere} title={name} />
            </SharedShell>
        );
    }

    if (kind === "markdown" || kind === "html" || kind === "csv" || kind === "text") {
        const text = await response.text();

        if (kind === "markdown") {
            return <SharedShell name={name} download={download}><SharedProse text={text} /></SharedShell>;
        }

        if (kind === "html") {
            /* Sandboxed with nothing granted. Inside the app an agent's HTML
               may run scripts, because a signed-in member asked for it; this
               page is opened by strangers from a link, so the markup renders
               and nothing executes. */
            return (
                <SharedShell name={name} download={download} bleed>
                    {/* A shared HTML file is a page. It gets the screen. */}
                    <iframe className="shared__frame" sandbox="" srcDoc={text} title={name} />
                </SharedShell>
            );
        }

        if (kind === "csv") {
            const rows = toRows(text, name.toLowerCase().endsWith(".tsv") ? "\t" : ",");
            if (rows.length > 1) {
                const [head, ...body] = rows;
                return (
                    <SharedShell name={name} download={download} wide>
                        <div className="shared__scroll">
                            <table className="shared__table">
                                <thead><tr>{head.map((cell, index) => <th key={index}>{cell}</th>)}</tr></thead>
                                <tbody>
                                    {body.slice(0, 500).map((line, index) => (
                                        <tr key={index}>{head.map((_, cell) => <td key={cell}>{line[cell] ?? ""}</td>)}</tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                        {body.length > 500 && (
                            <p className="shared__fine">First 500 rows of {body.length}. Download it for the rest.</p>
                        )}
                    </SharedShell>
                );
            }
        }

        return <SharedShell name={name} download={download}><pre className="shared__pre">{text}</pre></SharedShell>;
    }

    return (
        <SharedShell name={name} download={download}>
            <p className="shared__note">
                Preview unavailable for {type || "this file type"}. Download the file to open it.
            </p>
            <a className="btn btn--primary" href={download}>Download {name}</a>
        </SharedShell>
    );
}
