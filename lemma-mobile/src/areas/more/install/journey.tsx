import { useState, type ReactNode } from "react";
import { ActivityIndicator, KeyboardAvoidingView, Platform, ScrollView, View } from "react-native";
import { Field, Group, ReadProblem, Reading, Say } from "../../admin/ui";
import { problemOf } from "../../admin/words";
import { Meter } from "../../../ui/chrome";
import { LemmaFace } from "../../../ui/face";
import { Icon } from "../../../ui/icons";
import { Button, Pill, Press } from "../../../ui/press";
import { Sheet } from "../../../ui/sheet";
import { Counted, Num, Text } from "../../../ui/text";
import { color, TOUCH } from "../../../ui/tokens";
import type { Journey } from "./hooks";
import { useFirstOrg } from "./hooks";
import { installArrival } from "./arrival";
import {
    NEW, STEP_NAMES, actionTone, doneOf, includedLine, missingAnswer, phoneGroups, progressFill,
    stepAt, stepStateOf, stoppedWords, variableLabel,
} from "./model";
import type { ConnectorPlan, ImportStatusResponse, PlanStep, VariableSpec } from "./source";
import {
    actionLabel, askedConnectors, askedVariables, blockedBy, connectorLine, CREATE_INSTALLATION, defaultPick, needsAnswer, shareableAccounts, statusLine, suggestedValue,
} from "@web/site/import/plan";

/** The install journey, drawn for a phone: Choose, Review, Install, in a
 *  tall sheet over the repository's page. The state is the screen's
 *  (`useJourney`), so closing the sheet loses nothing; whatever leaves the
 *  page (connecting an account, opening what was installed) closes it
 *  first through `leave`. Choices are drawn in the sheet itself rather than
 *  in a second sheet over it. */
export function JourneySheet({ open, onClose, journey, title, leave }: {
    open: boolean;
    onClose: () => void;
    journey: Journey;
    title: string;
    /** Close the sheet and go somewhere; `back` reopens it on return. */
    leave: (href: string, back: boolean) => void;
}) {
    return (
        <Sheet open={open} onClose={onClose} title={`Install ${title}`} tall>
            <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flexShrink: 1 }}>
                <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 12, gap: 18 }} keyboardShouldPersistTaps="handled">
                    <JourneyBody journey={journey} title={title} leave={leave} />
                </ScrollView>
            </KeyboardAvoidingView>
        </Sheet>
    );
}

function JourneyBody({ journey, title, leave }: { journey: Journey; title: string; leave: (href: string, back: boolean) => void }) {
    const { orgs, job } = journey;
    if (orgs.pending) return <Reading label="Reading your organizations" />;
    if (orgs.error && !orgs.orgs.length) return <ReadProblem said={problemOf(orgs.error, "Your organizations could not be read.")} onRetry={orgs.reread} />;
    if (!orgs.orgs.length) return <FirstOrg title={title} />;
    if (!job) return <Choose journey={journey} title={title} />;
    if (job.status === "COMPLETED") return <Done journey={journey} job={job} title={title} leave={leave} />;
    if (job.status === "FAILED" || job.status === "CANCELLED" || job.status === "PARTIALLY_CANCELLED") return <Stopped journey={journey} job={job} />;
    return <Underway journey={journey} job={job} title={title} leave={leave} />;
}

/* ── the pieces ──────────────────────────────────────────────────────── */

