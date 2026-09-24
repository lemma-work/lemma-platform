"use client";

import Link from "next/link";
import { useState } from "react";
import { Character } from "@/shell/character";
import { CharacterPuppet } from "@/shell/character-puppet";
import type { CharacterName } from "@/shell/cast";
import s from "./landing.module.css";

/** A character that wakes up when you come near it.
 *
 *  Every rig already stops itself when it scrolls out of view, so the cost of
 *  putting several on one page is only ever the ones you can see. What this
 *  adds is the greeting: a wave is a response to somebody arriving, and the
 *  only way a page knows somebody arrived is the pointer. */
function Greeter({ character, size, label }: { character: CharacterName; size: number; label?: string }) {
    const [greeting, setGreeting] = useState(0);
    const [near, setNear] = useState(false);
    return (
        <span
            className={s.greeter}
            onPointerEnter={() => { setNear(true); setGreeting(count => count + 1); }}
            onPointerLeave={() => setNear(false)}
        >
            <CharacterPuppet
                character={character}
                size={size}
                greeting={greeting}
                mood={near ? "delighted" : "idle"}
                label={label}
            />
        </span>
    );
}

/* ── Works for everyone on your team ────────────────────────────────────
   Four people, one teammate, drawn as a ring rather than a thread.

   A transcript would say these happened in an order and that each reply
   waited for the last. They did not: these are four different people asking
   the same teammate four different things, which is the whole claim. So the
   teammate sits in the middle and the asks sit around it. */

/** Four moments on one piece of work.
 *
 *  Not four people asking four unrelated things — the Northfield refund, all
 *  day, picked up by whoever needed it next and from wherever they happened to
 *  be. That is the claim the section is making, so the messages have to carry
 *  a clock and a place or they are just four sentences.
 *
 *  The row is stated rather than left to auto-placement: the character spans
 *  both rows of the middle column, so the cursor has already walked past row
 *  two by the time the right-hand pair is placed, and they land a row low. */
type Ask = {
    who: string; mark: string; at: string; where: string; logo?: string;
    says: string; side: "left" | "right"; row: number;
};

const ASKS_AROUND: Ask[] = [
    {
        who: "Priya", mark: "P", at: "09:41", where: "in Lemma", side: "left", row: 1,
        says: "Does our policy actually allow the Northfield refund?",
    },
    {
        who: "Dev", mark: "D", at: "10:02", where: "from Slack", logo: "/connector-logos/slack.svg",
        side: "right", row: 1,
        says: "Did Priya's refund question land? Northfield are waiting on us.",
    },
    {
        who: "Sam", mark: "S", at: "14:20", where: "from WhatsApp", logo: "/connector-logos/whatsapp.svg",
        side: "left", row: 2,
        says: "Northfield emailed me too — has that refund actually gone out?",
    },
    {
        who: "Alex", mark: "A", at: "14:25", where: "in Lemma", side: "right", row: 2,
        says: "I can see the policy check and draft. Priya, this one is ready for your review.",
    },
];

export function Shared() {
    return (
        <section className={s.band} id="shared">
            <p className={s.sectionEyebrow}>WHAT SHARED MEANS</p>
            <h2 className={s.sectionHeading}>Works for everyone on your team.</h2>
            <div className={s.bandField}>
                <div className={s.orbit}>
                    {ASKS_AROUND.filter(a => a.side === "left").map(one => <Ask key={one.who} {...one} />)}
                    <span className={s.orbitFace}>
                        <Greeter character="gem" size={110} label="Support, a Lemma teammate" />
                        <span className={s.sharedRecord}>
                            <span>REFUND · ORDER 8841</span>
                            <b>Northfield · $420</b>
                            <em>Awaiting Priya’s review</em>
                            <span>Policy checked · reply drafted<br />Refund not issued</span>
                        </span>
                    </span>
                    {ASKS_AROUND.filter(a => a.side === "right").map(one => <Ask key={one.who} {...one} />)}
                </div>
            </div>
            <p className={s.sectionNote}>
                Keep the status, decisions and history in shared records your team can return to.
            </p>
        </section>
    );
}

function Ask({ who, mark, at, where, logo, says, side, row }: Ask) {
    return (
        <span className={side === "left" ? s.askLeft : s.askRight} style={{ gridRow: row }} data-row={row}>
            <i className={s.askMark}>{mark}</i>
            <span className={s.askBody}>
                <span className={s.askWho}>
                    {who}
                    <em>{at}</em>
                    <b>
                        {logo && <img src={logo} alt="" width={11} height={11} />}
                        {where}
                    </b>
                </span>
                {says}
            </span>
        </span>
    );
}

