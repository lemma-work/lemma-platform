import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import type { Pod } from "@/data";
import { BLANK, blankHire, dealtName, openersFor, profileFor, type Hire } from "@/data/hires";
import { hireFromCard } from "@/data/roles";
import { possessive } from "@/copy";
import { ASKS, typedAt } from "./asking";
import { ArrowRightIcon, BackIcon, CheckIcon, ChatIcon, ClockIcon, KeyIcon, LinkIcon, PeopleIcon } from "@/ui/icons";
import { characterForSeed, variantForCharacter } from "@/shell/character";
import { CharacterPuppet } from "@/shell/character-puppet";
import { formatIdentityIcon, identityVariantSeed } from "@/shell/resource-icon";
import { identityGenes } from "@/identity/seeded-identity";
import { pressSlot } from "@/identity/palette";
import { Modal } from "@/shell/modal";
import { ProfileView } from "./profile";
import { makeTeammate } from "./hiring-steps";
import { modelSetupState } from "@/shell/runs-on-state";
import { SetUpAiModelLink } from "@/desktop/set-up-on-this-mac";
import { useThisMacAvailability } from "@/desktop/this-mac-settings";
import { openSettings } from "@/desktop/open-settings";
import { lemma } from "@/session/client";
import { importTemplate, type Call } from "./template-import";
import { useCreateSchedule } from "@/schedule/queries";
import { humanizeName } from "@/schedule/schedules";

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
 *  meeting. The listings are the templates the server ships, each read with
 *  its card (`data/roles.ts`); hiring one makes the pod and then imports its
 *  template into it. */

/** What the reveal asked the app to do after it lands on the new teammate.
 *
 *  `say` fills the composer and stops — it does not send, because a message
 *  in a thread carries the name of whoever is standing there, and one the app
 *  wrote is one they did not. `open` raises the dialog that belongs to the
 *  pod, which the reveal cannot raise itself: both of them read surfaces and
 *  members off a `Pod` the shell holds and this page never sees. */
