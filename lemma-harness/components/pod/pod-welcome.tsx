'use client';

import { useState } from 'react';

import { ResourceIdentity } from '@/components/shared/resource-identity';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { AppWindow, ArrowRight, Bot, MessageCircle, Users, type LemmaIcon } from '@/components/ui/icons';
import { Input } from '@/components/ui/input';
import { LEM_SEED } from '@/lib/identity/seeded-identity';
import {
    POD_WELCOME_OPTIONS,
    POD_WELCOME_OWN_WORDS_INSTRUCTIONS,
    POD_WELCOME_SURPRISE,
    type PodWelcomeCardId,
    type PodWelcomeOptionId,
} from '@/lib/pods/pod-welcome';
import { DEFAULT_RESPONDER_NAME } from '@/lib/utils/agents';
import { cn } from '@/lib/utils';

/**
 * A mark per option, from the icon vocabulary rather than drawn here.
 *
 * These started as bespoke illustrations, back when each option owned a 92px
 * picture panel and the difference between them had to be *drawn*. At 34px
 * that stopped being true — three shapes in a row reads the same whether it
 * means agents or people — and a file of hand-rolled SVG for four glyphs is
 * exactly what the icon set exists to prevent. `Bot` against `Users` separates
 * the two middle rows better than any silhouette did at this size.
 */
const OPTION_MARKS: Record<PodWelcomeCardId, LemmaIcon> = {
    surface: MessageCircle,
    app: AppWindow,
    agent: Bot,
    people: Users,
};

export interface PodWelcomeChoice {
    message: string;
    instructions: string;
    optionId: PodWelcomeOptionId | null;
}

interface PodWelcomeProps {
    onStart: (choice: PodWelcomeChoice) => void;
    /** Sends the greeting and lets the welcome turn run, exactly as before. */
    onSkip: () => void;
}

/**
 * The door a new pod opens behind.
 *
 * It exists to produce the first message rather than to explain the product:
 * every control on it sends a real sentence, and the only path that still sends
 * `"Hi"` is the one where somebody declines to say anything. No tour, no
 * numbered steps, no paragraph.
 *
 * The screen is split because it is doing two jobs, and they are not the same
 * size. The left half introduces somebody — Lem, drawn large enough that its
 * eyes read and its idle motion runs, on its own tone — and that is the whole
 * point of a first screen. The right half is a menu, and a menu should be small:
 * four rows, a field, and a way past. An earlier pass had this backwards, with
 * four large picture panels and Lem at favicon size in the corner, so the
 * introduction was the smallest thing on the introduction screen.
 *
 * Appearance lives in `styles/features/pod-welcome.css` rather than in class
 * strings here, because every control is a `Button` or an `Input` and skinning
 * those inline is exactly the drift the design audit exists to catch.
 */
