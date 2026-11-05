"use client";

import type { ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { isForbidden } from "@/session/auth-state";
import { ChatIcon, WarningIcon } from "@/ui/icons";
import type { FlowArm, FlowItem, FlowStep, WorkflowShape } from "./shape";

/** How a workflow runs, drawn on its page.
 *
 *  Nothing here draws a box-and-arrow graph. `workflows.visualize` returns an
 *  entire HTML debugging page rather than a graph format, so there is nothing
 *  to compose with — and at this size a canvas would answer worse than an
 *  outline does. "How does this run" is a question about order, so the answer
 *  is still a column you read downwards. But where the workflow decides, the
 *  column forks: each rule gets an arm with its own steps, and the arms close
 *  again where they meet. A flat column there said every arm runs, one after
 *  another, which is the one thing a decision guarantees does not happen.
 *
 *  Nothing here edits the graph either: a workflow is changed by asking the
 *  teammate to change it. A form over `nodes`/`edges` would be a worse graph editor than the one
 *  this product does not want to build, and it would throw away the reason —
 *  the conversation is where "why does this branch on 5000" keeps its answer.
 */

/** The shape of the thing, above the record of it doing the thing.
 *
 *  The heading on this section has always said "what it runs the same way
 *  twice" and the section has never shown what that is — a name, a sentence
 *  and a step *count*. This is the count opened up: the trigger, then every
 *  step in the order a run meets them.
 *
 *  Its own spine rather than the run history's, because the two are different
 *  claims. A step here is something that *can* happen; a step in the history
 *  is something that *did*, and colouring these by a status they do not have
 *  would be inventing one. So the marks are hollow and the tones are gone.
 */
export function Shape({ query, teammate, onDiscuss }: {
    /* The query rather than the shape, because three of the four things this
       draws are states the shape cannot be in: still arriving, refused, and
       came back unreadable. A `shape | null` prop collapses all three into one
       blank. */
    query: UseQueryResult<WorkflowShape | null, Error>;
    teammate: string;
    onDiscuss?: () => void;
}) {
    const shape = query.data ?? null;

    return (
        <section className="wf-shape" aria-label="How it runs">
            <h4 className="wf-heading">How it runs</h4>

            {query.isPending && <p className="empty-row" role="status">Reading…</p>}
            {query.isError && (
                <p className="empty-row" role="alert">
                    {isForbidden(query.error)
                        ? "You may not read this workflow’s steps."
                        : "Couldn’t load workflow steps."}{" "}
                    <button className="linkish" onClick={() => void query.refetch()}>Try again</button>
                </p>
            )}
            {query.isSuccess && !shape && (
                <p className="empty-row" role="alert">This workflow came back in a shape this app could not read.</p>
            )}

            {shape && (
                <>
                    {/* The trigger sits on the same spine as the steps and one
                        mark above the first of them, because that is what it
                        is: the thing that happens before step one. Filled,
                        unlike the steps, so the column has a head. */}
                    <ol className="wf-spine">
                        <li className="wf-spine__row wf-spine__row--start">
                            <span className="wf-spine__mark" aria-hidden="true" />
                            <span className="wf-spine__body">
                                <span className="wf-spine__title"><b>{shape.start.says}</b></span>
                                {shape.start.detail.map((line, at) => (
                                    <span className="wf-spine__note" key={at}>{line}</span>
                                ))}
                            </span>
                        </li>
                        <Items items={shape.flow} />
                    </ol>

                    {shape.ordered.length === 0 && shape.orphans.length === 0 && (
                        <p className="empty-row">No steps yet — this workflow has a name and nothing in it.</p>
                    )}

                    {/* Above the orphans, not at the top: the trouble is
                        always *about* what is underneath it, and a warning
                        before the thing it warns about is a warning nobody can
                        check. */}
                    {shape.trouble && (
                        <p className="wf-shape__trouble" role="alert">
                            <WarningIcon size={15} aria-hidden="true" />
                            <span>{shape.trouble}</span>
                        </p>
                    )}

                    {shape.loose.length > 0 && (
                        <>
                            {/* Named plainly, because an orphan is a real
                                finding. A step nothing reaches is work
                                somebody wrote and then wired past, and it will
                                sit there being nobody's fault forever unless
                                something says it is there. One spine per
                                chain: an orphaned chain is still joined to
                                itself, just not to anything above it. */}
                            <h5 className="wf-heading wf-heading--sub">Unconnected steps</h5>
                            {shape.loose.map((items, at) => (
                                <ol className="wf-spine wf-spine--loose" key={at}>
                                    <Items items={items} />
                                </ol>
                            ))}
                        </>
                    )}

                    {onDiscuss && (
                        <p className="wf-shape__ask">
                            <button className="btn wf-ask" onClick={onDiscuss}>
                                <ChatIcon size={15} aria-hidden="true" /><span>Ask {teammate} to change it</span>
                            </button>
                        </p>
                    )}
                </>
            )}
        </section>
    );
}

/** One step, as a row on the spine.
 *
 *  Led by the name its author gave it, when there is one: "Chase the owners"
 *  says what the step is for, `chase` only what it is called. The id stays,
 *  small and in the mono face the run history sets a step id in, because it
 *  is the string `failed_node_id` names when a run dies, so the two lists
 *  have to be readable against each other by eye.
 */
function Step({ step, children }: { step: FlowStep; children?: ReactNode }) {
    /* A label that repeats the id is two columns of the same word. */
    const named = Boolean(step.label && step.label !== step.id);
    return (
        <li className="wf-spine__row" data-kind={step.kind || undefined} data-broken={step.unreadable || undefined}>
            <span className="wf-spine__mark" aria-hidden="true" />
            <div className="wf-spine__body">
                <span className="wf-spine__title">
                    {named ? <strong>{step.label}</strong> : step.id && <b>{step.id}</b>}
                    <i>{step.says}</i>
                    {named && step.id && <code>{step.id}</code>}
                </span>
                {step.detail.map((line, at) => (
                    <span className="wf-spine__note" key={at}>{line}</span>
                ))}
                {children}
            </div>
        </li>
    );
}

/** The outline, a row per item. A fork and a loop are rows like any other
 *  step, with what happens inside them nested under their title, so the
 *  spine's rule runs past them to whatever comes next. */
function Items({ items }: { items: FlowItem[] }) {
    return items.map((item, at) => {
        switch (item.type) {
            case "step":
                return <Step key={item.step.id || "step-" + at} step={item.step} />;
            case "fork":
                return (
                    <Step key={item.step.id || "fork-" + at} step={item.step}>
                        {/* Said once, so each arm can be only its value. */}
                        {item.on && <span className="wf-spine__note">On <code>{item.on}</code></span>}
                        <div className="wf-fork">
                            {item.arms.map((arm, one) => <Arm key={one} arm={arm} then={item.then} switched={Boolean(item.on)} />)}
                        </div>
                    </Step>
                );
            case "loop":
                return (
                    <Step key={item.step.id || "loop-" + at} step={item.step}>
                        <ol className="wf-spine wf-spine--inner"><Items items={item.body} /></ol>
                    </Step>
                );
            case "jump":
                return (
                    <li className="wf-spine__row wf-spine__row--jump" key={"jump-" + at}>
                        <span className="wf-spine__mark" aria-hidden="true">{item.back ? "↩" : "→"}</span>
                        <div className="wf-spine__body">
                            <span className="wf-spine__title">
                                <i>{item.to ? (item.back ? "Back to" : "On to") : "Goes to"}</i>
                                {item.to ? nameOf(item.to) : <b>{item.id || "a step it did not name"}</b>}
                                {!item.to && item.id && <i>, which is not a step here</i>}
                            </span>
                        </div>
                    </li>
                );
            case "end":
                return (
                    <li className="wf-spine__row wf-spine__row--end" key={"end-" + at}>
                        <span className="wf-spine__mark" aria-hidden="true" />
                        <div className="wf-spine__body">
                            <span className="wf-spine__title"><i>{item.inLoop ? "On to the next one." : "The run ends here."}</i></span>
                        </div>
                    </li>
                );
        }
    });
}

/** One way out of a decision: the condition, then what it leads to. The
 *  condition is set in mono because it is an expression the teammate wrote
 *  and somebody reading it may want to quote it back. */
function Arm({ arm, then, switched }: { arm: FlowArm; then: FlowStep | null; switched: boolean }) {
    return (
        <div className="wf-arm">
            <p className="wf-arm__when">
                {arm.when === null ? "Otherwise" : <>{switched ? "Is" : "If"} <code>{arm.when}</code></>}
            </p>
            <ol className="wf-spine wf-spine--inner">
                {arm.items.length > 0 ? <Items items={arm.items} /> : (
                    <li className="wf-spine__row wf-spine__row--jump">
                        <span className="wf-spine__mark" aria-hidden="true">↓</span>
                        <div className="wf-spine__body">
                            <span className="wf-spine__title"><i>Straight on{then ? " to" : ""}</i>{then && nameOf(then)}</span>
                        </div>
                    </li>
                )}
            </ol>
        </div>
    );
}

/** A step named the way its own row names it, for a line that points at it. */
function nameOf(step: FlowStep): ReactNode {
    if (step.label && step.label !== step.id) return <strong>{step.label}</strong>;
    return <b>{step.id}</b>;
}