export type FirstMove = { say: string } | { open: "reach" | "people" | "scorecard" };

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
    const [picked, setPicked] = useState<Hire | null>(() => (initialJob ? blankHire() : null));
    const [name, setName] = useState(initialJob?.name ?? "");
    const [job, setJob] = useState(initialJob?.job ?? "");
    const [done, setDone] = useState<string[]>([]);
    const [made, setMade] = useState<{ id: string; name: string; variant: number; faceSaved: boolean; templateProblem: string | null } | null>(null);
    const [error, setError] = useState<string | null>(null);
    /* The pod an attempt at this hire already created. Kept across a retry so
       pressing Hire again finishes that teammate instead of making a second
       one; cleared when somebody else is picked. */
    const created = useRef<Pod | null>(null);
    const queryClient = useQueryClient();

    /* Whether a new teammate here would have a model to answer with. Same
       queries, and the same reading, as the "runs on" picker — a new pod
       follows the organization default, so it has no choice of its own. */
    const runtimes = useQuery({ queryKey: ["runtimes", orgId], queryFn: () => source.listRuntimes(orgId), staleTime: 5 * 60_000 });
    const inherited = useQuery({ queryKey: ["default-runtime", orgId], queryFn: () => source.defaultRuntime(orgId), staleTime: 5 * 60_000 });
    const needsModel = runtimes.isSuccess && inherited.isSuccess
        ? modelSetupState(runtimes.data.filter((one) => !one.archived), inherited.data, null) !== null
        : false;

    function consider(hire: Hire) {
        created.current = null;
        setPicked(hire.id === "blank" ? blankHire() : hire);
        setName(hire.name);
        setError(null);
        if (hire.id === "blank") setDescribing(true);
        else setStage("candidate");
    }

    /* Just exploring: no name to think of and no job to describe. The face is
       dealt, the name comes with it, and the hire happens on the click —
       state is set for the reveal, but the hire reads its own arguments
       because a setter has not landed by the time the next line runs. */
    function explore() {
        const somebody = blankHire();
        const dealt = dealtName(somebody);
        created.current = null;
        setPicked(somebody);
        setName(dealt);
        setJob("");
        setError(null);
        void hire({ hire: somebody, name: dealt, job: "" });
    }

    async function hire(now?: { hire: Hire; name: string; job: string }) {
        const chosen = now?.hire ?? picked;
        if (!chosen) return;
        const clean = (now?.name ?? name).trim();
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
            const about = chosen.id === "blank" ? (now?.job ?? job).trim() : chosen.about;
            const character = characterForSeed(chosen.seed);
            /* The archetype fixes which character; this pod's own id supplies
               the seed, and the variant landing on that character is what gets
               stored — so the face you picked is the face it keeps. */
            const { pod, variant, faceSaved } = await makeTeammate({
                existing: created.current,
                create: () => source.createPod(orgId, clean, about),
                onCreated: (fresh) => { created.current = fresh; setDone(["made"]); },
                variantFor: (podId) => variantForCharacter(podId, character) ?? 0,
                saveFace: (podId, face) => source.setPodIcon(podId, formatIdentityIcon(face)),
            });
            setDone(["made", "face"]);

            /* The role's template: its skills, its tables and its scorecard.
               A failure here does not undo the hire — the teammate exists and
               works — so the reveal says what did not arrive instead of
               sending the person back to a Hire button for a teammate they
               already have. */
            let templateProblem: string | null = null;
            /* The sample workspace has no server to import into. */
            if (chosen.bundle && source.label !== "sample") {
                const call = ((method, path, body) => lemma(pod.id).request(method, path, body ? { body } : undefined)) as Call;
                try {
                    await importTemplate({ podId: pod.id, template: chosen.bundle, call });
                    setDone(["made", "face", "template"]);
                } catch (problem) {
                    templateProblem = problem instanceof Error ? problem.message : "The role’s setup stopped before it finished.";
                }
            }

            /* Put them in the rail directly rather than asking the list to go
               and look again. The new teammate is already in hand — a whole
               `Pod` — so writing it in is instant and cannot miss, where a
               refetch has to match a key, beat a stale time, and win a race
               with the reveal. The background refetch still runs, not awaited,
               to pick up anything the server decided that this does not know —
               for this organization's list only. The pod was made in it; no
               other organization's cached list changed, and refetching them
               all was a request per organization ever opened. */
            /* With the face it was just given: `createPod` answered before the
               face was saved, so the pod in hand still has no picture, and the
               rail drew the id's default face until the refetch landed. */
            const faced: Pod = variant > 0 ? { ...pod, iconUrl: formatIdentityIcon(variant) } : pod;
            queryClient.setQueryData(["pods", orgId], (old: Pod[] | undefined) =>
                old ? (old.some((entry) => entry.id === pod.id) ? old : [...old, faced]) : old,
            );
            void queryClient.invalidateQueries({ queryKey: ["pods", orgId] });
            created.current = null;
            setMade({ id: pod.id, name: pod.name, variant, faceSaved, templateProblem });
            setStage("met");
        } catch (problem) {
            setError(problem instanceof Error ? problem.message : "Couldn’t finish setting up this teammate. Check your teammate list before trying again.");
            if (chosen.id === "blank") { setDescribing(true); setStage("shelf"); }
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
                    {stage === "shelf"
                        ? "Hiring for " + orgName
                        : picked
                            ? (stage === "candidate" ? picked.name : name.trim()) || "Somebody new"
                            : ""}
                </span>
            </header>

            {(stage === "shelf" || describing) && <Shelf job={job} onJob={setJob} onPick={consider} onExplore={explore} />}

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
                    needsModel={needsModel}
                    faceSaved={made.faceSaved}
                    templateProblem={made.templateProblem}
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
    onExplore,
}: {
    job: string;
    onJob: (value: string) => void;
    onPick: (hire: Hire, suggested?: string) => void;
    onExplore: () => void;
}) {
    const input = useRef<HTMLInputElement>(null);
    useEffect(() => { input.current?.focus(); }, []);
    /* The field is focused on arrival, so "resting" cannot mean unfocused —
       that would be a box nobody ever sees suggesting anything. It rests until
       there is something in it, and stops for good once there is. */
    const [touched, setTouched] = useState(false);
    const asking = useAsking(!touched && job.length === 0);
    /* The roles are the templates the server ships, read from it rather than
       listed here. Long-lived: they change when the server does. */
    const roles = useQuery({ queryKey: ["roles"], queryFn: () => source.listRoles(), staleTime: 30 * 60_000 });
    const hires = useMemo(() => (roles.data ?? []).map(hireFromCard), [roles.data]);

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

                {/* The way in that asks for nothing. Somebody who came to look
                    around should not have to invent a job to get past this
                    page, and before this the only blank start was the
                    seventh card, below the fold, behind a name field. */}
                <button type="button" className="shelf__explore" onClick={onExplore}>
                    Just exploring? Start with a blank teammate <ArrowRightIcon size={14} />
                </button>

                {/* A shelf that could not be read says so; the blank teammate
                    above still works without it. */}
                {roles.isError && (
                    <p className="shelf__quiet" role="alert">
                        Couldn’t load the starting roles. <button className="linkish" onClick={() => void roles.refetch()}>Try again</button>
                    </p>
                )}
                {hires.length > 0 && (
                    <>
                        <div className="shelf__or">or start with a role</div>
                        <div className="shelf__grid">
                            {hires.map((hire) => <HireCard key={hire.id} hire={hire} onPick={onPick} />)}
                        </div>
                    </>
                )}
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
                /* The skills its template ships — the same deck it will hold
                   once hired, read before there is a pod to read it from. */
                skills: hire.taught && hire.taught.length > 0 ? (
                    <ul className="commits">
                        {hire.taught.map((skill) => (
                            <li key={skill.name}>
                                <span className="commits__dot commits__dot--off" />
                                <span className="commits__body">
                                    <span className="commits__title">{humanizeName(skill.name)}</span>
                                    {skill.description && <span className="commits__detail">{skill.description}</span>}
                                </span>
                            </li>
                        ))}
                    </ul>
                ) : undefined,
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
                        <div className="pcard__head"><h3>{hire.bundle ? "Arrives with" : "What you can set up together"}</h3></div>
                        <p className="empty-row">
                            {hire.bundle
                                ? "Hiring brings these. Connections and standing work are set up after, one tap each."
                                : "This role is a starting brief. Apps, sources and schedules are set up after hiring."}
                        </p>
                        <ul className="offer__brings">
                            {hire.brings.map((thing) => (
                                <li key={thing}><ArrowRightIcon size={15} />{thing}</li>
                            ))}
                        </ul>
                        {hire.judgedOn && (
                            <>
                                <div className="pcard__head"><h3>Judged on</h3></div>
                                <ul className="offer__brings">
                                    {hire.judgedOn.map((measure) => (
                                        <li key={measure}><ArrowRightIcon size={15} />{measure}</li>
                                    ))}
                                </ul>
                            </>
                        )}
                    </section>
                ),
            }}
        />
    );
}

