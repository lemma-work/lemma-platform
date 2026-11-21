import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { blank, fields, labelWords, payload } from "../src/connect/schema.ts";

registerHooks({ load(url, context, nextLoad) {
    if (url.endsWith(".css")) return { format: "module", shortCircuit: true, source: "" };
    if (!url.endsWith(".tsx")) return nextLoad(url, context);
    return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
        compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
    }).outputText };
} });

const { SchemaFields } = await import("../src/connect/fields.tsx");

/** WhatsApp's credential schema as the catalogue serves it: Title Case
 *  titles, no descriptions, two plumbing fields among four that matter. */
const whatsapp = {
    type: "object",
    required: ["access_token", "phone_number_id"],
    properties: {
        waba_id: { type: "string", title: "WhatsApp Business Account ID" },
        app_secret: { type: "string", title: "App Secret", format: "password" },
        access_token: { type: "string", title: "Access Token", format: "password" },
        api_base_url: { type: "string", title: "API Base URL" },
        verify_token: { type: "string", title: "Webhook Verify Token", format: "password" },
        phone_number_id: { type: "string", title: "Phone Number ID", description: "From the app's WhatsApp page." },
    },
};

function draw(schema: unknown, problems: Record<string, string> = {}) {
    const list = fields(schema);
    return renderToStaticMarkup(createElement(SchemaFields, { list, values: blank(list), problems, onChange: () => undefined }));
}

test("a schema's titles become plain words, never Title Case or a key", () => {
    assert.equal(labelWords("Phone Number ID"), "Phone number ID");
    assert.equal(labelWords("Access Token"), "Access token");
    assert.equal(labelWords("API Base URL"), "API address");
    assert.equal(labelWords("Webhook Verify Token"), "Webhook check token");
    assert.equal(labelWords("WhatsApp Business Account ID"), "WhatsApp Business Account ID");
    assert.equal(labelWords("client_id"), "Client ID");
    assert.equal(labelWords("Client Secret"), "Client secret");
    assert.equal(labelWords("Spec URL"), "Spec address");
});

test("required is the default: optional fields say so, and nothing carries a star", () => {
    const html = draw(whatsapp);
    assert.doesNotMatch(html, /\*<\/i>|> \*</);
    assert.match(html, /<label for="f-phone_number_id">Phone number ID<\/label>/);
    assert.match(html, /<label for="f-access_token">Access token<\/label>/);
    assert.match(html, /WhatsApp Business Account ID<span class="kfield__opt"> \(optional\)<\/span>/);
    assert.match(html, /App secret<span class="kfield__opt"> \(optional\)<\/span>/);
});

test("help comes from the schema's description, and only from it", () => {
    const html = draw(whatsapp);
    assert.match(html, /<small id="f-phone_number_id-help">From the app&#x27;s WhatsApp page\.<\/small>/);
    assert.equal((html.match(/<small/g) ?? []).length, 1, "no help is invented for fields that have none");
});

test("secrets are hidden, with Show beside them, and never offered to a password manager", () => {
    const html = draw(whatsapp);
    const token = html.match(/<input[^>]*id="f-access_token"[^>]*>/)?.[0] ?? "";
    assert.match(token, /type="password"/);
    assert.match(token, /autoComplete="new-password"|autocomplete="new-password"/i);
    assert.match(token, /data-1p-ignore="true"/);
    assert.match(html, /aria-label="Show access token"[^>]*>Show<\/button>/);
    const phone = html.match(/<input[^>]*id="f-phone_number_id"[^>]*>/)?.[0] ?? "";
    assert.match(phone, /type="text"/);
});

test("the plumbing waits under More settings, closed", () => {
    const html = draw(whatsapp);
    const more = html.slice(html.indexOf("<details"));
    assert.match(html, /<details class="kmore"><summary>More settings<\/summary>/, "closed: no open attribute");
    assert.match(more, /API address/);
    assert.match(more, /Webhook check token/);
    const before = html.slice(0, html.indexOf("<details"));
    for (const shown of ["Phone number ID", "Access token", "WhatsApp Business Account ID", "App secret"]) {
        assert.ok(before.includes(shown), shown + " is in the form itself");
    }
});

test("a field's problem is said under it", () => {
    const html = draw(whatsapp, { access_token: "Access token is needed." });
    assert.match(html, /id="f-access_token"[^>]*aria-invalid="true"/);
    assert.match(html, /<p class="kproblem" role="alert">.*Access token is needed\./);
});

test("drawing it changes nothing that is sent", () => {
    const list = fields(whatsapp);
    const sent = payload(list, { ...blank(list), access_token: " t ", phone_number_id: "123", api_base_url: "" });
    assert.deepEqual(sent, { access_token: "t", phone_number_id: "123" });
});