/* Connected channels share one teammate and one record. */

type Panel = {
    key: string; title: string; shortTitle: string; blurb: string;
    open: React.ReactNode;
};

const PANELS: Panel[] = [
    {
        key: "chat", title: "Slack and Teams", shortTitle: "Team chat", blurb: "Bring the work into your team’s conversation.",
        open: <ChatOpen />,
    },
    {
        key: "phone", title: "Telegram and WhatsApp", shortTitle: "Messaging", blurb: "Pick up the same work when you’re away.",
        open: <PhoneOpen />,
    },
    {
        key: "apps", title: "Apps", shortTitle: "Apps", blurb: "Open the work. Make edits. Review what’s ready.",
        open: <AppsOpen />,
    },
    {
        key: "clock", title: "Scheduled work", shortTitle: "Scheduled", blurb: "Set the rhythm. Get the next update without asking.",
        open: <ClockOpen />,
    },
];

export function Examples() {
    const [at, setAt] = useState(0);
    const shown = PANELS[at];
    return (
        <section className={s.section} id="examples">
            <p className={s.sectionEyebrow}>WORK WITH YOUR TEAMMATE</p>
            <h2 className={s.sectionHeading}>Works where you work.</h2>
            <p className={s.sectionNote}>Your AI teammates, in the tools you already use.</p>

            <div className={s.showcase}>
                <div className={s.switcher} role="tablist" aria-orientation="horizontal" aria-label="Ways to work with your teammate">
                    {PANELS.map((panel, index) => (
                        <button
                            key={panel.key}
                            type="button"
                            role="tab"
                            id={`ran-tab-${index}`}
                            aria-selected={index === at}
                            aria-controls="ran-panel"
                            tabIndex={index === at ? 0 : -1}
                            className={index === at ? s.pickOn : s.pick}
                            onClick={() => setAt(index)}
                            onKeyDown={event => {
                                const next = ["ArrowDown", "ArrowRight"].includes(event.key) ? (at + 1) % PANELS.length
                                    : ["ArrowUp", "ArrowLeft"].includes(event.key) ? (at - 1 + PANELS.length) % PANELS.length
                                    : event.key === "Home" ? 0 : event.key === "End" ? PANELS.length - 1 : -1;
                                if (next < 0) return;
                                event.preventDefault();
                                setAt(next);
                                document.getElementById(`ran-tab-${next}`)?.focus();
                            }}
                        >
                            <span className={s.pickFace}>
                                <span aria-hidden="true">0{index + 1}</span>
                            </span>
                            <span className={s.pickText}>
                                <b className={s.pickFull}>{panel.title}</b><b className={s.pickShort}>{panel.shortTitle}</b>
                                <em>{panel.blurb}</em>
                            </span>
                        </button>
                    ))}
                </div>

                <div
                    id="ran-panel"
                    role="tabpanel"
                    aria-labelledby={`ran-tab-${at}`}
                    className={s.wide}
                >
                    <div key={shown.key} className={s.exampleContent}>{shown.open}</div>
                </div>
            </div>
        </section>
    );
}

function WorkExample({ eyebrow, title, description, channels, children }: {
    eyebrow: string; title: string; description: string;
    channels?: { name: string; logo: string }[];
    children: React.ReactNode;
}) {
    return <div className={s.workExample}>
        <div className={s.workStory}>
            <span className={s.channelLabel}>{eyebrow}</span>
            <h3>{title}</h3>
            <p>{description}</p>
            {channels && <div className={s.messagingBrands}>{channels.map(channel => <span key={channel.name}><img src={channel.logo} alt="" width={22} height={22} />{channel.name}</span>)}</div>}
        </div>
        <div className={s.workVisual}>{children}</div>
    </div>;
}

function ChatOpen() {
    return <WorkExample eyebrow="IN YOUR TEAM’S CHANNELS" title="Part of the conversation. Part of the team."
        description="Every AI teammate on Lemma can work with your team in Slack and Microsoft Teams. Ask for work, review what’s ready, and follow up in the channels you already use."
        channels={[{ name: "Slack", logo: "/connector-logos/slack.svg" }, { name: "Microsoft Teams", logo: "/connector-logos/teams.svg" }]}>
            <div className={s.channelWindow}>
                <div className={s.channelHeader}>
                    <img src="/connector-logos/slack.svg" alt="Slack" width={22} height={22} />
                    <b>Acme</b><span># launch</span>
                </div>
                <div className={s.channelThread}>
                    <span className={s.channelLabel}>EXAMPLE CONVERSATION</span>
                    <span className={s.said}><i>PR</i><span><b>Priya <em>9:41 AM</em></b>Kit, what needs my attention before Thursday’s launch?</span></span>
                    <span className={s.said}>
                        <i className={s.saidBot}><Character character="loop" size={28} /></i>
                        <span><b>Kit <em>TEAMMATE</em></b>The landing page and announcement are ready for review. The demo needs three updated shots, and Harbor’s story still needs logo permission.</span>
                    </span>
                    <div className={s.workAttachment}>
                        <span className={s.channelLabel}>READY FOR REVIEW</span>
                        <b>Thursday launch review</b>
                        <span>2 assets to review · 2 blockers</span>
                        <span className={s.attachmentLink}>Landing page · Announcement</span>
                    </div>
                    <div className={s.channelComposer}>Message #launch<span>↵</span></div>
                </div>
            </div>
    </WorkExample>;
}

