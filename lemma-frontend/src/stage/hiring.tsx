import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import type { Pod } from "@/data";
import { HIRES, BLANK, openersFor, profileFor, type Hire } from "@/data/hires";
import { ASKS, typedAt } from "./asking";
import { PlusIcon, ArrowRightIcon, BackIcon, CheckIcon, ChatIcon, LinkIcon, PeopleIcon } from "@/ui/icons";
import { characterForSeed, variantForCharacter } from "@/shell/character";
import { CharacterPuppet } from "@/shell/character-puppet";
import { formatIdentityIcon, identityVariantSeed } from "@/shell/resource-icon";
import { identityGenes } from "@/identity/seeded-identity";
import { pressSlot } from "@/identity/palette";
import { Modal } from "@/shell/modal";
import { ProfileView } from "./profile";

/** One candidate.
 *
 *  Its own component for its own hover: six cards sharing one piece of state
 *  meant every card re-rendered whenever the pointer crossed any of them, and
 *  each of these now carries a rig.
 *
 *  Hover is two things, not one. The wave is a greeting — it fires once on
 *  arrival and settles, because a character that waves for as long as you
 *  hold the pointer is a metronome, not a hello. What lasts is the mood:
 *  `delighted` keeps the eyes smiling and the stance open the whole time you
 *  are there, and drops back to idle when you leave. Focus counts as arrival
 *  too, so this reads the same from the keyboard. */
function HireCard({ hire, onPick }: { hire: Hire; onPick: (hire: Hire) => void }) {
    const [here, setHere] = useState(false);
    const [hello, setHello] = useState(0);
    const arrive = () => { if (!here) { setHere(true); setHello(count => count + 1); } };
    const leave = () => setHere(false);
    return (
        <button
            className="hirecard"
            onClick={() => onPick(hire)}
            onPointerEnter={arrive}
            onPointerLeave={leave}
            onFocus={arrive}
            onBlur={leave}
        >
            <span className="hirecard__face">
                <CharacterPuppet
                    character={characterForSeed(hire.seed)}
                    size={52}
                    greeting={hello}
                    mood={here ? "delighted" : "idle"}
                />
            </span>
            <span className="hirecard__name">{hire.name}</span>
            <span className="hirecard__role">{hire.role}</span>
            <span className="hirecard__brings">
                <span>Set up together:</span>
                {hire.brings.map((thing) => <span key={thing}>{thing}</span>)}
            </span>
            <span className="hirecard__go">Explore this role <ArrowRightIcon size={14} /></span>
        </button>
    );
}

/** The hiring floor.
 *
 *  A page, not a dialog. Six candidates already overflowed a modal and the
 *  seventh would have scrolled; more to the point, a candidate deserves the
 *  *same page* a hired teammate gets — so clicking one opens a profile, not a
 *  summary. `ProfileView` draws both, which turns "what you see before you
 *  hire is what you get after" from a promise into a structural fact.
 *
 *  Four beats: the shelf, one candidate's profile, the making, and the
 *  meeting. The listings come from `HIRES`, a placeholder for published pod
 *  bundles; when those land only that file changes and `hire()` gains an
 *  import beside its create. */

/** What the reveal asked the app to do after it lands on the new teammate.
 *
 *  `say` fills the composer and stops — it does not send, because a message
 *  in a thread carries the name of whoever is standing there, and one the app
 *  wrote is one they did not. `open` raises the dialog that belongs to the
 *  pod, which the reveal cannot raise itself: both of them read surfaces and
 *  members off a `Pod` the shell holds and this page never sees. */
export type FirstMove = { say: string } | { open: "reach" | "people" };

type Stage = "shelf" | "candidate" | "making" | "met";

/** Somebody new is a name, a line and a face. Nothing else, because
 *  everything else is the part they build with you — and a profile page of
 *  empty sections ("no tools granted", "nothing scheduled") is a list of
 *  things they lack, which is a strange thing to read about someone you are
 *  about to hire.
 *
 *  A described job is not a name, either. "handle our marketing efforts" as a
 *  title gives you a colleague called Handle our marketing; the sentence is
 *  what they are *for*, and it belongs in the description where it was going
 *  to end up anyway. */