/** Choose · Review · Install, the one now in ink, the ones done ticked. */
function Steps({ at }: { at: 0 | 1 | 2 | 3 }) {
    return (
        <View accessibilityLabel={at === 3 ? "All three steps done" : `Step ${at + 1} of 3: ${STEP_NAMES[at]}`} style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            {STEP_NAMES.map((name, index) => {
                const done = index < at;
                const now = index === at;
                return (
                    <View key={name} style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                        {index > 0 ? <View style={{ width: 14, height: 1, backgroundColor: color.line2 }} /> : null}
                        <View style={{ width: 20, height: 20, borderRadius: 10, alignItems: "center", justifyContent: "center", backgroundColor: now ? color.ink : done ? color.wash : "transparent", borderWidth: now || done ? 0 : 1, borderColor: color.line2 }}>
                            {done ? <Icon name="check" size={12} width={2.4} tone={color.ink2} /> : <Num size={11.5} tone={now ? color.onInk : color.ink3}>{index + 1}</Num>}
                        </View>
                        <Text size={13.5} weight={now ? "medium" : "regular"} tone={now ? color.ink : done ? color.ink2 : color.ink3}>{name}</Text>
                    </View>
                );
            })}
        </View>
    );
}

/** One of a few, chosen in place: a tick on the one held. */
function Choice({ on, label, hint, glyph, onPress, disabled = false, last = false }: {
    on: boolean;
    label: string;
    hint?: string | null;
    glyph?: ReactNode;
    onPress: () => void;
    disabled?: boolean;
    last?: boolean;
}) {
    return (
        <Press
            role="radio"
            label={hint ? `${label}, ${hint}` : label}
            accessibilityState={{ selected: on, disabled }}
            disabled={disabled}
            onPress={onPress}
            style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: hint ? 58 : 50, paddingVertical: 6, borderBottomWidth: last ? 0 : 1, borderBottomColor: color.line, opacity: disabled ? 0.45 : 1 }}
        >
            {glyph ? <View style={{ width: 30, alignItems: "center" }}>{glyph}</View> : null}
            <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
                <Text size={16} numberOfLines={1}>{label}</Text>
                {hint ? <Text size={13} tone={color.ink3} numberOfLines={2}>{hint}</Text> : null}
            </View>
            {on ? <Icon name="check" size={18} width={2} /> : null}
        </Press>
    );
}

/** A box to tick before something that cannot be taken back. */
function Tick({ on, onChange, children }: { on: boolean; onChange: (next: boolean) => void; children: ReactNode }) {
    return (
        <Press accessibilityRole="checkbox" accessibilityState={{ checked: on }} onPress={() => onChange(!on)} style={{ flexDirection: "row", alignItems: "flex-start", gap: 12, minHeight: TOUCH, paddingVertical: 8 }}>
            <View style={{ width: 22, height: 22, marginTop: 1, borderRadius: 6, borderWidth: on ? 0 : 1.5, borderColor: color.ink3, backgroundColor: on ? color.ink : "transparent", alignItems: "center", justifyContent: "center" }}>
                {on ? <Icon name="check" size={14} width={2.6} tone={color.onInk} /> : null}
            </View>
            <View style={{ flex: 1 }}>{children}</View>
        </Press>
    );
}

/** A quiet word that does something: "Show each part", "Check again". */
function Quiet({ label, onPress, disabled = false }: { label: string; onPress: () => void; disabled?: boolean }) {
    return (
        <Press label={label} onPress={onPress} disabled={disabled} style={{ alignSelf: "flex-start", minHeight: TOUCH - 8, justifyContent: "center", opacity: disabled ? 0.45 : 1 }}>
            <Text size={14.5} weight="medium">{label}</Text>
        </Press>
    );
}

/** The Lemma the install is for, by face and name. */
function ForLine({ journey, job, preparing }: { journey: Journey; job: ImportStatusResponse; preparing: boolean }) {
    const lemma = journey.chosenLemma;
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
            <LemmaFace id={lemma?.id ?? job.pod_id} icon={lemma?.iconUrl} size={26} />
            <Text size={14.5} tone={color.ink2} numberOfLines={1} style={{ flex: 1 }}>
                {preparing ? "Preparing for " : "For "}<Text size={14.5} weight="medium">{journey.who}</Text>
            </Text>
        </View>
    );
}

function Warnings({ list }: { list: readonly string[] }) {
    if (!list.length) return null;
    return (
        <View style={{ gap: 6 }}>
            {list.map((warning, index) => <Say key={index} tone="bad">{warning}</Say>)}
        </View>
    );
}