/** The wait, said as what it is: the steps that really happen, and a role's
 *  template as a third when it has one. */
function Making({ hire, name, done }: { hire: Hire; name: string; done: string[] }) {
    const steps = useMemo(
        () => [
            { id: "made", label: "Setting up " + (name || "them") },
            { id: "face", label: "Creating their identity" },
            ...(hire.bundle ? [{ id: "template", label: "Bringing what " + hire.name + " comes with" }] : []),
        ],
        [name, hire.bundle, hire.name],
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
 *  So the page keeps the moment and adds the morning. The door comes first
 *  and is the primary: their space is where everything else on this page
 *  happens anyway, and it was the button most people were looking for while
 *  it sat last, styled as the fallback. Under it are the three things that
 *  make a new teammate real — something to do, somewhere to be written to,
 *  and somebody else who can use them — each an existing surface rather than
 *  a checklist item that ticks itself.
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
    needsModel,
    faceSaved,
    templateProblem,
    onOpen,
}: {
    podId: string;
    name: string;
    variant: number;
    hire: Hire;
    job: string;
    orgName: string;
    /** No model is set up for this organization, so they cannot reply yet. */
    needsModel: boolean;
    /** False when the face picked on the shelf could not be stored. */
    faceSaved: boolean;
    /** Why the role's template did not finish arriving, when it did not. */
    templateProblem: string | null;
    onOpen: (move?: FirstMove) => void;
}) {
    const [hello, setHello] = useState(1);
    const openers = useMemo(() => openersFor(hire, job), [hire, job]);
    const exploring = hire.id === "blank" && !job.trim();
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
            {/* "Ready" is a claim about the next message, and without a model
                the next message fails. Hiring still goes through — the
                teammate is real, and setting up a model is one step away. */}
            {needsModel ? (
                <div className="met__line met__needs" role="status">
                    <p>{name} needs an AI model before they can reply.</p>
                    <SetUpModel />
                </div>
            ) : (
                <p className="met__line">
                    {exploring
                        ? name + " starts with no job yet. Tell them about your work and find one together."
                        : hire.id === "blank"
                            ? name + " is ready to learn your work. Share the context for their first task."
                            : name + " is ready. Start with a task, then set up the sources, apps and schedules together."}
                </p>
            )}
            {!faceSaved && (
                <p className="met__note">Couldn’t save the face you picked, so {name} kept the one they came with.</p>
            )}
            {templateProblem && (
                <p className="met__note" role="alert">Not everything {hire.name} comes with arrived: {templateProblem} {name} is hired, and the rest can be set up together.</p>
            )}

            <button className="btn btn--primary met__door" onClick={() => onOpen()}>
                Go to {possessive(name)} space <ArrowRightIcon size={15} />
            </button>

            <div className="met__moves">
                {/* Every hire has openers now — a role brings its own, a
                    described job is handed back, and exploring gets questions
                    — so there is no empty variant of this card to draw. */}
                <section className="met__move">
                    <h3><ChatIcon size={17} />{exploring ? "Or ask " + name + " something" : "Or give " + name + " a first task"}</h3>
                    <p>Opens as a draft in {possessive(name)} space. Nothing sends until you do.</p>
                    <ul className="met__openers">
                        {openers.map((line) => {
                            /* A first win that needs a place says which, and
                               connects it from here — the draft alone would
                               only get "I can't see Intercom" back. */
                            const needs = hire.wins?.find((win) => win.say === line)?.needs;
                            return (
                                <li key={line} className={needs ? "met__opener--needs" : undefined}>
                                    <button onClick={() => onOpen({ say: line })}>
                                        <span>{line}</span>
                                        <ArrowRightIcon size={15} />
                                    </button>
                                    {needs && (
                                        <button className="linkish met__connect" onClick={() => openSettings("connectors", needs.connector)}>
                                            Needs {needs.label}. Connect it
                                        </button>
                                    )}
                                </li>
                            );
                        })}
                    </ul>
                </section>

                {hire.offers && hire.offers.length > 0 && <Offers podId={podId} name={name} offers={hire.offers} />}

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

                <button className="met__side met__judged" onClick={() => onOpen({ open: "scorecard" })}>
                    <CheckIcon size={18} />
                    <b>Choose how {name} is judged</b>
                    <span>{hire.judgedOn ? "A scorecard came with the role. Keep it, and " + name + " is reviewed every Friday." : "Pick what to count, and " + name + " is reviewed every Friday."}</span>
                </button>
            </div>

        </div></div>
    );
}