export function HiringView({
    orgId,
    orgName,
    onHired,
    onClose,
    initialJob,
}: {
    orgId: string;
    orgName: string;
    onHired: (podId: string, move?: FirstMove) => void;
    onClose: () => void;
    initialJob?: { name: string; job: string };
}) {
    const [stage, setStage] = useState<Stage>("shelf");
    /* Describing a job is a dialog over the shelf, not a page. It is two
       fields and a button — a page-shaped container around that left it
       stranded in the middle of an empty pane, and it cost the reader the
       floor they were standing on. A dialog keeps the candidates behind it
       and makes Escape mean what it looks like it means. */
    const [describing, setDescribing] = useState(Boolean(initialJob));
    const [picked, setPicked] = useState<Hire | null>(initialJob ? BLANK : null);
    const [name, setName] = useState(initialJob?.name ?? "");
    const [job, setJob] = useState(initialJob?.job ?? "");
    const [done, setDone] = useState<string[]>([]);
    const [made, setMade] = useState<{ id: string; name: string; variant: number } | null>(null);
    const [error, setError] = useState<string | null>(null);
    const queryClient = useQueryClient();

    function consider(hire: Hire) {
        setPicked(hire);
        setName(hire.name);
        setError(null);
        if (hire.id === "blank") setDescribing(true);
        else setStage("candidate");
    }

    async function hire() {
        if (!picked) return;
        const clean = name.trim();
        if (!clean) return;
        setDescribing(false);
        setStage("making");
        setDone([]);
        setError(null);
        try {
            /* Only the steps that really happen are listed. A published bundle
               will contribute its own — a table, a schedule, an app — and they
               belong in this list rather than behind a progress bar with
               nothing underneath it. */
            const about = picked.id === "blank" ? job.trim() : picked.about;
            const pod = await source.createPod(orgId, clean, about);
            setDone(["made"]);

            /* The archetype fixes which character; this pod's own id supplies
               the seed, and the variant landing on that character is what gets
               stored — so the face you picked is the face it keeps. */
            const variant = variantForCharacter(pod.id, characterForSeed(picked.seed)) ?? 0;
            if (variant !== 0) await source.setPodIcon(pod.id, formatIdentityIcon(variant));
            setDone(["made", "face"]);

            /* Refetch rather than invalidate, and do not wait on it. Awaiting
               held the reveal behind a list nobody is looking at yet; and
               invalidation only marks a query stale, which a list already
               holding fresh-looking data can sit on. */
            /* Put them in the rail directly rather than asking the list to go
               and look again. The new teammate is already in hand — a whole
               `Pod` — so writing it in is instant and cannot miss, where a
               refetch has to match a key, beat a stale time, and win a race
               with the reveal. The background refetch still runs, to pick up
               anything the server decided that this does not know. */
            queryClient.setQueryData(["pods", orgId], (old: Pod[] | undefined) =>
                old ? (old.some((entry) => entry.id === pod.id) ? old : [...old, pod]) : old,
            );
            void queryClient.refetchQueries({ queryKey: ["pods"] });
            setMade({ id: pod.id, name: pod.name, variant });
            setStage("met");
        } catch (problem) {
            setError(problem instanceof Error ? problem.message : "Couldn’t finish setting up this teammate. Check your teammate list before trying again.");
            if (picked.id === "blank") { setDescribing(true); setStage("shelf"); }
            else setStage("candidate");
        }
    }

    return (
        <div className="hiring">
            <header className="hiring__bar">
                <button className="hiring__back" onClick={stage === "candidate" ? () => setStage("shelf") : onClose}>
                    <BackIcon size={16} />
                    {stage === "candidate" ? "All candidates" : "Back to work"}
                </button>
                <span className="hiring__where">
                    {stage === "shelf" ? "Hiring for " + orgName : picked ? picked.name || "Somebody new" : ""}
                </span>
            </header>

            {(stage === "shelf" || describing) && <Shelf job={job} onJob={setJob} onPick={consider} />}

            {describing && (
                <NewFace
                    name={name}
                    onName={setName}
                    job={job}
                    onJob={setJob}
                    error={error}
                    onClose={() => { setDescribing(false); setStage("shelf"); }}
                    onHire={() => void hire()}
                />
            )}

            {stage === "candidate" && picked && picked.id !== "blank" && (
                <Candidate
                    hire={picked}
                    orgName={orgName}
                    name={name}
                    onName={setName}
                    error={error}
                    onHire={() => void hire()}
                />
            )}

            {stage === "making" && picked && <Making hire={picked} name={name.trim()} done={done} />}

            {stage === "met" && made && picked && (
                <Met
                    podId={made.id}
                    name={made.name}
                    variant={made.variant}
                    hire={picked}
                    job={job}
                    orgName={orgName}
                    onOpen={(move) => onHired(made.id, move)}
                />
            )}
        </div>
    );
}