/** How far it has got: the bar to where it is, and how many of how many. */
function Progress({ job }: { job: ImportStatusResponse }) {
    const fill = progressFill(job.progress);
    if (fill === null) {
        return (
            <View style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 24 }}>
                <ActivityIndicator size="small" color={color.violet} />
                <Text size={14} tone={color.ink2}>{statusLine(job.status)}</Text>
            </View>
        );
    }
    return (
        <View style={{ gap: 6 }}>
            <Meter fill={fill} mark={null} label={`${job.progress.done} of ${job.progress.total} installed`} />
            <Counted size={13} tone={color.ink3}>{`${job.progress.done} of ${job.progress.total}`}</Counted>
        </View>
    );
}

/** A step's mark while it installs. */
function StepState({ step }: { step: PlanStep }) {
    const state = stepStateOf(step.status);
    if (state.tone === "running") {
        return (
            <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                <ActivityIndicator size="small" color={color.violet} />
                <Text size={13} tone={color.violetInk}>{state.label}</Text>
            </View>
        );
    }
    if (state.tone === "done") {
        return (
            <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
                <Icon name="check" size={14} width={2.2} tone={color.ink2} />
                <Text size={13} tone={color.ink2}>{state.label}</Text>
            </View>
        );
    }
    return <Pill text={state.label} tone={state.tone === "bad" ? "amber" : "grey"} />;
}

/** Every part, by kind: what each does to the Lemma while reviewing, and
 *  how it is going while installing. */
function Parts({ steps, installing }: { steps: PlanStep[]; installing: boolean }) {
    const groups = phoneGroups(steps);
    return (
        <View style={{ gap: 14 }}>
            {groups.map((group) => (
                <Group key={group.kind} label={`${group.label} · ${group.steps.length}`}>
                    {group.steps.map((step, index) => (
                        <View key={step.index} style={{ paddingVertical: 8, minHeight: 44, justifyContent: "center", borderBottomWidth: index === group.steps.length - 1 ? 0 : 1, borderBottomColor: color.line, gap: 3 }}>
                            <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
                                <Text size={15} numberOfLines={1} style={{ flex: 1 }}>{step.name}</Text>
                                {installing ? <StepState step={step} /> : <Pill text={actionLabel(step)} tone={actionTone(step)} />}
                            </View>
                            {step.error ? <Text size={13} tone={color.amber}>{step.error}</Text> : null}
                        </View>
                    ))}
                </Group>
            ))}
        </View>
    );
}

/** A failure, with "Check again" when it was only the reading that failed. */
function Failure({ journey }: { journey: Journey }) {
    if (!journey.error) return null;
    return (
        <View style={{ gap: 4 }}>
            <Say tone="bad">{journey.error}</Say>
            {journey.lost ? <Quiet label="Check again" onPress={journey.checkAgain} /> : null}
        </View>
    );
}

/* ── no organization yet ─────────────────────────────────────────────── */

/** An organization for somebody who has none, made here rather than on a
 *  screen of its own: they came to install something, and need somewhere
 *  for it to go, named after them, with the install carrying on. */
function FirstOrg({ title }: { title: string }) {
    const first = useFirstOrg();
    const [name, setName] = useState<string | null>(null);
    return (
        <View style={{ gap: 14 }}>
            <Text size={20} weight="medium">First, an organization</Text>
            <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>Somewhere for {title} and your other Lemmas to live. It is private to you; you can invite people later.</Text>
            <Field label="Organization name" value={name ?? first.suggested} onChange={setName} maxLength={80} autoCapitalize="words" />
            <Button title="Continue" busy={first.busy} onPress={() => void first.make(name ?? first.suggested)} />
            {first.error ? <Say tone="bad">{first.error}</Say> : null}
        </View>
    );
}

/* ── Choose ──────────────────────────────────────────────────────────── */

