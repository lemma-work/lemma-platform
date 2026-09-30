import assert from "node:assert/strict";
import { chromium } from "playwright";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const browser = await chromium.launch({
    headless: true,
    channel: process.env.LEMMA_TEST_BROWSER_CHANNEL || undefined,
    args: ["--host-resolver-rules=MAP *.example.test 127.0.0.1", "--no-proxy-server"],
});
try {
    const context = await browser.newContext({ ignoreHTTPSErrors: true });
    if (!config.mode?.endsWith("signed-out") && config.mode !== "login-return") await context.addCookies([{ name: "sAccessToken", value: config.token, domain: "api.example.test", path: "/", secure: true, httpOnly: true, sameSite: "Lax" }]);
    if (config.mode === "blocked-cookies") {
        await context.route("**/_lemma/app-access/redeem", async route => {
            const target = new URL(route.request().url());
            const host = target.host;
            target.hostname = "127.0.0.1";
            const response = await route.fetch({ url: target.href, headers: { ...await route.request().allHeaders(), host } });
            const headers = response.headers();
            delete headers["set-cookie"];
            await route.fulfill({ response, headers });
        });
    }
    if (config.mode === "service-down") await context.route("**/_lemma/app-access/requests", route => route.fulfill({ status: 503, contentType: "application/json", body: '{}' }));
    const page = await context.newPage();
    page.on("pageerror", error => console.error(error.message));
    page.on("console", message => { if (message.type() === "error") console.error(message.text()); });
    page.on("requestfailed", request => console.error(new URL(request.url()).pathname, request.failure()));
    page.on("response", response => { if (response.status() >= 400) console.error(response.status(), new URL(response.url()).pathname); });
    let navigations = 0;
    page.on("framenavigated", () => { navigations++; });
    const response = await page.goto(config.workspace ? config.workspace : config.origin + "/deep/path?mode=study#section");
    const view = config.workspace ? page.frameLocator("iframe") : page;
    if (config.mode === "login-return") {
        const signIn = view.getByRole("link", { name: "Sign in to Lemma" });
        await signIn.waitFor({ timeout: 15_000 });
        const link = new URL(await signIn.getAttribute("href"));
        assert.equal(link.origin, config.workspaceOrigin);
        const returnUrl = link.searchParams.get("redirect_uri");
        assert.equal(returnUrl, config.origin + "/deep/path?mode=study#section");
        await signIn.click();
        await page.getByRole("heading", { name: "Sign in", exact: true }).waitFor();
        const signedIn = await page.evaluate(async ({ apiOrigin, email }) => {
            const response = await fetch(apiOrigin + "/st/auth/signin", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", rid: "session", "st-auth-mode": "cookie" }, body: JSON.stringify({ formFields: [{ id: "email", value: email }, { id: "password", value: "TestPassword@123" }] }) });
            return response.json();
        }, config);
        assert.equal(signedIn.status, "OK");
        await page.goto(returnUrl);
    }
    if (config.mode?.endsWith("signed-out")) {
        const signIn = view.getByRole("link", { name: "Sign in to Lemma" });
        await signIn.waitFor({ timeout: 15_000 });
        const link = new URL(await signIn.getAttribute("href"));
        assert.equal(link.origin, config.workspaceOrigin);
        assert.equal(link.searchParams.get("redirect_uri"), config.workspace || config.origin + "/deep/path?mode=study#section");
        assert.equal((await view.locator("body").innerText()).includes("PRIVATE_APP_CONTENT"), false);
    } else if (["blocked-cookies", "service-down"].includes(config.mode)) {
        await view.getByText(config.mode === "blocked-cookies" ? "Your browser blocked app access. Allow cookies for this site, then try again." : "We couldn’t check your access. Try again.").waitFor({ timeout: 15_000 });
        await view.getByRole("button", { name: "Try again" }).waitFor();
        const settled = navigations;
        await page.waitForTimeout(300);
        assert.equal(navigations, settled);
        assert.equal(settled, 1);
        console.log("Private app browser access passed");
        process.exitCode = 0;
    } else {
    try {
        await view.getByText("PRIVATE_APP_CONTENT", { exact: true }).waitFor({ timeout: 15_000 });
    } catch (error) {
        console.error(await view.locator("body").innerText());
        console.error(await page.evaluate(() => ({ sdk: typeof window.LemmaClient?.startAppAccess, crypto: typeof crypto.subtle, parent: window.parent === window })));
        throw error;
    }
    if (!config.workspace) assert.equal(page.url(), config.origin + "/deep/path?mode=study#section");
    const cookies = await context.cookies();
    assert.equal(cookies.find(cookie => cookie.name === "sAccessToken").domain, "api.example.test");
    const access = cookies.find(cookie => cookie.name === "__Host-lemmaAppAccess");
    assert.equal(access?.domain, new URL(config.origin).hostname);
    assert.equal(access?.httpOnly, true);
    assert.equal(access?.secure, true);
    assert.equal(access?.sameSite, "Lax");
    assert.ok(response);
    }
    console.log("Private app browser access passed");
} finally {
    await browser.close();
}