function PhoneOpen() {
    return <WorkExample eyebrow="ON YOUR PHONE" title="Your teammates. A message away."
        description="Every AI teammate on Lemma is accessible through WhatsApp and Telegram. Send a request, get an update, or keep work moving when you’re away from your desk."
        channels={[{ name: "WhatsApp", logo: "/connector-logos/whatsapp.svg" }, { name: "Telegram", logo: "/connector-logos/telegram.svg" }]}>
            <div className={s.phoneFrame} aria-label="Example WhatsApp conversation with Kit">
                <div className={s.phoneStatus}><span>9:41</span><span className={s.phoneIsland} /><span>▰</span></div>
                <div className={s.phoneHeader}><span aria-hidden="true">‹</span><Character character="loop" size={34} /><div><b>Kit</b><span>Launch producer</span></div><img src="/connector-logos/whatsapp.svg" alt="WhatsApp" width={22} height={22} /></div>
                <div className={s.phoneMessages}>
                    <span className={s.phoneDay}>THURSDAY · EXAMPLE</span>
                    <div className={s.phoneOutgoing}>On my way in. What’s still blocking the launch?<time>9:41</time></div>
                    <div className={s.phoneIncoming}>Three demo shots need updating. Harbor’s logo permission is still missing.<time>9:41</time></div>
                    <div className={s.phoneOutgoing}>And the announcement?<time>9:42</time></div>
                    <div className={s.phoneIncoming}>Revision 3 is ready in Launch studio. You can review it alongside the landing page. Nothing has been published.<time>9:42</time></div>
                </div>
                <div className={s.phoneComposer}><span>Message Kit</span><span aria-hidden="true">↑</span></div>
                <div className={s.phoneHome} />
            </div>
    </WorkExample>;
}

function AppsOpen() {
    return <WorkExample eyebrow="APPS BUILT FOR THE JOB" title="Go from talking about work to working on it."
        description="Your AI teammates build apps around the work you give them. Open a draft, update a record, or review a decision in a shared workspace your whole team can use.">
        <div className={s.workApp}>
            <div className={s.workAppHeader}><span>Launch studio · Interactive example</span><a href="/demo/launch" target="_blank" rel="noopener noreferrer">Open ↗</a></div>
            <iframe src="/demo/launch" title="Explore Kit’s launch studio" loading="lazy" sandbox="allow-scripts allow-same-origin allow-forms" />
        </div>
    </WorkExample>;
}

function ClockOpen() {
    return <WorkExample eyebrow="ON A SCHEDULE" title="The work keeps moving. Even before you ask."
        description="Give your AI teammates recurring work and choose when it runs. They check progress, prepare updates, and bring back what needs your attention.">
            <div className={s.scheduleReport}>
                <div className={s.scheduleAuthor}><Character character="loop" size={38} /><div><b>Kit</b><span>Thursday launch check-in · 9:00 AM</span></div></div>
                <h3>Two reviews.<br />Two things to unblock.</h3>
                <div className={s.reportItem}><span>01</span><div><b>Ready for Priya</b><p>Landing page and announcement, both at revision 3. Open Launch studio to review the changes.</p></div></div>
                <div className={s.reportItem}><span>02</span><div><b>Demo needs an update</b><p>Three shots still show the old onboarding. The affected frames and revised script are marked.</p></div></div>
                <div className={s.reportItem}><span>03</span><div><b>Permission still missing</b><p>Harbor’s story is drafted. Logo permission is the remaining dependency.</p></div></div>
                <span className={s.scheduleFooter}>EXAMPLE UPDATE · NOTHING PUBLISHED</span>
            </div>
    </WorkExample>;
}

type Layer = { key: string; name: string; says: string; open: React.ReactNode };