function Choose({ journey, title }: { journey: Journey; title: string }) {
    const { orgs, lemmas, list, target, created } = journey;
    const [orgsOpen, setOrgsOpen] = useState(false);
    const isNew = target === NEW;
    return (
        <>
            <Steps at={0} />
            <Text size={20} weight="medium">Who gets {title}?</Text>

            {orgs.orgs.length > 1 ? (
                <Group label="Organization">
                    <Press label={`Organization, ${orgs.org?.name ?? ""}`} onPress={() => setOrgsOpen((was) => !was)} style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 50, borderBottomWidth: orgsOpen ? 1 : 0, borderBottomColor: color.line }}>
                        <Text size={16} numberOfLines={1} style={{ flex: 1 }}>{orgs.org?.name}</Text>
                        <Text size={14.5} tone={color.ink3}>{orgsOpen ? "Done" : "Change"}</Text>
                    </Press>
                    {orgsOpen ? orgs.orgs.map((one, index) => (
                        <Choice key={one.id} on={one.id === orgs.org?.id} label={one.name} onPress={() => { journey.chooseOrg(one.id); setOrgsOpen(false); }} last={index === orgs.orgs.length - 1} />
                    )) : null}
                </Group>
            ) : null}

            <View accessibilityRole="radiogroup">
                <Choice
                    on={isNew}
                    label="A new Lemma"
                    hint={`Starts with only what ${title} brings`}
                    glyph={<View style={{ width: 28, height: 28, borderRadius: 8, borderWidth: 1, borderColor: color.line2, alignItems: "center", justifyContent: "center" }}><Icon name="plus" size={16} tone={color.ink2} /></View>}
                    onPress={() => journey.setTarget(NEW)}
                    last={isNew}
                />
                {isNew ? (
                    <View style={{ paddingTop: 4, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: color.line }}>
                        <Field
                            label="Name"
                            value={created?.name ?? journey.name}
                            onChange={journey.setName}
                            editable={!created}
                            maxLength={60}
                            autoCapitalize="words"
                            hint={created ? "Made already: going on adds to it." : null}
                        />
                    </View>
                ) : null}
                {lemmas.isPending ? <Reading label="Reading your Lemmas" /> : null}
                {lemmas.isError ? <ReadProblem said={problemOf(lemmas.error, "Your Lemmas could not be read.")} onRetry={() => void lemmas.refetch()} /> : null}
                {list.length ? <Text size={12.5} tone={color.ink3} style={{ paddingTop: 14, paddingBottom: 2 }}>or give it to</Text> : null}
                {list.map((lemma, index) => (
                    <Choice
                        key={lemma.id}
                        on={target === lemma.id}
                        label={lemma.name}
                        hint="Adds to what it already has"
                        glyph={<LemmaFace id={lemma.id} icon={lemma.iconUrl} size={28} />}
                        onPress={() => journey.setTarget(lemma.id)}
                        last={index === list.length - 1}
                    />
                ))}
            </View>

            <View style={{ gap: 8 }}>
                <Button
                    title="Review what’s included"
                    busy={journey.busy === "review"}
                    disabled={!journey.orgId || (isNew && !created && !journey.name.trim())}
                    onPress={() => void journey.review()}
                />
                <Text size={13} tone={color.ink3}>Nothing is added until you approve it.</Text>
                <Failure journey={journey} />
            </View>
        </>
    );
}

/* ── Review, and progress while planning or installing ─────────────────── */

