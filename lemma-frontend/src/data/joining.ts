/** Who may let themselves in to a teammate.
 *
 *  The platform stores one of three rules on a pod. They are a real ladder —
 *  each rung lets in everybody the one below it did, and more — so the words
 *  here are ordered the way the picker draws them and the way somebody thinks
 *  about the question: the door is shut, the door is open to the building, the
 *  door is open.
 *
 *  Pure, and the wire's spelling lives nowhere else. `INVITE_ONLY` read into
 *  the UI would end up in a sentence eventually — it is the kind of string
 *  that escapes through an aria-label — and "who can join: ORG_MEMBERS" is
 *  not a thing anybody says.
 */
export type JoinPolicy = "invited" | "org" | "anyone";

/** What the platform calls them. */
export type JoinWire = "INVITE_ONLY" | "ORG_MEMBERS" | "PUBLIC";

const WIRE: Record<JoinPolicy, JoinWire> = {
    invited: "INVITE_ONLY",
    org: "ORG_MEMBERS",
    anyone: "PUBLIC",
};

/** What the platform is told. */
export function joinWire(policy: JoinPolicy): JoinWire {
    return WIRE[policy];
}

/** What the platform said, or `"invited"`.
 *
 *  An unset policy is the closed one, which is also what the backend does with
 *  a pod nobody has configured — so a missing field and a shut door read the
 *  same here rather than the control having a fourth, empty state to draw. A
 *  rule this build has not been taught reads as closed too: guessing open
 *  about an access setting is the one direction that cannot be walked back.
 */
export function readJoin(raw: unknown): JoinPolicy {
    const said = String(raw ?? "").toUpperCase();
    const found = (Object.keys(WIRE) as JoinPolicy[]).find((policy) => WIRE[policy] === said);
    return found ?? "invited";
}

/** The three rungs, in order, said the way a person would say them.
 *
 *  `who` is the answer to "who can join", which is what the control shows when
 *  it is closed; `then` is what actually happens, which is the part that
 *  decides between them — the difference between the middle rung and the top
 *  is not who is allowed but whether anybody is asked first.
 *
 *  Shut does not mean silent, and the copy has to say so: somebody without
 *  access gets a Request button rather than a wall, and the ask reaches
 *  whoever runs the pod both in the app and by email. Read as "nobody else
 *  can get in", the closed rung looks like the setting you pick to make
 *  yourself unreachable, which is not what it does.
 *
 *  The organization's name goes in rather than "your organization": somebody
 *  in two of them is reading this to find out which one it means.
 */
export function joinChoices(orgName: string): { policy: JoinPolicy; who: string; then: string }[] {
    const org = orgName.trim() || "this organization";
    return [
        {
            policy: "invited",
            who: "Only people who are added",
            then: "Anyone else gets a Request button; the ask arrives here and by email.",
        },
        {
            policy: "org",
            who: "Anyone at " + org,
            then: "They add themselves, without asking. Nobody outside " + org + " can.",
        },
        {
            policy: "anyone",
            who: "Anyone with a Lemma account",
            then: "They add themselves, without asking, from outside " + org + " as well.",
        },
    ];
}

/** One rung, for the places that show the current answer without the list. */
export function joinSaid(policy: JoinPolicy, orgName: string): { who: string; then: string } {
    const rungs = joinChoices(orgName);
    const { who, then } = rungs.find((choice) => choice.policy === policy) ?? rungs[0];
    return { who, then };
}

/** A refusal from the platform, with the rules called what this app calls
 *  them.
 *
 *  Opening a pod wider than its organization is refused like this:
 *
 *      This pod cannot be opened to everyone while its organization is not
 *      public. Ask an organization owner to open the organization first, or
 *      use ORG_MEMBERS to open the pod to the organization.
 *
 *  Every word of that is worth showing except the last one anybody would act
 *  on: `ORG_MEMBERS` appears nowhere on this screen, so the sentence ends by
 *  naming a setting the reader cannot find. Putting the three wire names back
 *  into the picker's own words is the difference between a refusal that can be
 *  acted on and one that has to be taken to somebody who knows the API.
 */
export function sayJoinWords(text: string, orgName: string): string {
    const rungs = joinChoices(orgName);
    return rungs.reduce(
        (said, rung) => said.replaceAll(WIRE[rung.policy], "\u201c" + rung.who + "\u201d"),
        text,
    );
}

/* ── The organization's own door ──────────────────────────────────────────
 *
 *  The same question one level up, and not a copy of it: a pod's middle rung
 *  is everybody in the organization, and an organization's middle rung is
 *  everybody at an email domain — a rule that needs a second piece of
 *  information to mean anything, which is what makes this a different control
 *  rather than the same one pointed at something else.
 *
 *  The two doors are connected, and the platform enforces it: a pod cannot be
 *  opened wider than the organization around it. That is worth saying on this
 *  screen, because the refusal it causes is raised over on a teammate's page,
 *  a long way from the setting that would fix it.
 */

/** Who may let themselves into an organization. */
export type OrgJoinPolicy = "invited" | "domain" | "anyone";

/** The rule, and the domain the middle one is about. The domain travels with
 *  the policy because it is meaningless without it and is kept when the policy
 *  moves away from it — somebody switching to invitations for a week should
 *  not have to remember their own domain afterwards. */
export interface OrgJoin {
    policy: OrgJoinPolicy;
    domain: string;
}

const ORG_WIRE: Record<OrgJoinPolicy, string> = {
    invited: "INVITE_ONLY",
    domain: "EMAIL_DOMAIN",
    anyone: "PUBLIC",
};