const STACK: Layer[] = [
    { key: "data", name: "Tables and files", says: "What it remembers", open: <DataLayer /> },
    { key: "fns", name: "Functions", says: "Always the same steps", open: <FnLayer /> },
    { key: "agents", name: "Agents and workflows", says: "What moves the work", open: <AgentLayer /> },
    { key: "apps", name: "Apps and surfaces", says: "Where people meet it", open: <AppLayer /> },
    { key: "base", name: "Permissions and connectors", says: "The boundary it all sits on", open: <BaseLayer /> },
];

/** A static stack paired with optional, keyboard-accessible detail panels. */

/** Each face wears what that layer actually is, drawn at full size rather than
 *  stamped with a small icon — a record grid, machined repeats of one part,
 *  routed nodes, stacked panes, a lattice. Drawn rather than photographed, so
 *  they stay sharp at any zoom and cost nothing to ship. */
const FACE: Record<string, React.ReactNode> = {
    data: (
        <>
            <rect x="34" y="40" width="232" height="34" rx="6" />
            <rect x="34" y="40" width="232" height="220" rx="6" />
            {[110, 147, 184, 221].map(y => <line key={y} x1="34" y1={y} x2="266" y2={y} />)}
            {[104, 174, 218].map(x => <line key={x} x1={x} y1="40" x2={x} y2="260" />)}
            {[[40, 82], [110, 119], [180, 156], [40, 193], [180, 230]].map(([x, y], i) =>
                <rect key={i} x={x} y={y} width="52" height="10" rx="5" fill="currentColor" stroke="none" />)}
        </>
    ),
    fns: (
        <>
            {[0, 1, 2].flatMap(r => [0, 1, 2].map(c => (
                <g key={`${r}-${c}`} transform={`translate(${44 + c * 76} ${44 + r * 76})`}>
                    <rect x="0" y="0" width="56" height="56" rx="9" />
                    <line x1="12" y1="18" x2="44" y2="18" />
                    <line x1="12" y1="30" x2="36" y2="30" />
                    <circle cx="16" cy="43" r="4" fill="currentColor" stroke="none" />
                </g>
            )))}
            {[0, 1].flatMap(r => [0, 1].map(c => (
                <line key={`h${r}-${c}`} x1={100 + c * 76} y1={72 + r * 76} x2={120 + c * 76} y2={72 + r * 76} />
            )))}
        </>
    ),
    agents: (
        <>
            <path d="M74 86h62v54h66M74 86v92h62v42h66M202 140v-54h30M202 220v-40h30" />
            {[[74, 86], [136, 140], [136, 178], [202, 140], [202, 220], [232, 86], [232, 180]].map(([x, y], i) =>
                <circle key={i} cx={x} cy={y} r="11" style={{ fill: "var(--plate)" }} />)}
            <rect x="46" y="58" width="56" height="56" rx="12" />
            <path d="M172 100l16 10-16 10" />
            <path d="M168 196l16 10-16 10" />
        </>
    ),
    apps: (
        <>
            {[[36, 168], [64, 130], [92, 92]].map(([x, y], i) => (
                <g key={i} transform={`translate(${x} ${y})`}>
                    <rect x="0" y="0" width="172" height="106" rx="10" style={{ fill: "var(--plate)" }} />
                    <line x1="0" y1="24" x2="172" y2="24" />
                    <circle cx="14" cy="12" r="3.5" fill="currentColor" stroke="none" />
                    <circle cx="26" cy="12" r="3.5" fill="currentColor" stroke="none" />
                    <rect x="14" y="40" width="70" height="9" rx="4.5" fill="currentColor" stroke="none" />
                    <rect x="14" y="60" width="104" height="9" rx="4.5" fill="currentColor" stroke="none" />
                    <rect x="124" y="74" width="34" height="18" rx="9" fill="currentColor" stroke="none" />
                </g>
            ))}
        </>
    ),
    base: (
        <>
            {[-140, -70, 0, 70, 140, 210].map(d => (
                <g key={d}>
                    <line x1={60 + d} y1="30" x2={200 + d} y2="270" />
                    <line x1={240 - d} y1="30" x2={100 - d} y2="270" />
                </g>
            ))}
            <rect x="112" y="118" width="76" height="64" rx="10" style={{ fill: "var(--plate)" }} />
            <path d="M134 118v-14a16 16 0 0 1 32 0v14" />
            <circle cx="150" cy="148" r="7" fill="currentColor" stroke="none" />
        </>
    ),
};

