import { getDocument, GlobalWorkerOptions, type PDFDocumentProxy } from "pdfjs-dist/legacy/build/pdf.mjs";
import "./reader.css";

/** A PDF, drawn page by page with pdf.js: Android's WebView cannot show a
 *  PDF at all, and iOS would show it in a frame of its own choosing.
 *
 *  The app hands the file over as base64, in pieces (`chunk`), rather than a
 *  URL: a signed link is served from storage with headers meant for a
 *  download, and a page here may not be allowed to read it. Pages are drawn
 *  as they come near the screen and let go of once they are far from it, so
 *  a long document does not hold every page in memory at once. */

declare const PDF_WORKER_SOURCE: string;

type FromApp =
    | { type: "chunk"; data: string; last: boolean }
    | { type: "theme"; paper: string; ink3: string; wash: string };

type ToApp =
    | { type: "ready" }
    | { type: "loaded"; pages: number }
    | { type: "page"; at: number }
    | { type: "problem"; message: string };

declare global {
    interface Window {
        ReactNativeWebView?: { postMessage: (data: string) => void };
        __fromApp?: (message: FromApp) => void;
    }
}

const say = (message: ToApp) => window.ReactNativeWebView?.postMessage(JSON.stringify(message));

GlobalWorkerOptions.workerSrc = URL.createObjectURL(new Blob([PDF_WORKER_SOURCE], { type: "text/javascript" }));

const host = document.getElementById("pages")!;
const parts: string[] = [];

function bytesOf(base64: string): Uint8Array {
    const binary = atob(base64);
    const out = new Uint8Array(binary.length);
    for (let at = 0; at < binary.length; at++) out[at] = binary.charCodeAt(at);
    return out;
}

/** Sharp at the phone's density, and no sharper: a page is drawn again at
 *  most this wide, whatever the zoom. */
const MAX_WIDTH = 2200;

async function open(data: Uint8Array) {
    let pdf: PDFDocumentProxy;
    try {
        pdf = await getDocument({ data, isEvalSupported: false }).promise;
    } catch (failure) {
        say({ type: "problem", message: failure instanceof Error && /password/i.test(failure.message) ? "This PDF is locked with a password." : "This PDF could not be read." });
        return;
    }
    say({ type: "loaded", pages: pdf.numPages });
    const width = host.clientWidth;
    const scale = Math.min(window.devicePixelRatio || 2, 3);
    const sheets: { box: HTMLDivElement; drawn: boolean; drawing: boolean; at: number }[] = [];

    for (let at = 1; at <= pdf.numPages; at++) {
        const page = await pdf.getPage(at);
        const natural = page.getViewport({ scale: 1 });
        const box = document.createElement("div");
        box.className = "sheet";
        box.style.aspectRatio = `${natural.width} / ${natural.height}`;
        box.dataset.at = String(at);
        host.appendChild(box);
        sheets.push({ box, drawn: false, drawing: false, at });
        page.cleanup();
    }

    async function draw(sheet: (typeof sheets)[number]) {
        if (sheet.drawn || sheet.drawing) return;
        sheet.drawing = true;
        try {
            const page = await pdf.getPage(sheet.at);
            const natural = page.getViewport({ scale: 1 });
            const pixels = Math.min(MAX_WIDTH, width * scale);
            const viewport = page.getViewport({ scale: pixels / natural.width });
            const canvas = document.createElement("canvas");
            canvas.width = Math.floor(viewport.width);
            canvas.height = Math.floor(viewport.height);
            await page.render({ canvas, viewport }).promise;
            sheet.box.replaceChildren(canvas);
            sheet.drawn = true;
        } catch {
            sheet.box.textContent = "This page could not be drawn.";
        } finally {
            sheet.drawing = false;
        }
    }

    function forget(sheet: (typeof sheets)[number]) {
        if (!sheet.drawn) return;
        sheet.box.replaceChildren();
        sheet.drawn = false;
    }

    const near = new IntersectionObserver((entries) => {
        for (const entry of entries) {
            const sheet = sheets[Number((entry.target as HTMLElement).dataset.at) - 1];
            if (entry.isIntersecting) void draw(sheet);
            else forget(sheet);
        }
    }, { rootMargin: "150% 0px" });
    const seen = new IntersectionObserver((entries) => {
        for (const entry of entries) {
            if (entry.isIntersecting) say({ type: "page", at: Number((entry.target as HTMLElement).dataset.at) });
        }
    }, { rootMargin: "-45% 0px -45% 0px" });
    for (const sheet of sheets) {
        near.observe(sheet.box);
        seen.observe(sheet.box);
    }
}

window.__fromApp = (message: FromApp) => {
    if (message.type === "theme") {
        document.documentElement.style.setProperty("--paper", message.paper);
        document.documentElement.style.setProperty("--ink3", message.ink3);
        document.documentElement.style.setProperty("--wash", message.wash);
        return;
    }
    parts.push(message.data);
    if (!message.last) return;
    const whole = parts.join("");
    parts.length = 0;
    void open(bytesOf(whole));
};

say({ type: "ready" });