function Underway({ journey, job, title, leave }: { journey: Journey; job: ImportStatusResponse; title: string; leave: (href: string, back: boolean) => void }) {
    const reviewing = job.status === "AWAITING_CONFIRMATION";
    const installing = job.status === "APPLYING";
    const plan = job.plan;
    const steps = plan?.steps ?? [];
    const groups = phoneGroups(steps);
    const asked = plan ? askedVariables(plan.variables) : [];
    /* Only what nothing can stand in for is put in front of the person: an
       account, or a name that could not be made up. The rest is named after
       the Lemma, and shown filled in to whoever opens the parts. */
    const needs = journey.details ? asked : asked.filter((one) => needsAnswer(one, journey.who));
    const warnings = [...job.warnings, ...(plan?.warnings ?? [])];
    const missing = missingAnswer(asked, journey.values, journey.who);
    const connectors = plan?.connectors ?? [];
    const askedOf = askedConnectors(connectors);
    /* A connector with nowhere to map to stops the install as picked. */
    const blocked = connectors.some((one) => blockedBy(one, journey.picks[one.slug] ?? defaultPick(one)) !== null);
    const at = stepAt(job);
    return (
        <>
            {at !== null && at < 3 ? <Steps at={at} /> : null}
            <ForLine journey={journey} job={job} preparing={!reviewing && !installing} />
            <Text size={20} weight="medium">{reviewing ? "What’s included" : statusLine(job.status)}</Text>
            {reviewing ? <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>{includedLine(journey.who, title, groups)}</Text> : <Progress job={job} />}
            <Warnings list={warnings} />

            {(journey.details || !reviewing) && groups.length ? <Parts steps={steps} installing={installing} /> : null}
            {reviewing && groups.length ? <Quiet label={journey.details ? "Hide the parts" : "Show each part"} onPress={() => journey.setDetails(!journey.details)} /> : null}

            {reviewing && plan ? (
                <View style={{ gap: 14 }}>
                    {needs.length ? (
                        <Group label="Needs from you">
                            <View style={{ gap: 16, paddingTop: 4 }}>
                                {needs.map((variable) => (
                                    <Variable
                                        key={variable.name}
                                        variable={variable}
                                        value={journey.values[variable.name] ?? suggestedValue(variable, journey.who)}
                                        onChange={(value) => journey.setValue(variable.name, value)}
                                    />
                                ))}
                            </View>
                        </Group>
                    ) : null}
                    {askedOf.length ? (
                        <Group label="Connections">
                            <View style={{ gap: 16, paddingTop: 4 }}>
                                {askedOf.map((one) => (
                                    <ConnectorChoice key={one.slug} plan={one} pick={journey.picks[one.slug] ?? defaultPick(one)} onPick={(pick) => journey.setPick(one.slug, pick)} />
                                ))}
                            </View>
                        </Group>
                    ) : null}
                    {plan.has_destructive_steps ? (
                        <Tick on={journey.confirm} onChange={journey.setConfirm}>
                            <Text size={15} style={{ lineHeight: 21 }}>Replace what {journey.who} already has where it says Replaces yours.</Text>
                        </Tick>
                    ) : null}
                    <View style={{ flexDirection: "row", gap: 10 }}>
                        <Button title="Back" kind="outline" grow disabled={journey.busy !== null && journey.busy !== "back"} busy={journey.busy === "back"} onPress={() => void journey.back()} />
                        <Button
                            title="Install everything"
                            grow
                            busy={journey.busy === "install"}
                            disabled={(journey.busy !== null && journey.busy !== "install") || missing || blocked || (plan.has_destructive_steps && !journey.confirm)}
                            onPress={() => void journey.install()}
                        />
                    </View>
                </View>
            ) : null}

            {journey.working ? (
                <Button title="Stop" kind="quiet" busy={journey.busy === "stop"} disabled={journey.busy !== null && journey.busy !== "stop"} onPress={() => void journey.stop()} />
            ) : null}
            <Failure journey={journey} />
        </>
    );
}

/** One thing the person has to answer: a name in a box. */
function Variable({ variable, value, onChange }: { variable: VariableSpec; value: string; onChange: (value: string) => void }) {
    const label = variableLabel(variable);
    return (
        <Field
            label={variable.required ? label : `${label} (optional)`}
            value={value}
            onChange={onChange}
            hint={variable.description}
            autoCapitalize="none"
        />
    );
}

/** A connector the bundle brings, and what the person picks for it in
 *  their organization: which installation it runs through (or Lemma's own
 *  app, installed now), and for one that runs as one shared account,
 *  whether to share one of theirs. Sharing is never assumed. */
