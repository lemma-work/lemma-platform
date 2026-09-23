import { Surfaces, useSurfaces } from "@/shell/surfaces";
import {
    AgentIcon, EditIcon, ExternalIcon, ChatIcon, BrowserIcon, CheckCircleIcon, ClockIcon,
    ConnectorIcon, GlobeIcon, ImageIcon, LibraryIcon, MemoryIcon, QuestionIcon,
    SparkleIcon, TerminalIcon, ToolIcon, VoiceIcon,
} from "@/ui/icons";
import { useEffect, useId, useRef, useState, type ReactNode, type RefObject } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { initialsOf, saidAbout, source } from "@/data";
import type { Member, Profile, Pod, Surface } from "@/data";
import { Mark } from "@/shell/mark";
import { identityGenes } from "@/identity/seeded-identity";
import { pressSlot } from "@/identity/palette";
import { AgentsView } from "./agents-view";
import { capabilityList, type ToolsetIcon } from "./colleagues";
import { SkillsView } from "@/skills/skills-view";
import { StandingWork } from "@/schedule/standing-work";
import { WorkflowsView } from "@/workflow/workflows-view";
import { AtTheDoor } from "@/shell/at-the-door";
import { RunsOn } from "@/shell/runs-on";
import { WhoCanJoin } from "@/shell/who-can-join";

/** The teammate's own page.
 *
 *  A pod is configured on the platform; here it is *someone*. So this borrows
 *  the shape everybody already reads without instructions — a profile, with a
 *  badge beside it — and fills it with nothing but facts the API already
 *  holds: the pod's description, its default agent's instruction and
 *  toolsets, the schedules it keeps, the apps it runs, the addresses it
 *  answers on. No invented endorsements, no numbers with nothing behind them.
 *  A section with nothing in it says so rather than showing a placeholder. */

function monthOf(iso: string): string {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleDateString([], { month: "long", year: "numeric" });
}

function shortMonth(iso: string): string {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleDateString([], { month: "short", year: "numeric" });
}

/** How long it has been at it, said the way a CV says it. */
function tenure(iso: string): string {
    if (!iso) return "";
    const start = new Date(iso).getTime();
    if (Number.isNaN(start)) return "";
    const months = Math.max(0, Math.round((Date.now() - start) / (1000 * 60 * 60 * 24 * 30.44)));
    if (months < 1) return "less than a month";
    if (months < 12) return months + " mo";
    const years = Math.floor(months / 12);
    const rest = months % 12;
    return years + " yr" + (rest ? " " + rest + " mo" : "");
}

/** The id, in the only form worth printing on a card.
 *
 *  Hashed rather than sliced: a pod id is often a slug, and the first eight
 *  characters of "marketing" print as MARK-ETIN, which reads as a bug. Eight
 *  hex digits read as a badge number, and the same pod gets the same one. */
