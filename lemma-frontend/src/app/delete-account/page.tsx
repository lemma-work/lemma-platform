import { ActionHost } from "@/site/action-host";
import { SitePage } from "@/site/chrome";
import { pageMetadata } from "@/site/metadata";
import { Prose } from "@/site/prose";
import { DELETION_CONSEQUENCES } from "@/session/account-deletion";
import { ORG } from "@/copy";

const TITLE = "Delete your account";
const DESCRIPTION =
    "Delete your Lemma account from any browser, without the app: sign in, read what happens, and confirm.";

export const metadata = pageMetadata(TITLE, DESCRIPTION, "/delete-account");

/** Where somebody deletes their account without the app.
 *
 *  App stores ask for a web address that does this, and the people who need it
 *  most are the ones who no longer have the app installed. What deletion does
 *  is on the page before signing in, so it can be read by somebody deciding
 *  whether to; the same form as Account settings appears once they are signed
 *  in, and the page is also where a deletion lands, saying it is done. */
const explanation = [
    "Sign in with the account you want to delete, then type its email address to confirm. " +
        "In the app, the same thing is under Settings → Account → Delete account.",
    "## What happens",
    DELETION_CONSEQUENCES.map((line) => "- " + line).join("\n"),
    "## When it is refused",
    [
        `- If other people work in your ${ORG} and you are its only owner, make someone else an owner first, ` +
            "so it is not left with nobody who can run it.",
        "- If you pay for a plan, cancel it first.",
        "- Nothing is deleted when a deletion is refused.",
    ].join("\n"),
    "Questions about your data? [Contact us](/contact), or read the [privacy policy](/privacy).",
].join("\n\n");

export default function Page() {
    return (
        <SitePage title={TITLE} description={DESCRIPTION}>
            <Prose>{explanation}</Prose>
            <section className="site-card delete-account-page">
                <ActionHost action="delete-account" />
            </section>
        </SitePage>
    );
}