/** The box, saying a few of the things it will take.
 *
 *  "I need someone to…" is an invitation with no sense of scale, and six cards
 *  sitting under it quietly suggest the answer has to be one of six. So the
 *  placeholder types out jobs that are not on the shelf.
 *
 *  It is not autoplay decoration, which `DESIGN.md` rules out and is right to:
 *  this is the field telling you what it accepts, on the one screen where
 *  nobody knows. It still holds to the rest of that rule — it stops the moment
 *  the box is somebody's, and it never runs at all where the reader has asked
 *  for less motion, which is the case the resting placeholder is for.
 *
 *  A placeholder attribute rather than text laid over the input: no second
 *  element to keep in register with a caret, no `aria-hidden` to get wrong,
 *  and the field's accessible name comes from its label either way. */
function useAsking(resting: boolean): string | null {
    const [elapsed, setElapsed] = useState(0);
    const [wanted, setWanted] = useState(false);

    useEffect(() => {
        const reduced = matchMedia("(prefers-reduced-motion: reduce)");
        const read = () => setWanted(!reduced.matches);
        read();
        reduced.addEventListener("change", read);
        return () => reduced.removeEventListener("change", read);
    }, []);

    useEffect(() => {
        if (!wanted || !resting) return;
        /* Restarted from zero each time the box goes back to resting, so
           somebody who clicked in and back out gets a phrase from its
           beginning rather than joining one halfway through an erase. */
        const from = Date.now();
        const timer = window.setInterval(() => setElapsed(Date.now() - from), 40);
        return () => window.clearInterval(timer);
    }, [wanted, resting]);

    if (!wanted || !resting) return null;
    return typedAt(ASKS, elapsed) || null;
}

/** The shelf. The question comes first and the cast answers it — the free
 *  text sits above the grid, because somebody who already knows what they
 *  want should not read six cards to learn none of them is it. */
function Shelf({
    job,
    onJob,
    onPick,
}: {
    job: string;
    onJob: (value: string) => void;
    onPick: (hire: Hire, suggested?: string) => void;
}) {
    const input = useRef<HTMLInputElement>(null);
    useEffect(() => { input.current?.focus(); }, []);
    /* The field is focused on arrival, so "resting" cannot mean unfocused —
       that would be a box nobody ever sees suggesting anything. It rests until
       there is something in it, and stops for good once there is. */
    const [touched, setTouched] = useState(false);
    const asking = useAsking(!touched && job.length === 0);

    return (
        <div className="pane">
            <div className="shelf">
                <div className="shelf__head">
                    <h1>What needs doing?</h1>
                    <p>Give a teammate its first responsibility. Choose a starting role or describe the job yourself.</p>
                </div>

                <form
                    className="shelf__ask"
                    onSubmit={(event) => {
                        event.preventDefault();
                        if (job.trim()) onPick(BLANK);
                    }}
                >
                    <input
                        ref={input}
                        value={job}
                        placeholder={asking ? "I need someone to " + asking : "I need someone to…"}
                        aria-label="Describe the job you need doing"
                        onChange={(event) => { setTouched(true); onJob(event.target.value); }}
                        onKeyDown={(event) => {
                            if (event.key !== "Enter" || !job.trim()) return;
                            event.preventDefault();
                            onPick(BLANK);
                        }}
                    />
                    <button className="btn btn--primary" type="submit" disabled={!job.trim()}>
                        Continue <ArrowRightIcon size={15} />
                    </button>
                </form>

                <div className="shelf__or">or start with a role</div>

                <div className="shelf__grid">
                    {HIRES.map((hire) => <HireCard key={hire.id} hire={hire} onPick={onPick} />)}

                    <button className="hirecard hirecard--blank" onClick={() => onPick(BLANK)}>
                        <span className="hirecard__face"><PlusIcon size={24} /></span>
                        <span className="hirecard__name">Somebody new</span>
                        <span className="hirecard__role">{BLANK.role}</span>
                        <span className="hirecard__brings">
                            <span>Give them a name and a first responsibility</span>
                        </span>
                        <span className="hirecard__go">Start your own <ArrowRightIcon size={14} /></span>
                    </button>
                </div>
            </div>
        </div>
    );
}