function badgeNumber(podId: string): string {
    let hash = 0x811c9dc5;
    for (let index = 0; index < podId.length; index += 1) {
        hash ^= podId.charCodeAt(index);
        hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    const hex = hash.toString(16).toUpperCase().padStart(8, "0");
    return hex.slice(0, 4) + "-" + hex.slice(4);
}

/** How a badge is die-cut.
 *
 *  Fractions of the card, because both the SVG mask and the window that holds
 *  the creature are driven from the same numbers — two descriptions of one
 *  hole drift apart the moment either is touched, which is exactly how the
 *  last version ended up drawing two concentric circles a few pixels apart.
 *
 *  An ellipse rather than a circle: a die-cut in a portrait card reads wider
 *  than tall. And every cut takes the lanyard slot with it, so the strap
 *  passes *through* the card instead of stopping behind it — which is most of
 *  what makes the object look manufactured rather than drawn.
 */
interface Cut {
    cx: number;
    cy: number;
    rx: number;
    ry: number;
}

const CUTS: readonly Cut[] = [
    /* The centred one is smaller than it looks like it should be, and smaller
       than it was. A die-cut off to one side reads as a window with card
       around it; the same size centred reads as a card with its middle
       missing, because there is an even margin on both sides and nothing to
       say which is the shape and which is the hole. It was 0.355 wide — 71% of
       the card — against 0.235 for the two beside it. */
    { cx: 0.5, cy: 0.325, rx: 0.25, ry: 0.165 },
    { cx: 0.655, cy: 0.255, rx: 0.235, ry: 0.15 },
    { cx: 0.345, cy: 0.255, rx: 0.235, ry: 0.15 },
];

/** The slot the lanyard threads through, in the same fractions. */
const SLOT = { x: 0.412, y: 0.034, w: 0.176, h: 0.034, r: 0.017 };

/** How big the name gets to be before anything has been measured.
 *
 *  Computed rather than stepped. Fixed buckets kept cutting names that fell
 *  just inside one — "theoremproving" at the 12cqw step needed 194px of a
 *  189px line, so it broke mid-word and printed "theoremprovin / g".
 *
 *  The card gives the name 82% of its width and the face averages about
 *  0.52em a character, so the size that fits is 82 / (0.52 x length), which
 *  is where the 158 comes from. Capped at 21cqw so a three-letter name does
 *  not become the whole card, and floored at 6cqw so a very long one is still
 *  legible.
 *
 *  It is an average, and this is the first paint only — `useFittedName`
 *  replaces it with a measurement.
 */
function nameSize(name: string): string {
    const length = Math.max(1, name.trim().length);
    return Math.max(6, Math.min(21, 158 / length)).toFixed(1) + "cqw";
}

/** The size the name is actually printed at.
 *
 *  An average of 0.52em a character cannot know that "Openloops" is nine wide
 *  round letters and "theoremproving" fourteen mostly narrow ones. It lands
 *  within a few percent either way — and a few percent is exactly the margin
 *  that decides whether the last letter is on the card or shaved off its right
 *  edge. It was shaving them off silently, because the wordmark is clipped
 *  sideways and a clip does not complain.
 *
 *  So the average is only what the first paint uses. Once the element is on
 *  the page and the face's own font has arrived, the string is measured and
 *  the size solved for rather than guessed. The answer is in `cqw` and holds
 *  at every card size, because the type and the card scale together — so this
 *  runs once a name, not on every resize.
 */
function useFittedName(name: string, target: RefObject<HTMLSpanElement | null>): string {
    const [size, setSize] = useState(() => nameSize(name));

    useEffect(() => {
        let live = true;
        setSize(nameSize(name));

        const fit = () => {
            const el = target.current;
            const card = el?.closest<HTMLElement>(".idcard");
            if (!live || !el || !card) return;
            const line = el.clientWidth;
            const across = card.clientWidth;
            if (line <= 0 || across <= 0) return;

            const style = getComputedStyle(el);
            const at = parseFloat(style.fontSize);
            const ink = document.createElement("canvas").getContext("2d");
            if (!ink || !(at > 0)) return;

            ink.font = style.fontWeight + " " + at + "px " + style.fontFamily;
            /* Letter-spacing is not part of the canvas font, and CSS adds it
               after every character including the last — so it is one
               multiplication rather than a second measurement. */
            const tracking = parseFloat(style.letterSpacing) || 0;
            const perEm = (ink.measureText(name).width + tracking * name.length) / at;
            if (!(perEm > 0)) return;

            /* A pixel back from the edge. The browser rounds, and landing
               exactly on the boundary is a coin toss that costs a letter. */
            setSize(Math.max(6, Math.min(21, ((line - 1) / perEm / across) * 100)).toFixed(2) + "cqw");
        };

        fit();
        void document.fonts?.ready.then(fit);
        return () => {
            live = false;
        };
    }, [name, target]);

    return size;
}

/** Who is answerable for this teammate.
 *
 *  An owner if the roster names one. Failing that, the only person in the
 *  pod — which is not a guess: if exactly one human is in here, they are who
 *  you go to, whatever the API decided to call their role.
 *
 *  The first version required `role === "Owner"` and nothing else, which read
 *  as strict and was simply wrong against live data: most pods come back with
 *  one member on some other role string, so the one line the badge exists to
 *  carry printed "No lead assigned" on nearly every real pod. Two or more
 *  people and no owner is the only genuinely ambiguous case, and that is the
 *  only one that still declines to answer. */
/** The lead's name, short enough to print.
 *
 *  A pod member comes back with whatever the account has, and for most people
 *  that is an email address — so the one line the badge exists to carry was
 *  setting `deepakjha0196@gmail.com` across two lines in the card's own voice.
 *  A badge prints a person's name. Where there is only an address, the local
 *  part is the closest thing to one it holds. */
function leadName(raw: string): string {
    const name = raw.trim();
    const at = name.indexOf("@");
    return at > 0 ? name.slice(0, at) : name;
}

function leadOf(members: Member[]): string | null {
    const people = members.filter((member) => member.kind === "person");
    const owner = people.find((member) => /owner/i.test(member.role));
    if (owner) return leadName(owner.name);
    return people.length === 1 ? leadName(people[0].name) : null;
}

/** A capability, drawn.
 *
 *  The mapping lives here rather than in `colleagues.ts` because that module
 *  is pure and is read by a test running under Node, where a `.tsx` import
 *  would be JSX in a runtime with no JSX in it. So the table there names a
 *  key and this names the drawing, and adding a toolset touches both. */
const TOOLSET_ICONS: Record<ToolsetIcon, typeof TerminalIcon> = {
    shell: TerminalIcon,
    browser: BrowserIcon,
    /* A globe rather than a magnifier: the magnifier is this app's search
       control, and a capability chip that borrows a control's icon reads as
       something you can press. */
    web: GlobeIcon,
    pod: LibraryIcon,
    connectors: ConnectorIcon,
    delegate: AgentIcon,
    message: ChatIcon,
    speech: VoiceIcon,
    skills: SparkleIcon,
    memory: MemoryIcon,
    plan: CheckCircleIcon,
    ask: QuestionIcon,
    wait: ClockIcon,
    image: ImageIcon,
    other: ToolIcon,
};

/** What this teammate may reach for.
 *
 *  These are toolsets, and toolsets are not skills — which is why they are no
 *  longer printed under a heading saying Skills. They are worth keeping on the
 *  page all the same: a skill is something the teammate has been taught, and
 *  this is what it is allowed to touch, and neither answers the other's
 *  question.
 *
 *  A strip, not a list. One icon and one or two words each, so eleven of them
 *  are two lines you take in at a glance rather than eleven clauses nobody
 *  reads. The longer phrasing is still there on the title and the accessible
 *  name, where length is free.
 *
 *  The heading does not say "Tools" for one specific reason. `MEMORY` carries
 *  no tools at all — the platform grants it so the agent is taught the memory
 *  contract, and the reading and writing happen through the shell and the pod
 *  — so a heading promising tools would be a small version of the same lie
 *  this section was built to remove. */
function Reach({ me }: { me: Profile }) {
    if (me.unavailable?.includes("tools")) return <p className="empty-row">Couldn’t load available tools.</p>;
    const granted = capabilityList(me.skills.map((skill) => skill.id));

    if (granted.length === 0) {
        /* Safe to say because an empty list here means an empty list. The
           pod's own responder comes back with `toolsets: []` and a fixed set
           applied at run time; `grantedToolsets` resolves that before this
           ever sees it, so what is left really is an agent somebody made with
           nothing on it. */
        return <p className="empty-row">Nothing granted — it can read and write its reply, and that is all.</p>;
    }

    /* The ones every teammate has go last. A stable sort, so the ordering
       `capabilityList` already applies — the most telling first — survives
       inside each group. */
    /* The uninformative ones are dropped rather than drawn last and quietly.
       "Waits", "Plans" and "Asks first" are in the fixed set the platform
       hands every pod's own responder, so they are true of every teammate
       anybody will ever open this page on — three chips that cannot
       distinguish one from another, padding a strip whose entire job is to
       say what *this* one can reach for. They are still on each agent's own
       row under Agents, where the comparison is between agents and they do
       tell you something. */
    const ordered = granted.filter((capability) => !capability.plain);

    return (
        <ul className="toolline">
            {ordered.map((capability) => {
                const Glyph = TOOLSET_ICONS[capability.icon];
                return (
                    <li
                        className="toolchip"
                        key={capability.code}
                        data-plain={capability.plain || undefined}
                        title={capability.says}
                    >
                        <Glyph size={15} aria-hidden="true" />
                        <span aria-hidden="true">{capability.word}</span>
                        <span className="sr-only">{capability.says}</span>
                    </li>
                );
            })}
        </ul>
    );
}

function Section({
    title,
    meta,
    children,
}: {
    title: string;
    meta?: string;
    children: React.ReactNode;
}) {
    return (
        <section className="pcard" tabIndex={-1} data-profile-section={title.toLowerCase()}>
            <div className="pcard__head">
                <h3>{title}</h3>
                {meta && <span className="meta">{meta}</span>}
            </div>
            {children}
        </section>
    );
}

/** The lanyard badge.
 *
 *  A credential rather than a card. The order is fixed and it is the order a
 *  real one uses: who issued it, a die-cut window, the name at display size,
 *  the role, the number — then a rule, and under the rule the one fact the
 *  rest of the badge exists to carry, which is the name of the person
 *  answerable for this teammate. Everything above the rule describes the
 *  agent; the thing below it describes who it answers to. That break is the
 *  whole hierarchy and it is why the rule is a real element rather than a
 *  margin.
 *
 *  The field is the teammate's own identity tone at full strength, which is a
 *  deliberate exception to the rule that the accent appears about four times
 *  a screen: a badge is a figure, not chrome, and it appears where identity is
 *  the subject rather than the label on something else.
 *
 *  The window is cut through the field, so what shows in it is the page
 *  behind — and sitting in that hole is the creature. That is the join
 *  between the app's two ways of saying who somebody is: identity as
 *  *issued* (a number, a role, an org, a named lead) printed on the outside,
 *  and identity as *derived* (a seed, a tone, a body, eyes) showing through
 *  the middle. Neither one is decoration for the other.
 *
 *  It still flips, because of course it flips. */
/** The badge takes a subject, not a pod, because a candidate on the hiring
 *  floor gets one too — the same lanyard, minus a number it has not been
 *  issued yet. */
function IdCard({
    seed,
    name,
    iconUrl,
    orgName,
    title,
    joined,
    lead,
    issued = true,
}: {
    seed: string;
    name: string;
    iconUrl: string | null;
    orgName: string;
    title: string;
    joined: string;
    /** The person answerable for this teammate. Absent on a candidate, who
     *  does not answer to anybody yet. */
    lead?: string | null;
    issued?: boolean;
}) {
    const [back, setBack] = useState(false);
    /* Both sides carry the character, so both wave when they turn to face
       you — and on opening the badge. The side facing away waves to nobody
       and settles before it comes back round. */
    const [hello, setHello] = useState(0);
    useEffect(() => { setHello(count => count + 1); }, [back]);
    const wordmark = useRef<HTMLSpanElement>(null);
    const printed = useFittedName(name, wordmark);
    const genes = identityGenes(seed);
    const cut = CUTS[genes.form % CUTS.length];
    const cutId = "cut-" + useId().replace(/[^a-zA-Z0-9-]/g, "");
    /* The back takes the lanyard slot but not the window. A real badge is
       punched through, so the hole belongs on both faces — but what you saw
       through it was the card's own cast shadow, a grey crescent sitting
       above the portrait, and the back is now a portrait and nothing else.
       The slot stays because the clip has to hang from something. */
    const slotId = "slot-" + useId().replace(/[^a-zA-Z0-9-]/g, "");

    return (
        <div className="idwrap">
            {/* The badge is photographed rather than drawn: it hangs in a lit
                scene of its own, and the hole is a real void onto that scene's
                ground rather than a filled disc. The ground stays lit in both
                appearances, because a photograph does not go dark when the
                page around it does — which is also what makes the void safe,
                since the creature standing in it always has a lit surface
                behind it. */}
            <div
                className="idscene"
                style={{
                    ["--field" as string]: pressSlot(genes.tone).field,
                    ["--field-ink" as string]: pressSlot(genes.tone).ink,
                    /* The resting angle, not the live one. Setting `--hang`
                       itself inline made it unoverridable — an inline custom
                       property beats every stylesheet rule, so `:hover` could
                       never straighten the card and nothing moved at all. The
                       stylesheet reads this as its default and is free to
                       replace it. */
                    ["--hang-rest" as string]: (genes.crest % 2 ? -1 : 1) * (1 + (genes.phase % 3) * 0.7) + "deg",
                    /* Both the hole and the creature standing in it come from
                       one description of the cut. */
                    ["--cut-x" as string]: cut.cx * 100 + "%",
                    ["--cut-y" as string]: cut.cy * 100 + "%",
                    ["--cut-w" as string]: cut.rx * 200 + "%",
                    ["--cut-h" as string]: cut.ry * 200 + "%",
                }}
            >
            {/* The mask is geometry, not an image, so the same path cuts the
                plate and the shadow it throws. */}
            <svg className="idcut" aria-hidden="true" focusable="false">
                <defs>
                    <mask id={cutId} maskContentUnits="objectBoundingBox">
                        <rect width="1" height="1" fill="#fff" />
                        <ellipse cx={cut.cx} cy={cut.cy} rx={cut.rx} ry={cut.ry} fill="#000" />
                        <rect x={SLOT.x} y={SLOT.y} width={SLOT.w} height={SLOT.h} rx={SLOT.r} fill="#000" />
                    </mask>
                    <mask id={slotId} maskContentUnits="objectBoundingBox">
                        <rect width="1" height="1" fill="#fff" />
                        <rect x={SLOT.x} y={SLOT.y} width={SLOT.w} height={SLOT.h} rx={SLOT.r} fill="#000" />
                    </mask>
                </defs>
            </svg>
            <div className="idclip" aria-hidden="true">
                <span className="idclip__strap" />
                <span className="idclip__clasp" />
            </div>
            <button
                className={"idcard" + (back ? " idcard--back" : "")}
                style={{
                    ["--name-size" as string]: printed,
                }}
                onClick={() => setBack((was) => !was)}
                title="Flip the card"
                aria-label={back ? "Show the front of the badge" : "Show the back of the badge"}
            >
                {/* Not a drop-shadow: a second copy of the same silhouette,
                    thrown onto the ground and sheared, the way a real one
                    falls. It sits inside the card so it is exactly the card's
                    box rather than a rectangle guessed from the scene. */}
                <span className="idcast" aria-hidden="true" style={{ maskImage: `url(#${cutId})`, WebkitMaskImage: `url(#${cutId})` }} />
                <span className="idcard__inner">
                    <span className="idcard__face">
                        {/* The colour, punched. Everything readable sits above it. */}
                        <span className="idcard__field" aria-hidden="true" style={{ maskImage: `url(#${cutId})`, WebkitMaskImage: `url(#${cutId})` }} />
                        {/* The cut edge: dark where the material turns away,
                            a light catch where it comes back. */}
                        <span className="idcard__rim" aria-hidden="true" />
                        <span className="idcard__top">
                            <span className="idcard__org">{orgName}</span>
                            <span>{issued ? "AGENT" : "CANDIDATE"}</span>
                        </span>
                        <span className="idcard__window">
                            <Mark seed={seed} name={name} icon={iconUrl} size={72} greeting={hello} />
                        </span>
                        <span className="idcard__name" ref={wordmark}>{name}</span>
                        <span className="idcard__role">{title}</span>
                        <span className="idcard__no">
                            {issued ? "AI\u2013" + badgeNumber(seed) : "AI\u2013\u2014\u2014\u2014\u2014"}
                        </span>
                        <span className="idcard__rule" />
                        <span className="idcard__lead">
                            {lead
                                ? "Human lead: " + lead
                                : issued
                                  ? "No lead assigned"
                                  : "Choose who they work with"}
                        </span>
                        <span className="idcard__since">
                            {(issued && shortMonth(joined)) || "NOT YET ISSUED"}
                        </span>
                    </span>

                    <span className="idcard__face idcard__face--rear">
                        {/* Whole stock, slot only. */}
                        <span className="idcard__backing" aria-hidden="true" style={{ maskImage: `url(#${slotId})`, WebkitMaskImage: `url(#${slotId})` }} />
                        {/* The back is the teammate, at the size a badge photo
                            wants to be, and nothing else. The addresses live in
                            the hero, where somebody looking for one would go;
                            here they read as a form on the back of a toy. */}
                        <span className="idcard__portrait">
                            <Mark seed={seed} name={name} icon={iconUrl} size={196} greeting={hello} />
                        </span>
                    </span>
                </span>
            </button>
            </div>
            <p className="idhint">Tap the badge</p>
        </div>
    );
}

/** Everything the page needs about whoever it is drawing.
 *
 *  A hired teammate and a candidate on the shelf are the same shape on
 *  purpose: what you read before hiring is the page you get afterwards, and
 *  that is a structural fact rather than a promise, because it is one
 *  component. What differs is supplied, not branched — the action in the
 *  hero, whether a badge has a number, whether there is a roster yet. */
export interface Subject {
    /** Seeds the mark and the banner. A candidate uses its archetype seed, so
     *  the face on the shelf is the face the hire comes out wearing. */
    seed: string;
    name: string;
    iconUrl: string | null;
    orgName: string;
    me: Profile;
    reach: Surface[];
    members: Member[];
    /** Omitted for a candidate: nobody has talked to them yet, and a zero
     *  there reads as a dead product rather than as an empty one. */
    stats?: { talks: number; people: number };
    issued?: boolean;
    /** The hero's primary control, and the chips beside it. */
    action: ReactNode;
    channels?: ReactNode;
    /** What sits under the badge. */
    aside?: ReactNode;
    /** What this teammate is run on. Absent for a candidate on the hiring
     *  floor — nothing has been hired, so there is nothing to set it on yet. */
    runsOn?: ReactNode;
    /** Who may let themselves in, rendered — a node for the same reason
     *  `runsOn` is, and absent on the hiring floor for the same reason: a
     *  candidate has no door yet. It is drawn under the roster, where the
     *  question it answers gets asked. */
    joining?: ReactNode;
    /** Who is waiting to be let in, rendered — a node for the same reason
     *  `joining` and `agents` are: the section reads its own list, owns its
     *  own refusal, and draws nothing at all for the many people who may not
     *  read it. Absent on a candidate, who has no pod for anybody to knock
     *  on. */
    knocking?: ReactNode;
    /** Type over the name. Absent where the name is not this person's to
     *  change — a candidate's name is set at the moment of hiring, and that
     *  field is on the hiring floor. */
    onRename?: (name: string) => Promise<void>;
    onOpenProject?: (tabId: string) => void;
    onRetryProfile?: () => void;
    /** The other agents in this pod. Absent for a candidate: nothing has been
     *  hired, so there is nobody for it to hand work to yet. */
    /** The pod's agents, rendered. A node rather than data, the way
     *  `channels` and `runsOn` already are: this view stays presentational and
     *  the section fetches its own list, with its own cache and its own
     *  failure — a section that cannot load should not take the page. */
    agents?: ReactNode;
    /** The skills, rendered — a node for the same reason `agents` is. The deck
     *  costs one listing plus a file per skill, holds its own cache and its own
     *  failure, and a section that cannot load must not take the page.
     *
     *  Absent on a listing: a candidate has no pod, so there is no `/skills`
     *  to read and an empty deck would be a claim about nothing. */
    skills?: ReactNode;
    /** The schedules, rendered. A node rather than data, for the same reason
     *  `agents` is: the section reads its own list, holds its own pending and
     *  failure, and can pause one without the whole profile refetching.
     *
     *  Absent on a listing, where `me.commitments` is drawn instead — a
     *  candidate's schedules are a description of what would arrive, and there
     *  is nothing there yet to pause or retry. */
    standing?: ReactNode;
    /** The workflows, rendered, for the same reason `agents` and `standing`
     *  are nodes: the section reads its own list and owns its own failure.
     *
     *  Here rather than in a tab, and the Agents tab that was built and pulled
     *  this week is the argument: permanent furniture on every teammate, for a
     *  list most pods have three rows of. "What runs the same way twice" is
     *  the same question as "what is this teammate made of", and that question
     *  is already this page.
     *
     *  Absent on a listing — a candidate has no runs, and an empty run history
     *  reads as a product that does not work rather than as one nobody has
     *  hired yet. */
    flows?: ReactNode;
    /** Still arriving, and — separately — could not be read at all.
     *
     *  These were both `undefined` colleagues, which rendered as no section.
     *  The file already said a section that cannot load should not take the
     *  page with it; it took *itself* away instead, with no trace, so a failed
     *  request and a teammate that genuinely works alone were the same blank
     *  space. A request that fails has to say so — it is the only way anybody
     *  can tell which of the two they are looking at. */
    colleaguesPending?: boolean;
    colleaguesProblem?: string | null;
    onRetryColleagues?: () => void;
    /** Open one's own conversation — where it gets changed. */
    onDiscussAgent?: (name: string) => void;
    /** Said instead of "no apps yet" when this is a listing rather than a
     *  pod: an empty section on a candidate means *comes with none*, which
     *  is a different sentence from *has not made one yet*. */
    emptyVoice?: "hired" | "candidate";
}

/** A headline a pod usually does not have. Rather than an empty line under
 *  the name, say what it actually keeps — a truer headline anyway. */
export function headlineFor(me: Profile): string {
    const standing = me.commitments.filter((item) => item.active).length;
    /* Toolsets are deliberately not counted here as "N tool bundles". Every
       teammate has twelve, so the number separates nobody from anybody — and
       "bundles" is the wire's word for it, not a person's. What a teammate
       *keeps* is what it has been set up to do. */
    const derived = [
        !me.unavailable?.includes("schedules") && standing ? standing + (standing === 1 ? " standing job" : " standing jobs") : "",
        !me.unavailable?.includes("apps") && me.projects.length ? me.projects.length + (me.projects.length === 1 ? " app" : " apps") : "",
    ].filter(Boolean);
    return me.headline || (derived.length ? "Keeps " + derived.join(", ") : "Give me a responsibility and we’ll get started.");
}

/** The name, and typing over it.
 *
 *  Renaming happens here rather than in a settings screen because the name is
 *  not a setting: it is the largest word on the teammate's own page, and the
 *  place people try to change a name is the place the name is. So the display
 *  type doubles as the field — same size, same tracking, a rule under it while
 *  it is open — and the pencil only appears under the pointer, so a page being
 *  read is still a page of facts.
 *
 *  One save path. Enter and clicking away both blur, blur is what writes, and
 *  Escape sets a flag on its way out so the blur it causes knows it was a
 *  cancel. Two of these — a save on Enter and a save on blur — is how a rename
 *  gets sent twice.
 *
 *  The typed name shows immediately and stays until the write comes back. A
 *  name that snaps to the old one for as long as a request takes reads as a
 *  rejection, and this one is almost never rejected.
 */
function HeroName({ name, onRename }: { name: string; onRename?: (next: string) => Promise<void> }) {
    const [draft, setDraft] = useState<string | null>(null);
    const [pending, setPending] = useState<string | null>(null);
    const [problem, setProblem] = useState<string | null>(null);
    const cancelled = useRef(false);
    const field = useId();

    async function save(typed: string) {
        const clean = typed.trim();
        setDraft(null);
        /* An empty field is a cancel, not a request to be called nothing. */
        if (!clean || clean === name || !onRename) return;
        setPending(clean);
        setProblem(null);
        try {
            await onRename(clean);
        } catch (trouble) {
            setProblem(saidAbout(trouble, "That name would not save."));
        } finally {
            setPending(null);
        }
    }

    const shown = pending ?? name;

    return (
        <>
            <h2 className="hero__name">
                {draft !== null ? (
                    <input
                        id={field}
                        className="hero__field"
                        autoFocus
                        aria-label="This teammate's name"
                        /* Sized to what is in it, so the badge beside the
                           name does not travel to the far edge the moment
                           the field opens. `field-sizing` does this exactly
                           where it exists; `size` is the approximation
                           everywhere else, and being a character or two wide
                           is not something anybody reads as wrong. */
                        size={Math.max(draft.length, 2)}
                        value={draft}
                        onChange={(event) => setDraft(event.target.value)}
                        onFocus={(event) => event.currentTarget.select()}
                        onBlur={(event) => {
                            if (cancelled.current) { cancelled.current = false; setDraft(null); return; }
                            void save(event.target.value);
                        }}
                        onKeyDown={(event) => {
                            if (event.key === "Enter") { event.preventDefault(); event.currentTarget.blur(); }
                            if (event.key === "Escape") { event.preventDefault(); cancelled.current = true; event.currentTarget.blur(); }
                        }}
                    />
                ) : onRename ? (
                    <button
                        className="hero__rename"
                        title={"Rename " + shown}
                        onClick={() => { setProblem(null); cancelled.current = false; setDraft(shown); }}
                    >
                        <span>{shown}</span>
                        <EditIcon size={17} aria-hidden="true" />
                    </button>
                ) : shown}
                {/* No badge beside the name. It said "an agent, not a person"
                    to a reader who is on an agent's profile, in an app whose
                    every teammate is one — and the whole page is already that
                    sentence: the creature, the lanyard, the AGENT printed on
                    the card. A mark that repeats what surrounds it is a mark
                    that is only in the way of the name. */}
            </h2>
            {problem && <p className="hero__problem" role="alert">{problem}</p>}
        </>
    );
}

export function ProfileView({ subject, initialSection }: { subject: Subject; initialSection?: string }) {
    const { me, seed, name, orgName, reach, members, stats } = subject;
    const pane = useRef<HTMLDivElement>(null);
    useEffect(() => {
        if (!initialSection || !pane.current) return;
        const node = pane.current;
        const target = initialSection === "standing work" ? "schedules" : initialSection;
        const section = Array.from(node.querySelectorAll<HTMLElement>("[data-profile-section]")).find(item => item.dataset.profileSection === target);
        if (section) node.scrollTop += section.getBoundingClientRect().top - node.getBoundingClientRect().top - 24;
    }, [initialSection]);
    const [descriptionOpen, setDescriptionOpen] = useState(false);
    const descriptionId = useId();
    const candidate = subject.emptyVoice === "candidate";
    const headline = headlineFor(me);
    const sections = [
        subject.agents && ["agents", "Agents"],
        subject.runsOn && ["runs on", "Runs on"],
        subject.skills && ["skills", "Skills"],
        [candidate ? "work to set up" : "schedules", "Schedules"],
        [candidate ? "apps to build together" : "apps", "Apps"],
        subject.flows && ["workflows", "Workflows"],
        (members.length > 0 || subject.joining || subject.knocking) && ["people with access", "People with access"],
    ].filter((entry): entry is string[] => Array.isArray(entry));

    return (
        <div className="pane pane--profile" ref={pane}>
            <div className="profile">
                {Boolean(me.unavailable?.length) && (
                    <p className="empty-row profile__load-error" role="alert">
                        Some profile details couldn’t be loaded. {subject.onRetryProfile && (
                            <button className="linkish" onClick={subject.onRetryProfile}>Try again</button>
                        )}
                    </p>
                )}
                <div className="profile__main">
                    <section className="pcard pcard--hero">
                        {/* The masthead is printed ON the field, not above it. An
                            empty colour bar with the name underneath it on white
                            is a header illustration; the name standing in the
                            colour is a masthead, and that is the whole gesture
                            this language is borrowed from. */}
                        <div
                            className="hero__masthead"
                        >
                            <div className="hero__avatar">
                                <Mark seed={seed} name={name} icon={subject.iconUrl} size={64} />
                                {reach.length > 0 && <span className="hero__oncall">REACHABLE</span>}
                            </div>
                            <div className="hero__lede">
                                <HeroName name={name} onRename={subject.onRename} />
                                <p id={descriptionId} className="hero__headline" data-collapsed={headline.length > 180 && !descriptionOpen || undefined}>{headline}</p>
                                {headline.length > 180 && <button className="linkish hero__description-toggle" aria-expanded={descriptionOpen} aria-controls={descriptionId} onClick={() => setDescriptionOpen(!descriptionOpen)}>
                                    {descriptionOpen ? "Show less" : "Read full description"}
                                </button>}
                                <p className="hero__rule" aria-hidden="true" />
                                <p className="hero__meta">
                                    {orgName}
                                    {me.joined && <> · joined {monthOf(me.joined)}</>}
                                    {me.joined && <> · {tenure(me.joined)} in the job</>}
                                    {candidate && <> · suggested role</>}
                                </p>
                            </div>
                        </div>
                        <div className="hero__body">
                            {stats && (
                                <p className="hero__stats">
                                    <b>{stats.talks}</b> {stats.talks === 1 ? "conversation" : "conversations"} ·{" "}
                                    <b>{stats.people}</b> {stats.people === 1 ? "person" : "people"} with access
                                </p>
                            )}

                            <div className="hero__acts">
                                {subject.action}
                                {subject.channels}
                            </div>
                        </div>
                    </section>

                    <nav className="profile__contents" aria-label="What’s in this pod">
                        <strong>What’s in this pod</strong>
                        <div>{sections.map(([target, label]) => <button key={target} className="btn" onClick={() => {
                            const section = Array.from(pane.current?.querySelectorAll<HTMLElement>("[data-profile-section]") ?? []).find(item => item.dataset.profileSection === target);
                            section?.scrollIntoView({ block: "start" });
                            section?.focus({ preventScroll: true });
                        }}>{label}</button>)}</div>
                    </nav>

                    {/* About is gone. It carried the teammate's own `about`
                        and, far more often, the sentence saying there was not
                        one — and what it was reaching for is now said twice
                        over on this same page: the headline under the name,
                        and each agent's instruction in full under Agents. A
                        section whose commonest state is an apology for being
                        empty is a section the page is better without. */}

                    {subject.runsOn && (
                        /* Directly under About, not at the foot of the page.
                           It is the only thing here anybody changes, and what
                           a teammate thinks with belongs beside its own
                           account of itself rather than below the furniture. */
                        <Section title="Runs on" meta="what it thinks with">
                            {subject.runsOn}
                        </Section>
                    )}

                    {/* Two sections where there was one, because there were two
                        different things under that one heading and neither of
                        them was a skill. The toolsets are what it may reach
                        for; the skills are what it has been taught. And
                        `me.permits` — `allowed_actions` — is gone from this
                        page entirely: those are *your* permissions on an agent
                        row, not a fact about the teammate, and the Agents
                        detail already lists them under "You may". */}
                    <Section title="What it can reach for" meta={candidate ? "tools to consider for this job" : "tools available to the agent answering here"}>
                        <Reach me={me} />
                    </Section>

                    {/* Absent on the hiring floor, where there is no pod to
                        read a `/skills` folder out of. A candidate showing an
                        empty deck would be reporting a fact about a pod that
                        does not exist yet. */}
                    {subject.skills && (
                        <Section title="Skills" meta="what it has been taught">
                            {subject.skills}
                        </Section>
                    )}

                    <Section
                        title={candidate ? "Work to set up" : "Schedules"}
                        meta={me.commitments.length ? (candidate ? "suggested schedules" : "scheduled work") : undefined}
                    >
                        {subject.standing ?? (me.unavailable?.includes("schedules") ? <p className="empty-row">Couldn’t load schedules.</p> : me.commitments.length === 0 ? (
                            <p className="empty-row">
                                {candidate
                                    ? "Set a schedule together once the job is ready."
                                    : "No schedules yet."}
                            </p>
                        ) : (
                            <ul className="commits">
                                {me.commitments.map((item) => (
                                    <li key={item.id}>
                                        <span className={"commits__dot" + (item.active ? "" : " commits__dot--off")} />
                                        <span className="commits__body">
                                            <span className="commits__title">{item.title}</span>
                                            <span className="commits__when">
                                                {item.cadence}
                                                {item.since && <> · since {shortMonth(item.since)}</>}
                                                {!candidate && !item.active && <> · paused</>}
                                            </span>
                                            {item.detail && <span className="commits__detail">{item.detail}</span>}
                                        </span>
                                        {item.last && <span className="commits__last">last {item.last}</span>}
                                    </li>
                                ))}
                            </ul>
                        ))}
                    </Section>

                    <Section
                        title={candidate ? "Apps to build together" : "Apps"}
                        meta={me.projects.length ? (candidate ? "suggestions for the job" : "open in a tab") : undefined}
                    >
                        {me.unavailable?.includes("apps") ? <p className="empty-row">Couldn’t load apps.</p> : me.projects.length === 0 ? (
                            <p className="empty-row">
                                {candidate
                                    ? "Ask your teammate to build an app for the job."
                                    : "No apps yet. Ask it for one in the conversation."}
                            </p>
                        ) : (
                            <div className="projects">
                                {me.projects.map((project) => (
                                    <button
                                        className="project"
                                        key={project.id}
                                        disabled={!subject.onOpenProject}
                                        onClick={() => subject.onOpenProject?.(project.tabId)}
                                    >
                                        <span className="project__name">{project.name}</span>
                                        {project.description && (
                                            <span className="project__detail">{project.description}</span>
                                        )}
                                        <span className="project__status">
                                            {project.status || "app"} {subject.onOpenProject && <ExternalIcon size={14} />}
                                        </span>
                                    </button>
                                ))}
                            </div>
                        )}
                    </Section>

                    {/* Agents live here rather than in a tab of their own. A
                        permanent tab per teammate, for a list most pods have
                        three rows of, was a lot of furniture for something you
                        look at when you are already asking what this teammate
                        is made of — which is this page. */}
                    {subject.agents && (
                        <Section title="Agents" meta="what this teammate hands work to">
                            {subject.agents}
                        </Section>
                    )}

                    {subject.flows && (
                        <Section title="Workflows" meta="repeatable steps for the job">
                            {subject.flows}
                        </Section>
                    )}


                    {/* The roster and the door, in one section. Who is here
                        is the question that raises who else could be, and an
                        access rule kept anywhere else is a rule nobody reads
                        until somebody is already in. */}
                    {(members.length > 0 || subject.joining || subject.knocking) && (
                        <Section
                            title="People with access"
                            meta={members.length > 0 ? "who can ask, and what each may do" : undefined}
                        >
                            {members.length === 0 && (
                                <p className="empty-row">Nobody here but you yet.</p>
                            )}
                            {members.length > 0 && <ul className="roster">
                                {members.map((member) => (
                                    <li key={member.id}>
                                        <span className={"face" + (member.kind === "teammate" ? " face--teammate" : "")}>
                                            {member.initials}
                                        </span>
                                        <span className="roster__body">
                                            <span className="roster__name">{member.name}</span>
                                            <span className="roster__role">{member.role}</span>
                                        </span>
                                        <span className="roster__can">{member.can}</span>
                                    </li>
                                ))}
                            </ul>}
                            {/* Between the two on purpose: who is here, who
                                is asking, who could. Reading the door rule
                                first and finding the queue underneath it puts
                                the decision after the policy that caused it. */}
                            {subject.knocking}
                            {subject.joining}
                        </Section>
                    )}
                </div>

                <aside className="profile__aside">
                    <IdCard
                        seed={seed}
                        name={name}
                        iconUrl={subject.iconUrl}
                        orgName={orgName}
                        title={headline}
                        joined={me.joined}
                        lead={candidate ? null : leadOf(members)}
                        issued={subject.issued !== false}
                    />
                    {subject.aside}
                </aside>
            </div>
        </div>
    );
}

export function ProfilePane({
    initialSection,
    openAgentName,
    onOpenAgentName,
    onAskFor,
    pod,
    orgName,
    others,
    onOpenTab,
    onPickPod,
    onMessage,
    onFile,
    onDiscussAgent,
    onDiscussWorkflow,
}: {
    initialSection?: string;
    pod: Pod;
    orgName: string;
    others: Pod[];
    onOpenTab: (tabId: string) => void;
    onPickPod: (podId: string) => void;
    onMessage: () => void;
    /** Put a pod file on the stage as a tab of its own — the same handler the
     *  Library is given, for the same reason: a path is handed up and the
     *  shell decides what a tab is.
     *
     *  Optional because the shell does not pass one here yet. Where it is
     *  absent a skill opens inside its own section instead, which is the same
     *  drill-down the Agents and Workflows sections on this page already do.
     *  Passing `onFile={openFile}` from the shell is all it takes to make a
     *  skill open as a file tab like any other file. */
    onFile?: (path: string) => void;
    /** Open an agent's own conversation — the place it gets changed. */
    onDiscussAgent?: (name: string) => void;
    /** And a workflow's, for the same reason: the shape is read here and
     *  changed by asking. Separate from `onDiscussAgent` rather than one
     *  `(kind, name)` prop because `ProfileView` hands each straight to the
     *  section that uses it, and a section taking a kind it must always pass
     *  the same value for is a parameter that only exists to be ignored. */
    onDiscussWorkflow?: (name: string) => void;
    /** Which agent to land expanded, when search sent somebody here. */
    openAgentName?: string | null;
    onOpenAgentName?: (name: string | null) => void;
    /** Put a request to this teammate in the composer, on the conversation
     *  tab, ready to be read and sent. The shell owns which conversation is
     *  open and owns the composer, so it does the filling. */
    onAskFor?: (text: string) => void;
}) {

    const queryClient = useQueryClient();

    const profile = useQuery({
        queryKey: ["profile", pod.id],
        queryFn: () => source.getProfile(pod.id),
        staleTime: 5 * 60_000,
    });

    /* The rail, the header, the badge and the masthead all read the pod out
       of one list, so the new name is written into that list rather than
       waited for: a refetch has to match a key and beat a stale time, and
       until it lands the page the rename happened on is the one page still
       showing the old name. The refetch still runs behind it.

       The teammate's name follows the pod's only where it *was* the pod's —
       a pod whose front agent has a name of its own keeps it. */
    const rename = useMutation({
        mutationFn: (name: string) => source.renamePod(pod.id, name),
        onSuccess: (_answer, name) => {
            queryClient.setQueryData(["pods", pod.orgId], (old: Pod[] | undefined) =>
                old?.map((entry) => entry.id !== pod.id ? entry : {
                    ...entry,
                    name,
                    teammate: entry.teammate.name === entry.name
                        ? { ...entry.teammate, name, initials: initialsOf(name) }
                        : entry.teammate,
                }),
            );
            void queryClient.invalidateQueries({ queryKey: ["pods"] });
            /* And the roster, which is a second query and the one that wins:
               the shell spreads pod detail *over* the listed pod, so a
               teammate name patched into the list alone is overwritten by a
               five-minute-stale copy of the old one — the rail would say the
               new name while the roster under it said the old. */
            void queryClient.invalidateQueries({ queryKey: ["pod-detail", pod.id] });
        },
    });

    /* Both of these are already on screen elsewhere in this pod, so they
       come from the same cache rather than being fetched twice. */
    const surfaces = useSurfaces(pod.id);
    const conversations = useQuery({
        queryKey: ["conversations", pod.id],
        queryFn: () => source.listConversations(pod.id),
        staleTime: 60_000,
    });

    if (profile.isError) {
        return (
            <div className="pane"><div className="pane__inner">
                <p className="empty-row">Couldn’t load this teammate’s profile. <button className="linkish" onClick={() => void profile.refetch()}>Try again</button></p>
            </div></div>
        );
    }

    if (!profile.data) {
        return (
            <div className="pane"><div className="pane__inner">
                <p className="empty-row">Looking {pod.name} up…</p>
            </div></div>
        );
    }

    const reach = (surfaces.data ?? []).filter(
        (surface) => surface.mine && surface.active !== false && Boolean(surface.handle),
    );

    return (
        <ProfileView
            initialSection={initialSection}
            subject={{
                seed: pod.id,
                name: pod.name,
                iconUrl: pod.iconUrl,
                orgName,
                me: profile.data,
                onRetryProfile: () => { void profile.refetch(); },
                reach,
                members: pod.members,
                stats: conversations.isSuccess ? { talks: conversations.data.length, people: pod.members.length } : undefined,
                onOpenProject: onOpenTab,
                onDiscussAgent,
                action: (
                    <button className="btn btn--primary hero__message" onClick={onMessage}>
                        <ChatIcon size={16} aria-hidden="true" /><span>Message</span>
                    </button>
                ),
                agents: (
                    <AgentsView
                        podId={pod.id}
                        teammate={pod.teammate?.name ?? pod.name}
                        embedded
                        open={openAgentName ?? null}
                        onOpen={(name) => onOpenAgentName?.(name)}
                        onDiscussAgent={onDiscussAgent}
                    />
                ),
                skills: (
                    <SkillsView
                        podId={pod.id}
                        teammate={pod.teammate?.name ?? pod.name}
                        onFile={onFile}
                        onCreate={onAskFor && (() => onAskFor(
                            "Write me a new skill. Use the lemma-skill-creator skill: decide its triggers, "
                            + "write the instructions, and publish it under /skills. Ask me what it should do first."
                        ))}
                    />
                ),
                standing: <StandingWork podId={pod.id} teammate={pod.teammate?.name ?? pod.name} />,
                flows: (
                    <WorkflowsView
                        podId={pod.id}
                        teammate={pod.teammate?.name ?? pod.name}
                        onDiscuss={onDiscussWorkflow}
                    />
                ),
                channels: <Surfaces pod={pod} expanded />,
                runsOn: <RunsOn podId={pod.id} orgId={pod.orgId} />,
                joining: <WhoCanJoin podId={pod.id} orgName={orgName} />,
                knocking: <AtTheDoor podId={pod.id} teammate={pod.teammate?.name ?? pod.name} />,
                onRename: rename.mutateAsync,
                aside: others.length > 0 ? (
                    <section className="pcard">
                        <div className="pcard__head"><h3>Also in {orgName}</h3></div>
                        <ul className="alsolist">
                            {others.slice(0, 6).map((other) => (
                                <li key={other.id}>
                                    <button onClick={() => onPickPod(other.id)}>
                                        <Mark seed={other.id} name={other.name} icon={other.iconUrl} size={26} />
                                        <span className="alsolist__body">
                                            <span className="alsolist__name">{other.name}</span>
                                            <span className="alsolist__sub">{other.subtitle}</span>
                                        </span>
                                    </button>
                                </li>
                            ))}
                        </ul>
                    </section>
                ) : undefined,
            }}
        />
    );
}