/** Standing work the role offers, each turned on with one tap.
 *
 *  Not created by the hire: a schedule wakes the teammate on its own, and
 *  that is something a person says yes to. Turned on here it is an ordinary
 *  schedule, owned by whoever pressed the button, and lives in Standing work
 *  from then on. */
function Offers({ podId, name, offers }: { podId: string; name: string; offers: NonNullable<Hire["offers"]> }) {
    const create = useCreateSchedule(podId);
    const [on, setOn] = useState<string[]>([]);
    const [failed, setFailed] = useState<string | null>(null);
    const turnOn = (offer: NonNullable<Hire["offers"]>[number]) => {
        setFailed(null);
        create.mutate({
            name: offer.title.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, ""),
            cron: offer.cron,
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            target: "agent",
            agentName: "pod_default",
            workflowName: "",
            instruction: offer.instruction,
        }, {
            onSuccess: () => setOn((previous) => [...previous, offer.title]),
            onError: () => setFailed(offer.title),
        });
    };
    return (
        <section className="met__move">
            <h3><ClockIcon size={17} />Or let {name} take something on</h3>
            <p>Runs on its own once you turn it on. Change or stop it in About.</p>
            <ul className="met__offers">
                {offers.map((offer) => (
                    <li key={offer.title}>
                        <span>
                            <b>{offer.title}</b>
                            <small>{offer.detail}</small>
                            {failed === offer.title && <small role="alert">Couldn’t turn this on. Try again, or ask {name}.</small>}
                        </span>
                        {on.includes(offer.title)
                            ? <span className="met__on"><CheckIcon size={14} /> On</span>
                            : <button className="btn" disabled={create.isPending} onClick={() => turnOn(offer)}>Turn on</button>}
                    </li>
                ))}
            </ul>
        </section>
    );
}

/** Where the model gets set up. Inside the Lemma app on the machine it runs
 *  on, Server setup's AI model card; anywhere else — a browser, a hosted
 *  workspace — the organization's Models page, which is where a provider key
 *  is added there. */
function SetUpModel() {
    const availability = useThisMacAvailability();
    if (availability === "shown") return <SetUpAiModelLink compact={false} />;
    return (
        <button type="button" className="btn" onClick={() => openSettings("models")}>
            <KeyIcon size={13} /> Set up a model in Settings → Models
        </button>
    );
}