/** Somebody new: a face, a name, a line, in a dialog over the shelf.
 *
 *  The face drifts because it is not theirs yet — a teammate's creature comes
 *  from its pod id, and the id does not exist until they do, so anything
 *  settled here would be a promise this screen cannot keep. It stops drifting
 *  at the reveal, and the note under the button says so rather than leaving a
 *  grey placeholder to be misread as an image that failed to load. */
function NewFace({
    name,
    onName,
    job,
    onJob,
    error,
    onClose,
    onHire,
}: {
    name: string;
    onName: (value: string) => void;
    job: string;
    onJob: (value: string) => void;
    error: string | null;
    onClose: () => void;
    onHire: () => void;
}) {
    const seeds = useRef(Array.from({ length: 16 }, (_, index) => "waiting/" + Date.now().toString(36) + "/" + index));
    const [at, setAt] = useState(0);
    const field = useRef<HTMLInputElement>(null);

    useEffect(() => {
        field.current?.focus();
        const timer = window.setInterval(() => setAt((was) => was + 1), 2400);
        return () => window.clearInterval(timer);
    }, []);

    return (
        <Modal
            narrow
            title="Somebody new"
            subtitle="Start with a name and a responsibility. Teach them your business as you work together."
            onClose={onClose}
        >
            <div className="newface">
                <span className="newface__face">
                    <CharacterPuppet key={at} character={characterForSeed(seeds.current[at % seeds.current.length])} size={76} />
                </span>

                <label className="takeon__name">
                    <span>Their name</span>
                    <input
                        ref={field}
                        value={name}
                        placeholder="Ops, Nikhil, night shift…"
                        onChange={(event) => onName(event.target.value)}
                        onKeyDown={(event) => { if (event.key === "Enter") onHire(); }}
                    />
                </label>

                <label className="takeon__name">
                    <span>The job, if you know it yet</span>
                    <input
                        value={job}
                        placeholder="Watches the inbox and answers what it can"
                        onChange={(event) => onJob(event.target.value)}
                        onKeyDown={(event) => { if (event.key === "Enter") onHire(); }}
                    />
                </label>

                {error && <p className="hire__error">{error}</p>}

                <div className="newface__foot">
                    <button className="btn btn--primary" onClick={onHire} disabled={!name.trim()}>
                        Hire {name.trim() || "them"}
                    </button>
                    <span className="newface__note">You will meet them in a moment.</span>
                </div>
            </div>
        </Modal>
    );
}

/** One candidate, on the page they will keep. The hero's action is the only
 *  thing that differs from a hired teammate's page: Hire instead of Message. */
function Candidate({
    hire,
    orgName,
    name,
    onName,
    error,
    onHire,
}: {
    hire: Hire;
    orgName: string;
    name: string;
    onName: (value: string) => void;
    error: string | null;
    onHire: () => void;
}) {
    const me = useMemo(() => profileFor(hire), [hire]);

    return (
        <ProfileView
            subject={{
                seed: hire.seed,
                name: name.trim() || hire.name || "Somebody new",
                iconUrl: null,
                orgName,
                me,
                reach: [],
                members: [],
                issued: false,
                emptyVoice: "candidate",
                action: (
                    <div className="takeon">
                        <label className="takeon__name">
                            <span>Their name</span>
                            <input
                                value={name}
                                placeholder={hire.name}
                                onChange={(event) => onName(event.target.value)}
                                onKeyDown={(event) => { if (event.key === "Enter") onHire(); }}
                            />
                        </label>
                        <button className="btn btn--primary takeon__go" onClick={onHire} disabled={!name.trim()}>
                            Hire {name.trim() || "them"}
                        </button>
                        {error && <p className="hire__error">{error}</p>}
                    </div>
                ),
                aside: (
                    <section className="pcard">
                        <div className="pcard__head"><h3>What you can set up together</h3></div>
                        <p className="empty-row">This role is a starting brief. Apps, sources and schedules are set up after hiring.</p>
                        <ul className="offer__brings">
                            {hire.brings.map((thing) => (
                                <li key={thing}><ArrowRightIcon size={15} />{thing}</li>
                            ))}
                        </ul>
                    </section>
                ),
            }}
        />
    );
}

/** The wait, said as what it is. Two steps today because two things really
 *  happen; a bundle import will add its own. */