/** The same five, small enough to head a card. */
const MARK: Record<string, React.ReactNode> = {
    data: <><rect x="3" y="4" width="18" height="16" rx="2.5" /><path d="M3 9h18M9 9v11" /></>,
    fns: <path d="M9 7l-4 5 4 5M15 7l4 5-4 5" />,
    agents: <><circle cx="6" cy="7" r="2.5" /><circle cx="6" cy="17" r="2.5" /><circle cx="18" cy="12" r="2.5" /><path d="M8.3 8.2l7.4 2.7M8.3 15.8l7.4-2.7" /></>,
    apps: <><rect x="3" y="4" width="18" height="16" rx="2.5" /><path d="M3 9h18" /></>,
    base: <path d="M12 3l8 3.4v5.1c0 4.8-3.3 7.8-8 9.5-4.7-1.7-8-4.7-8-9.5V6.4z" />,
};

export function Behind() {
    const [at, setAt] = useState(-1);

    return (
        <section className={s.section} id="how-it-works">
            <p className={s.sectionEyebrow}>HOW THE WORK FITS TOGETHER</p>
            <h2 className={s.sectionHeading}>The work stays with your teammate.</h2>
            <p className={s.sectionNote}>
                Files and records hold the context. Apps help your team use it. Functions and workflows
                handle repeatable steps, with access and approvals configured for the job.
            </p>

            <div className={s.podLayout}>
                <div className={s.podRail}>
                    <div className={s.podIso}>
                        {/* Inert: everything clickable lives in the flat label
                            overlay, so nothing here joins the depth sort. */}
                        <div className={s.podScene} aria-hidden="true">
                            {STACK.map((layer, index) => (
                                <span
                                    key={layer.key}
                                    className={`${s.plane} ${s[layer.key]} ${index === at ? s.planeOn : ""}`}
                                >
                                    <span className={s.planeFace}>
                                        <svg viewBox="0 0 300 300">{FACE[layer.key]}</svg>
                                    </span>
                                </span>
                            ))}
                        </div>

                        {/* Labels sit outside the 3D scene on purpose. Inside it
                            they are either squashed flat by the same rotation
                            that tilts the planes, or — counter-rotated upright —
                            sliced wherever a plane crosses them. */}
                        <div className={s.podLabels}>
                            {STACK.map((layer, index) => (
                                <button
                                    key={layer.key}
                                    type="button"
                                    className={`${s.podLabel} ${index === at ? s.podLabelOn : ""}`}
                                    aria-expanded={index === at}
                                    aria-controls={`layer-${layer.key}`}
                                    onClick={() => {
                                        setAt(index === at ? -1 : index);
                                        if (index !== at && matchMedia("(max-width: 1180px)").matches) {
                                            requestAnimationFrame(() => {
                                                const detail = document.getElementById(`layer-${layer.key}`)?.parentElement;
                                                detail?.scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
                                            });
                                        }
                                    }}
                                >
                                    <b>{layer.name}</b>
                                    <em>{layer.says}</em>
                                </button>
                            ))}
                        </div>
                    </div>
                </div>

                <div className={s.podCards}>
                    <p className={s.detailHint}>Look inside. Open any layer to see how it works.</p>
                    {STACK.map((layer, index) => (
                        <details
                            key={layer.key}
                            open={index === at}
                            data-layer={layer.key}
                            className={`${s.podCard} ${s[layer.key]} ${index === at ? s.podCardOn : ""}`}
                        >
                            <summary className={s.podCardHead} onClick={event => { event.preventDefault(); setAt(index === at ? -1 : index); }}>
                                <span className={s.podCardMark} aria-hidden="true">
                                    <svg viewBox="0 0 24 24">{MARK[layer.key]}</svg>
                                </span>
                                <div>
                                    <h3>{layer.name}</h3>
                                    <p>{layer.says}</p>
                                </div>
                                <span className={s.detailToggle} aria-hidden="true">{index === at ? "−" : "+"}</span>
                            </summary>
                            <div className={s.layerContent} id={`layer-${layer.key}`}>{layer.open}</div>
                        </details>
                    ))}
                </div>
            </div>
        </section>
    );
}

const SCHEMA = [
    ["id", "uuid", "primary key"],
    ["customer", "relation", "→ customers"],
    ["amount", "money", "indexed"],
    ["status", "enum", "open · drafted · closed"],
    ["created_at", "timestamp", "auto"],
];