function ConnectorChoice({ plan, pick, onPick }: { plan: ConnectorPlan; pick: { installation: string; share: string }; onPick: (pick: { installation: string; share: string }) => void }) {
    const stop = blockedBy(plan, pick);
    const installations = [
        ...plan.installations.map((one) => ({ value: one.auth_config_id, label: one.name + (one.is_default ? " (default)" : "") })),
        ...(plan.can_create_installation ? [{ value: CREATE_INSTALLATION, label: "Install Lemma’s own app for it" }] : []),
    ];
    const shareable = plan.mode === "SHARED" ? shareableAccounts(plan, pick) : [];
    return (
        <View style={{ gap: 4 }}>
            <Text size={13.5} tone={color.ink2}>{plan.slug}</Text>
            <Text size={13} tone={color.ink3} style={{ lineHeight: 18 }}>{connectorLine(plan)}</Text>
            {installations.length > 1 || (installations.length === 1 && pick.installation !== installations[0].value) ? (
                <View accessibilityRole="radiogroup">
                    {installations.map((one, index) => (
                        <Choice key={one.value} on={pick.installation === one.value} label={one.label} onPress={() => onPick({ installation: one.value, share: "" })} last={index === installations.length - 1} />
                    ))}
                </View>
            ) : null}
            {plan.mode === "SHARED" && pick.installation && pick.installation !== CREATE_INSTALLATION ? (
                <View accessibilityRole="radiogroup">
                    {[{ value: "", label: "Share none for now" }, ...shareable.map((one) => ({ value: one.account_id, label: "Share " + (one.label || "my account") }))].map((one, index, all) => (
                        <Choice key={one.value || "none"} on={pick.share === one.value} label={one.label} onPress={() => onPick({ ...pick, share: one.value })} last={index === all.length - 1} />
                    ))}
                </View>
            ) : null}
            {stop ? <Say tone="bad">{stop}</Say> : null}
        </View>
    );
}

/* ── the end ─────────────────────────────────────────────────────────── */

function Done({ journey, job, title, leave }: { journey: Journey; job: ImportStatusResponse; title: string; leave: (href: string, back: boolean) => void }) {
    const done = doneOf(job.pod_id, job.plan?.steps ?? [], journey.who, title);
    const lemma = journey.chosenLemma;
    const warnings = [...job.warnings, ...(job.plan?.warnings ?? [])];
    return (
        <>
            <Steps at={3} />
            <View style={{ alignItems: "center", gap: 10, paddingVertical: 8 }}>
                <LemmaFace id={lemma?.id ?? job.pod_id} icon={lemma?.iconUrl} size={56} />
                <Text accessibilityRole="header" size={20} weight="medium" style={{ textAlign: "center" }}>{done.heading}</Text>
                <Text size={15} tone={color.ink2} style={{ textAlign: "center", lineHeight: 21 }}>{done.line}</Text>
            </View>
            <View style={{ gap: 6 }}>
                <Button title={done.open.label} onPress={() => { installArrival.mark(); journey.orgs.arriveIn(journey.orgId); leave(done.open.href, false); }} />
                {done.other ? <Button title={done.other.label} kind="quiet" onPress={() => { installArrival.mark(); journey.orgs.arriveIn(journey.orgId); leave(done.other!.href, false); }} /> : null}
            </View>
            <Warnings list={warnings} />
        </>
    );
}

function Stopped({ journey, job }: { journey: Journey; job: ImportStatusResponse }) {
    return (
        <>
            <Text size={20} weight="medium">{statusLine(job.status)}</Text>
            <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>{stoppedWords(job, journey.who)}</Text>
            {job.plan?.steps.some((step) => step.error) ? <Parts steps={job.plan.steps} installing /> : null}
            <Button title="Start again" busy={journey.busy === "back"} onPress={() => void journey.back()} />
            <Failure journey={journey} />
        </>
    );
}