function Making({ hire, name, done }: { hire: Hire; name: string; done: string[] }) {
    const steps = useMemo(
        () => [
            { id: "made", label: "Setting up " + (name || "them") },
            { id: "face", label: "Creating their identity" },
        ],
        [name],
    );

    return (
        <div className="pane"><div className="making">
            <span className="making__face"><CharacterPuppet character={characterForSeed(hire.seed)} size={92} /></span>
            <ul className="making__steps">
                {steps.map((step, index) => {
                    const finished = done.includes(step.id);
                    const running = !finished && done.length === index;
                    return (
                        <li key={step.id} data-state={finished ? "done" : running ? "running" : "waiting"}>
                            <span className="making__dot">{finished ? <CheckIcon size={13} /> : null}</span>
                            {step.label}
                        </li>
                    );
                })}
            </ul>
        </div></div>
    );
}

/** The reveal.
 *
 *  A face, a name and a door — with the door as the only thing on it you can
 *  do — makes the best moment in the flow a corridor: you meet somebody, and
 *  then you are alone with them on an empty thread that says "Nothing said in
 *  here yet" and no more.
 *
 *  So the page keeps the moment and adds the morning. The three things under
 *  the face are the three that make a new teammate real — something to do,
 *  somewhere to be written to, and somebody else who can use them — and each
 *  one is an existing surface rather than a checklist item that ticks itself.
 *
 *  It is printed in the teammate's own field, the same colour their badge and
 *  their header band will be, which is the first time anyone sees it: the
 *  hiring floor runs on the app's accent because it does not belong to anyone
 *  yet, and from here on this pod does.
 *
 *  The face waves on arrival and holds `delighted` for as long as the page is
 *  up, because this is the one screen where that is the whole point, and waves
 *  again when you come back to it with a pointer or the keyboard. */
function Met({
    podId,
    name,
    variant,
    hire,
    job,
    orgName,
    onOpen,
}: {
    podId: string;
    name: string;
    variant: number;
    hire: Hire;
    job: string;
    orgName: string;
    onOpen: (move?: FirstMove) => void;
}) {
    const [hello, setHello] = useState(1);
    const openers = useMemo(() => openersFor(hire, job), [hire, job]);
    const tone = useMemo(() => pressSlot(identityGenes(podId).tone), [podId]);
    const wave = () => setHello((count) => count + 1);

    return (
        <div className="pane"><div
            className="met"
            style={{ ["--field" as string]: tone.field, ["--field-ink" as string]: tone.ink }}
        >
            <span
                className="met__face"
                onPointerEnter={wave}
                /* A greeting is worth having from the keyboard too, and the
                   face is not a control — so the wrapper takes focus only as
                   far as a pointer already reaches, and announces nothing. */
                onFocus={wave}
                tabIndex={-1}
            >
                <CharacterPuppet
                    character={characterForSeed(identityVariantSeed(podId, variant))}
                    size={200}
                    greeting={hello}
                    mood="delighted"
                />
            </span>
            <h2 className="met__name">Meet {name}</h2>
            <p className="met__line">
                {hire.id === "blank"
                    ? name + " is ready to learn your work. Share the context for their first task."
                    : name + " is ready. Start with a task, then work together to set up the sources, apps and schedules for the job."}
            </p>

            <div className="met__moves">
                <section className="met__move">
                    <h3><ChatIcon size={17} />Say the first thing</h3>
                    {openers.length > 0 ? (
                        <>
                            <p>Each one opens as a draft you can change before sending.</p>
                            <ul className="met__openers">
                                {openers.map((line) => (
                                    <li key={line}>
                                        <button onClick={() => onOpen({ say: line })}>
                                            <span>{line}</span>
                                            <ArrowRightIcon size={15} />
                                        </button>
                                    </li>
                                ))}
                            </ul>
                        </>
                    ) : (
                        <p>Whatever you were going to hand them first. {name} has no history to catch up on.</p>
                    )}
                </section>

                <div className="met__pair">
                    <button className="met__side" onClick={() => onOpen({ open: "reach" })}>
                        <LinkIcon size={18} />
                        <b>Give them a way to be reached</b>
                        <span>Message {name} here in Lemma, or connect email, Slack or WhatsApp.</span>
                    </button>
                    <button className="met__side" onClick={() => onOpen({ open: "people" })}>
                        <PeopleIcon size={18} />
                        <b>Bring somebody in</b>
                        <span>Invite people from {orgName} to work with {name}.</span>
                    </button>
                </div>
            </div>

            {/* The door is still here, and still last. It stops being the
                primary only when there is something better to press. */}
            <button
                className={openers.length > 0 ? "btn met__door" : "btn btn--primary met__door"}
                onClick={() => onOpen()}
            >
                {openers.length > 0 ? "Or just open " + name : "Open " + name}
            </button>
        </div></div>
    );
}
