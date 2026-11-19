import test from "node:test";
import assert from "node:assert/strict";
import {
    folderFromQuery,
    importRequest,
    installPath,
    installTitle,
    isBundleFolder,
    sourceHref,
    sourceLabel,
} from "../src/site/import/github-source.ts";
import {
    readBundleManifest,
    resolveReadmeAssetUrl,
    resolveReadmeLinkUrl,
} from "../src/site/github/public-repository.ts";
import { pendingInstall, rememberInstall } from "../src/site/import/pending-install.ts";

const repository = { owner: "acme", repo: "awesome-plugins", path: null };
const mail = { ...repository, path: "install/mail" };

test("a repository installs as it always has: no folder is sent", () => {
    assert.deepEqual(importRequest(repository), {
        kind: "GITHUB",
        owner: "acme",
        repo: "awesome-plugins",
    });
    assert.equal(installPath(repository), "/import/github/acme/awesome-plugins");
    assert.equal(sourceHref(repository), "https://github.com/acme/awesome-plugins");
    assert.equal(sourceLabel(repository), "acme/awesome-plugins");
});

test("a folder of a repository installs that folder", () => {
    assert.deepEqual(importRequest(mail), {
        kind: "GITHUB",
        owner: "acme",
        repo: "awesome-plugins",
        path: "install/mail",
    });
    assert.equal(
        installPath(mail),
        "/import/github/acme/awesome-plugins?path=install/mail",
    );
    assert.equal(
        sourceHref(mail),
        "https://github.com/acme/awesome-plugins/tree/HEAD/install/mail",
    );
    assert.equal(sourceLabel(mail), "acme/awesome-plugins/install/mail");
});

test("a folder's Lemma is named by its bundle, else by the folder, never by the repository", () => {
    assert.equal(installTitle(mail, { manifest: "Mail Desk" }), "Mail Desk");
    assert.equal(installTitle(mail, {}), "Mail");
    assert.equal(
        installTitle(mail, { template: "Ledger", readme: "Awesome Plugins" }),
        "Mail",
    );
    assert.equal(
        installTitle({ ...repository, path: "install/smart_inbox" }, {}),
        "Smart Inbox",
    );
});

test("an install of a folder that sign-in interrupts is offered back with its folder", () => {
    const held = new Map<string, string>();
    const store = {
        getItem: (name: string) => held.get(name) ?? null,
        setItem: (name: string, value: string) => void held.set(name, value),
        removeItem: (name: string) => void held.delete(name),
    };
    rememberInstall(store, { path: installPath(mail), title: "Mail" }, 1_000);
    assert.deepEqual(pendingInstall(store, 2_000), {
        path: "/import/github/acme/awesome-plugins?path=install/mail",
        title: "Mail",
    });
});

test("a repository is named as before: template, README title, then its name", () => {
    assert.equal(
        installTitle(repository, { template: "Ledger", readme: "Readme" }),
        "Ledger",
    );
    assert.equal(installTitle(repository, { readme: "Readme" }), "Readme");
    assert.equal(installTitle(repository, {}), "Awesome Plugins");
});

test("only a folder the server would take is a page", () => {
    assert.equal(folderFromQuery(undefined), null);
    assert.equal(folderFromQuery("install/mail"), "install/mail");
    assert.equal(folderFromQuery(".lemma/mail"), ".lemma/mail");
    for (const bad of [
        "",
        "/install/mail",
        "install/mail/",
        "install//mail",
        "..",
        "install/../secrets",
        "install/./mail",
        "install/mail name",
        "install%2Fmail",
        "a".repeat(256),
    ]) {
        assert.equal(isBundleFolder(bad), false, bad);
        assert.equal(folderFromQuery(bad), undefined, bad);
    }
    assert.equal(folderFromQuery(["install/mail", "install/crm"]), undefined);
});

test("a folder's README links and images are relative to the folder", () => {
    const at = ["acme", "awesome-plugins", "HEAD", "install/mail"] as const;
    assert.equal(
        resolveReadmeAssetUrl("./cover.png", ...at),
        "https://raw.githubusercontent.com/acme/awesome-plugins/HEAD/install/mail/cover.png",
    );
    assert.equal(
        resolveReadmeLinkUrl("docs/setup.md", ...at),
        "https://github.com/acme/awesome-plugins/blob/HEAD/install/mail/docs/setup.md",
    );
    /* A leading slash is the repository's root, as GitHub reads it. */
    assert.equal(
        resolveReadmeLinkUrl("/LICENSE", ...at),
        "https://github.com/acme/awesome-plugins/blob/HEAD/LICENSE",
    );
    /* And a repository's own README resolves as it did. */
    assert.equal(
        resolveReadmeAssetUrl("./cover.png", "acme", "awesome-plugins", "HEAD"),
        "https://raw.githubusercontent.com/acme/awesome-plugins/HEAD/cover.png",
    );
    assert.equal(
        resolveReadmeLinkUrl("/docs/a.md", "acme", "awesome-plugins", "HEAD"),
        "https://github.com/acme/awesome-plugins/blob/HEAD/docs/a.md",
    );
});

test("a bundle's manifest gives its name and line, and nothing it cannot", () => {
    assert.deepEqual(
        readBundleManifest({ name: " Mail ", description: "Answers mail." }),
        { name: "Mail", description: "Answers mail." },
    );
    assert.deepEqual(readBundleManifest({ name: 7, description: "" }), {
        name: null,
        description: null,
    });
    assert.deepEqual(readBundleManifest({ name: "x".repeat(81) }), {
        name: null,
        description: null,
    });
    assert.equal(readBundleManifest(null), null);
    assert.equal(readBundleManifest("Mail"), null);
});