export function PodWelcome({ onStart, onSkip }: PodWelcomeProps) {
    const [ownWords, setOwnWords] = useState('');

    const submitOwnWords = (event: React.FormEvent) => {
        event.preventDefault();
        const message = ownWords.trim();
        if (!message) return;
        onStart({
            message,
            instructions: POD_WELCOME_OWN_WORDS_INSTRUCTIONS,
            optionId: null,
        });
    };

    return (
        <Dialog open onOpenChange={(next) => { if (!next) onSkip(); }}>
            <DialogContent className="!max-w-[42rem] gap-0 overflow-hidden p-0">
                <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,19.5rem)_minmax(0,1fr)]">

                    {/* Lem's half. Tone 0 is its own colour, washed into the
                        sheet rather than laid on top of it, so the panel reads
                        as the creature's ground and not as a coloured box. */}
                    <aside className="lm-identity-hue-0 pod-welcome-portrait">
                        <ResourceIdentity
                            seed={LEM_SEED}
                            label={DEFAULT_RESPONDER_NAME}
                            kind="being"
                            size={84}
                            /* Well past the size where the identity system turns
                               on a being's rich motion, so Lem is awake when the
                               door opens rather than a sticker of itself. */
                            className="h-[84px] w-[84px] shrink-0"
                        />

                        <div>
                            {/* One line for every pod, because it is true in
                                every pod: Lem is the only thing in here yet, and
                                "first" is what makes the second one — the row
                                that offers to hire one — read as the natural next
                                thought rather than as an upsell. It also drops a
                                split where a fifth pod was being told it was
                                somebody's first. */}
                            <DialogTitle className="pod-welcome-title">
                                {DEFAULT_RESPONDER_NAME} is the first agent here.
                            </DialogTitle>
                            {/* "Answers where you already chat" was the weakest
                                true thing we could have said: it makes Lem sound
                                like a support desk waiting to be asked. The
                                point of a surface is the opposite — once it is
                                on Telegram it can start the conversation. */}
                            <DialogDescription className="pod-welcome-lede">
                                It builds what you ask for, and it can work from
                                Telegram or Slack.
                            </DialogDescription>
                        </div>

                        {/* The one control that asks for nothing — no decision,
                            no sentence — so it belongs with the introduction
                            rather than at the end of a list of decisions. */}
                        <Button
                            type="button"
                            variant="secondary"
                            className="pod-welcome-surprise"
                            onClick={() => onStart({
                                message: POD_WELCOME_SURPRISE.message,
                                instructions: POD_WELCOME_SURPRISE.instructions,
                                optionId: POD_WELCOME_SURPRISE.id,
                            })}
                        >
                            {POD_WELCOME_SURPRISE.title}
                            <ArrowRight className="pod-welcome-surprise-arrow h-3.5 w-3.5" />
                        </Button>
                    </aside>

                    {/* The menu half. */}
                    <div className="pod-welcome-menu">
                        <div className="pod-welcome-eyebrow">Point it somewhere</div>

                        <div className="pod-welcome-options">
                            {POD_WELCOME_OPTIONS.map((option) => {
                                const Mark = OPTION_MARKS[option.id];
                                return (
                                    <Button
                                        key={option.id}
                                        type="button"
                                        variant="quiet"
                                        className={cn(
                                            // `hue` rather than `tone`: the row adopts
                                            // the colour for its mark without it
                                            // inheriting onto the title and the note.
                                            `lm-identity-hue-${option.tone}`,
                                            'pod-welcome-option',
                                        )}
                                        onClick={() => onStart({
                                            message: option.message,
                                            instructions: option.instructions,
                                            optionId: option.id,
                                        })}
                                    >
                                        <span className="pod-welcome-option-mark">
                                            <Mark className="h-[18px] w-[18px]" />
                                        </span>
                                        <span className="min-w-0">
                                            <span className="pod-welcome-option-title">
                                                {option.title}
                                            </span>
                                            <span className="pod-welcome-option-note">
                                                {option.note}
                                            </span>
                                        </span>
                                    </Button>
                                );
                            })}
                        </div>

                        <div className="pod-welcome-tail">
                            <form onSubmit={submitOwnWords} className="pod-welcome-field">
                                <Input
                                    value={ownWords}
                                    onChange={(event) => setOwnWords(event.target.value)}
                                    placeholder="Or say it in your own words"
                                    aria-label={`Tell ${DEFAULT_RESPONDER_NAME} what you need`}
                                    className="pod-welcome-field-input"
                                />
                                <Button
                                    type="submit"
                                    variant="quiet"
                                    size="icon"
                                    aria-label="Send"
                                    disabled={!ownWords.trim()}
                                    className="pod-welcome-send"
                                >
                                    <ArrowRight className="h-3.5 w-3.5" />
                                </Button>
                            </form>

                            {/* The way past, and it lands on what this route did
                                before the door existed: the greeting, and the
                                welcome turn. */}
                            <Button
                                type="button"
                                variant="link"
                                className="pod-welcome-skip"
                                onClick={onSkip}
                            >
                                Not now
                            </Button>
                        </div>
                    </div>
                </div>
            </DialogContent>
        </Dialog>
    );
}