export function orgJoinWire(policy: OrgJoinPolicy): string {
    return ORG_WIRE[policy];
}

/** What the platform said, defaulting closed for the reasons `readJoin` does.
 *
 *  A domain arrives with or without its `@` depending on who typed it, so it
 *  is stored here the way it is shown: bare. */
export function readOrgJoin(rawPolicy: unknown, rawDomain: unknown): OrgJoin {
    const said = String(rawPolicy ?? "").toUpperCase();
    const found = (Object.keys(ORG_WIRE) as OrgJoinPolicy[]).find((policy) => ORG_WIRE[policy] === said);
    const domain = String(rawDomain ?? "").trim().replace(/^@+/, "").toLowerCase();
    /* A domain rule with no domain admits nobody and reads as though it admits
       a company. Shown as what it actually is until somebody says which. */
    if (found === "domain" && !domain) return { policy: "invited", domain: "" };
    return { policy: found ?? "invited", domain };
}

/** A typed domain, or why it cannot be one.
 *
 *  Loose, for the reason `inviteProblem` is: the server owns what a real
 *  domain is, and a regex that thinks it knows better refuses somebody's
 *  legitimate one. This catches only what cannot work. */
export function domainProblem(raw: string): string | null {
    const domain = raw.trim().replace(/^@+/, "");
    if (!domain) return "A domain is needed — the part after the @.";
    if (/\s/.test(domain)) return "A domain cannot contain spaces.";
    if (domain.includes("@")) return "Just the part after the @.";
    if (!domain.includes(".")) return "That is missing the part after the dot.";
    return null;
}

/** The three rungs, in order, said the way a person would say them. */
export function orgJoinChoices(domain: string): { policy: OrgJoinPolicy; who: string; then: string }[] {
    const at = domain.trim() ? "@" + domain.trim() : "";
    return [
        {
            policy: "invited",
            who: "Only people who are invited",
            then: "Everybody arrives by an invitation somebody here sent.",
        },
        {
            policy: "domain",
            who: at ? "Anyone with an " + at + " address" : "Anyone at our own domain",
            then: at
                ? "They join themselves, with no invitation, once they have proved the address."
                : "Needs the domain your addresses end in before it can let anybody in.",
        },
        {
            policy: "anyone",
            who: "Anyone with a Lemma account",
            then: "They join themselves, with no invitation, from anywhere.",
        },
    ];
}

/** One rung, for showing the current answer without the list. */
export function orgJoinSaid(join: OrgJoin): { who: string; then: string } {
    const rungs = orgJoinChoices(join.domain);
    const { who, then } = rungs.find((choice) => choice.policy === join.policy) ?? rungs[0];
    return { who, then };
}

/* ── Asking to be let in ──────────────────────────────────────────────────
 *
 *  The other side of the door. `joinChoices` already tells whoever runs a pod
 *  that "anyone else gets a Request button; the ask arrives here and by
 *  email" — this is what makes both halves of that sentence true.
 *
 *  The platform decides what an ask becomes, not this app: where the door is
 *  already open to the asker the membership is made on the spot and the answer
 *  comes back `approved`, and where it is shut the answer is `pending` and
 *  whoever runs the pod is emailed. So the button says "Ask", never "Join" —
 *  it cannot know which it will be until it has asked.
 */

/** What became of an ask. */
export type JoinStanding = "pending" | "approved" | "refused";

/** Somebody's ask to be let in to a teammate. */
export interface JoinRequest {
    id: string;
    podId: string;
    standing: JoinStanding;
    /** Who asked. Both halves are optional on the wire; a row that can name
     *  neither is still a real ask and still has to be answerable, so the UI
     *  is handed the blanks rather than a fabricated name. */
    name: string;
    email: string;
    askedAt: string;
}

const STANDING: Record<string, JoinStanding> = {
    PENDING: "pending",
    APPROVED: "approved",
    REJECTED: "refused",
};

/** What the platform said about an ask, or null if it did not describe one.
 *
 *  An unrecognised status reads as `pending`, for the reason `readJoin`
 *  defaults closed: the two mistakes are not symmetrical. Reading an unknown
 *  status as `approved` tells somebody they are in when they are not, and they
 *  find out by clicking into a wall.
 */
export function readJoinRequest(raw: unknown): JoinRequest | null {
    if (!raw || typeof raw !== "object") return null;
    const said = raw as {
        id?: unknown; pod_id?: unknown; status?: unknown;
        user_name?: unknown; user_email?: unknown;
        requested_at?: unknown; created_at?: unknown;
    };
    const id = String(said.id ?? "");
    if (!id) return null;
    return {
        id,
        podId: String(said.pod_id ?? ""),
        standing: STANDING[String(said.status ?? "").toUpperCase()] ?? "pending",
        name: String(said.user_name ?? "").trim(),
        email: String(said.user_email ?? "").trim(),
        askedAt: String(said.requested_at ?? said.created_at ?? ""),
    };
}

/** What to call whoever asked.
 *
 *  An ask with no name on it is not a broken row — the platform fills
 *  `user_name` from a profile that a brand-new account has not written yet,
 *  which is exactly the account most likely to be knocking. The address is the
 *  better answer than "Unknown", and where there is not one either, saying so
 *  plainly beats a blank cell that reads as a rendering bug.
 */
export function askerName(request: JoinRequest): string {
    return request.name || request.email || "Somebody with no name set";
}