function DataLayer() {
    return (
        <div className={s.two}>
            <div className={s.sheet}>
                <span className={s.sheetLabel}>TICKETS · 1,284 ROWS</span>
                <div className={s.schema}>
                    {SCHEMA.map(([col, type, note]) => (
                        <span key={col} className={s.schemaRow}>
                            <b>{col}</b><em>{type}</em><i>{note}</i>
                        </span>
                    ))}
                </div>
                <span className={s.sheetFoot}>
                    ROW-LEVEL SECURITY · THE CLASSIFIER SEES OPEN TICKETS ONLY
                </span>
            </div>
            <div className={s.sheet}>
                <span className={s.sheetLabel}>REFUND-POLICY.MD · MARKDOWN MEMORY</span>
                <div className={s.md}>
                    <p>## Thresholds</p>
                    <p>Refunds above $250 require an approver.</p>
                    <p>Repeat requests inside 30 days escalate to a lead.</p>
                </div>
                <span className={s.sheetText}>
                    Policy lives here — versioned, editable, and read at run time alongside the tables.
                </span>
                <span className={s.sheetFoot}>SEARCHABLE · PERMISSION-SCOPED</span>
            </div>
        </div>
    );
}

function FnLayer() {
    return (
        <div className={s.two}>
            <div className={s.sheet}>
                <span className={s.sheetLabel}>CHECK_POLICY · DETERMINISTIC</span>
                <pre className={s.code}>
{`fn check_policy(amount, customer) {
  policy = read("refund-policy.md")
  if amount > policy.threshold
    return { requiresApproval: true }
}`}
                </pre>
                <span className={s.sheetFoot}>APPLY THE CONFIGURED REFUND POLICY</span>
            </div>
            <div className={s.sheet}>
                <span className={s.sheetLabel}>THE PREDICTABLE HALF</span>
                <div className={s.steps2}>
                    <span className={s.step2}>Plain code, end to end. No model in the loop.</span>
                    <span className={s.step2}>Validators, state changes and outbound actions live here.</span>
                    <span className={s.step2}>Agents call them as tools, so judgment and rules stay separable.</span>
                </div>
                <span className={s.sheetFoot}>THE HALF YOU DO NOT WANT IMPROVISED</span>
            </div>
        </div>
    );
}

const SCOPE = [
    { can: "Can read", what: "tickets · customers · refund-policy.md" },
    { can: "Can write", what: "tickets.status" },
    { can: "Cannot touch", what: "refunds · api_keys · connectors", deny: true },
    { can: "Tools", what: "check_policy · search · table.update" },
];

const FLOW = [
    { step: "classify", kind: "agent", state: "done" },
    { step: "check_policy", kind: "function", state: "done" },
    { step: "approve", kind: "human", state: "wait" },
    { step: "send", kind: "function", state: "idle" },
];

function AgentLayer() {
    return (
        <div className={s.two}>
            <div className={s.sheet}>
                <span className={s.sheetLabel}>CLASSIFIER · AGENT</span>
                <div className={s.scope}>
                    {SCOPE.map(one => (
                        <span key={one.can} className={one.deny ? s.scopeNo : s.scopeRow}>
                            <b>{one.can}</b><em>{one.what}</em>
                        </span>
                    ))}
                </div>
                <span className={s.sheetFoot}>EXAMPLE · CONFIGURED AGENT ACCESS</span>
            </div>
            <div className={s.sheet}>
                <span className={s.sheetLabel}>REFUND-REVIEW · WORKFLOW</span>
                <div className={s.flow}>
                    {FLOW.map(node => (
                        <span
                            key={node.step}
                            className={node.state === "done" ? s.nodeDone : node.state === "wait" ? s.nodeWait : s.nodeIdle}
                        >
                            <b>{node.step}</b><em>{node.kind}</em>
                        </span>
                    ))}
                </div>
                <span className={s.sheetText}>
                    It pauses at <b>approve</b> and resumes on a person&rsquo;s decision — days later,
                    if that is how long it takes.
                </span>
                <span className={s.sheetFoot}>TRIGGERED BY A TABLE EVENT, SCHEDULE, WEBHOOK OR MESSAGE</span>
            </div>
        </div>
    );
}

const SURFACES = [
    { name: "Slack", logo: "/connector-logos/slack.svg" },
    { name: "Teams", logo: "/connector-logos/teams.svg" },
    { name: "Telegram", logo: "/connector-logos/telegram.svg" },
    { name: "WhatsApp", logo: "/connector-logos/whatsapp.svg" },
];

