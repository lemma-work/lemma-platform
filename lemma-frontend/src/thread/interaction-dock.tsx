import { InteractionCard, type Resolve } from "./interaction-card";
import type { Interaction } from "./turns";

/** The shelf between the transcript and the composer, holding whatever the run
 *  is blocked on.
 *
 *  An approval or a question is not a message. It is the only thing you can do
 *  in this conversation until it is answered, and while it lived purely in the
 *  transcript it was wherever it happened to have streamed in — above the fold
 *  if you had scrolled, and announced only by a line of grey text beside the
 *  composer saying "waiting on your answer". A label apologising for a control
 *  being somewhere else is the app telling on itself.
 *
 *  This is not the floating card the transcript one replaced. That one sat at
 *  the bottom of the pane at all times, cut off from the work that raised it,
 *  and vanished the instant it was clicked. Two things make the difference:
 *
 *  - **Only while it is open.** The moment it resolves, the shelf empties and
 *    the record appears in the transcript at the tool call that raised it,
 *    where it stays. What you agreed to is still filed where it happened.
 *  - **Nothing is left behind.** An open pause is always the last thing in the
 *    transcript — the run is stopped, so nothing has arrived after it — so
 *    lifting it out of the flow reorders nothing and detaches it from nothing.
 *
 *  It takes the composer's width rather than a message's. A question is
 *  answered with the hands, not read, and the controls should be as wide as
 *  the box they are standing in front of. */
export function InteractionDock({
    interaction,
    teammate,
    onResolve,
}: {
    interaction: Interaction | null;
    teammate: string;
    onResolve?: Resolve;
}) {
    if (!interaction) return null;
    return (
        <div className="dock" data-kind={interaction.kind}>
            <div className="dock__row">
                {/* Keyed by the pause, so answering one and being handed
                    another does not animate a card into a card — the second
                    arrives as its own thing. */}
                <InteractionCard
                    key={interaction.id}
                    interaction={interaction}
                    teammate={teammate}
                    onResolve={onResolve}
                    docked
                />
            </div>
        </div>
    );
}