function AppLayer() {
    return (
        <div className={s.two}>
            <div className={s.sheet}>
                <span className={s.sheetLabel}>SUPPORT OPS · EXAMPLE APP</span>
                <div className={s.rows}>
                    <span className={s.rowHead}><b>TICKET</b><b>ACCOUNT</b><b>STATE</b></span>
                    <span className={s.row}><b>tkt_418</b><b>Northfield · $420</b><b className={s.rowWait}>Needs approval</b></span>
                    <span className={s.row}><b>tkt_417</b><b>Halden · $38</b><b>Sent</b></span>
                    <span className={s.row}><b>tkt_416</b><b>Ravel · $120</b><b>Sent</b></span>
                </div>
                <span className={s.sheetFoot}>SUPPORT-OPS.LEMMA.WORK · ONE URL FOR THE TEAM</span>
            </div>
            <div className={s.sheet}>
                <span className={s.sheetLabel}>REACH THE TEAMMATE THROUGH OTHER CHANNELS</span>
                <span className={s.agentRow}>
                    {SURFACES.map(one => (
                        <em key={one.name}>
                            <img src={one.logo} alt="" width={16} height={16} />
                            {one.name}
                        </em>
                    ))}
                    <em>Email</em><em>The API</em>
                </span>
                <span className={s.sheetText}>
                    Apps and agents work with shared records. Connect a channel so your team can
                    ask for updates from the tools they already use.
                </span>
                <span className={s.sheetFoot}>SHARED RECORDS · CONNECTED CHANNELS</span>
            </div>
        </div>
    );
}

const CONNS = [
    { name: "Stripe", who: "billing@acme.co", does: "issue refunds" },
    { name: "Gmail", who: "support@acme.co", does: "read threads · create drafts" },
    { name: "Shopify", who: "ops@acme.co", does: "read orders" },
];

function BaseLayer() {
    return (
        <div className={s.two}>
            <div className={s.sheet}>
                <span className={s.sheetLabel}>CONNECTORS · CONNECTED ACCOUNTS</span>
                <div className={s.rows}>
                    {CONNS.map(one => (
                        <span key={one.name} className={s.row}>
                            <b>{one.name}</b><b>{one.who}</b><b>{one.does}</b>
                        </span>
                    ))}
                </div>
                <span className={s.sheetText}>
                    Connect the accounts your teammate needs for the job. Review the account and
                    permissions before authorizing access.
                </span>
                <span className={s.sheetFoot}>CHOOSE WHICH ACCOUNTS TO CONNECT</span>
            </div>
            <div className={s.sheet}>
                <span className={s.sheetLabel}>ACCESS AND APPROVALS</span>
                <div className={s.steps2}>
                    <span className={s.step2}>Choose who can work with your teammate.</span>
                    <span className={s.step2}>A consequential step can require a named approver.</span>
                    <span className={s.step2}>Review access when you add tools or connect accounts.</span>
                </div>
                <span className={s.sheetFoot}>SET THE BOUNDARIES FOR THE JOB</span>
            </div>
        </div>
    );
}

const COLUMNS = [
    { name: "Northfield", open: true },
    { name: "Vale", open: true },
    { name: "Kite", open: true },
    { name: "Morrow", open: false },
    { name: "Brightpath", open: false },
];

const REACH_ROWS: { mark: string; name: string; role: string; verb: string; openOnly?: boolean; character?: CharacterName }[] = [
    { mark: "P", name: "Priya", role: "OWNER", verb: "Approve" },
    { mark: "D", name: "Dev", role: "EDITOR", verb: "Edit" },
    { mark: "S", name: "Sam", role: "VIEWER", verb: "Read" },
    { mark: "", name: "Support", role: "TEAMMATE", verb: "Draft", openOnly: true, character: "gem" },
];

export function Access() {
    return (
        <section className={s.section}>
            <p className={s.sectionEyebrow}>CONFIGURE ACCESS FOR THE JOB</p>
            <h2 className={s.sectionHeading}>Choose who can see and change the work.</h2>
            <p className={s.sectionNote}>
                In this example, the support agent can draft replies for open refunds.
                People review the requests according to their assigned roles.
            </p>

            <div className={s.chart}>
                <span className={s.chartHead}>
                    <span className={s.chartWho}>REFUNDS · 5 OF 88 ROWS</span>
                    {COLUMNS.map(column => (
                        <span key={column.name} className={column.open ? s.colName : s.colShut}>
                            {column.name}
                        </span>
                    ))}
                    <span className={s.chartVerb} />
                </span>

                {REACH_ROWS.map(one => (
                    <span key={one.name} className={s.chartRow}>
                        <span className={s.chartWho}>
                            {one.character
                                ? <i className={s.chartFace}><Character character={one.character} size={26} /></i>
                                : <i>{one.mark}</i>}
                            {one.name}
                            <em>{one.role}</em>
                        </span>
                        {COLUMNS.map(column => {
                            const reaches = !one.openOnly || column.open;
                            return <span key={column.name} className={reaches ? s.cell : s.cellGone}
                                aria-label={`${one.name}: ${column.name}, ${reaches ? one.verb.toLowerCase() : "no access"}`}>
                                <span aria-hidden="true">{reaches ? "✓" : "—"}</span>
                            </span>;
                        })}
                        <span className={s.chartVerb}><em>{one.verb}</em></span>
                    </span>
                ))}
            </div>

            <div className={s.chartLegend}><span><i className={s.cell} />Within assigned access</span><span><i className={s.cellGone} />Outside assigned access</span></div>

            <div className={s.mobileAccess} aria-label="Example access by member">
                {REACH_ROWS.map(one => (
                    <article key={one.name}>
                        <div><b>{one.name}</b><span>{one.role}</span><em>{one.verb}</em></div>
                        <p>{one.openOnly ? "Open refunds: Northfield, Vale and Kite." : "All five refunds: Northfield, Vale, Kite, Morrow and Brightpath."}</p>
                        {one.openOnly && <small>Closed refunds are outside this example’s access.</small>}
                    </article>
                ))}
            </div>

            <div className={s.chartNotes}>
                <span><b>Shopify</b> is the connected account in this example. Choose the accounts your own workflow needs.</span>
                <span><b>Approval</b> is a configured step in this example, before a refund is issued.</span>
            </div>
        </section>
    );
}

/* ── What it thinks with ────────────────────────────────────────────────
   Three ways, and the honest difference between them. `src/data/runtimes.ts`
   already draws the first two: a paired computer is a machine that can be
   asleep, and a bought key is a bill that never is. The third is ours. */

const AGENTS = [
    { name: "Claude Code", logo: "/agent-logos/claudecode.png" },
    { name: "Codex", logo: "/agent-logos/codex.png" },
    { name: "Cursor", logo: "/agent-logos/cursor.png" },
    { name: "OpenCode", logo: "/agent-logos/opencode.png" },
];

export function Thinks() {
    return (
        <section className={s.section}>
            <p className={s.sectionEyebrow}>HOW YOUR TEAMMATE RUNS</p>
            <h2 className={s.sectionHeading}>Choose how your teammate runs.</h2>
            <p className={s.sectionNote}>
                Choose a hosted model, connect a provider key, or pair a computer running a coding
                agent. Your teammate&rsquo;s files and records stay in Lemma when you change its runtime.
            </p>

            <div className={s.runtimes}>
                <div className={s.flat}>
                    <span className={s.sheetLabel}>YOUR COMPUTER</span>
                    <p className={s.sheetText}>
                        The coding agent already on your machine, running as this teammate.
                    </p>
                    <span className={s.agentRow}>
                        {AGENTS.map(agent => (
                            <em key={agent.name}>
                                <img src={agent.logo} alt="" width={17} height={17} />
                                {agent.name}
                            </em>
                        ))}
                    </span>
                    <span className={s.sheetFoot}>THE COMPUTER NEEDS TO BE ONLINE</span>
                </div>

                <div className={s.flat}>
                    <span className={s.sheetLabel}>YOUR PROVIDER KEY</span>
                    <p className={s.sheetText}>
                        Connect a provider account for your organization. Usage is billed by that provider.
                    </p>
                    <span className={s.agentRow}>
                        <em>Your API key</em><em>Your choice of model</em>
                    </span>
                    <span className={s.sheetFoot}>YOUR PROVIDER&rsquo;S MODELS AND USAGE LIMITS</span>
                </div>

                <div className={s.flatOn}>
                    <span className={s.sheetLabel}>LEMMA HOSTED</span>
                    <p className={s.sheetText}>
                        Use Lemma-hosted models without connecting your own computer or API key.
                        Your account&rsquo;s usage limits apply.
                    </p>
                    <span className={s.agentRow}>
                        <em>Managed by Lemma</em><em>View usage in Settings</em>
                    </span>
                    <span className={s.sheetFoot}>NO MACHINE OF YOUR OWN TO KEEP AWAKE</span>
                </div>
            </div>
        </section>
    );
}

/* ── The close ─────────────────────────────────────────────────────────── */

export function Closing() {
    return (
        <section className={s.closingField}>
            <h2 className={s.closingHeading}>What would you hand over first?</h2>
            <div className={s.closingActions}>
                <Link className={s.primaryOn} href="/t">Get started</Link>
                <a className={s.secondaryOn} href="https://github.com/lemma-work" target="_blank" rel="noreferrer">
                    View on GitHub
                </a>
            </div>
            <div className={s.lineup} aria-hidden="true">
                <Greeter character="sprout" size={124} />
                <Greeter character="gem" size={124} />
                <Greeter character="moon" size={124} />
            </div>
        </section>
    );
}
