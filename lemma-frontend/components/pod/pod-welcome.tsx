'use client';

import { useState } from 'react';

import { POD_WELCOME_ART } from '@/components/pod/pod-welcome-art';
import { ResourceIdentity } from '@/components/shared/resource-identity';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { ArrowRight } from '@/components/ui/icons';
import { LEM_SEED } from '@/lib/identity/seeded-identity';
import {
    POD_WELCOME_OPTIONS,
    POD_WELCOME_OWN_WORDS_INSTRUCTIONS,
    POD_WELCOME_SURPRISE,
    type PodWelcomeOptionId,
} from '@/lib/pods/pod-welcome';
import { DEFAULT_RESPONDER_NAME } from '@/lib/utils/agents';
import { cn } from '@/lib/utils';

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
                    <aside className="lm-identity-hue-0 flex flex-col justify-center gap-[18px] border-b border-[color:var(--border-subtle)] bg-[color:color-mix(in_srgb,var(--lm-identity-soft)_62%,var(--surface-1))] p-7 sm:border-b-0 sm:border-r">
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
                                below that offers to hire one — read as the
                                natural next thought rather than as an upsell.
                                It also drops a split where a fifth pod was being
                                told it was somebody's first. */}
                            <DialogTitle className="text-[21px] font-medium leading-[1.2] tracking-[-0.012em] text-[var(--text-primary)] text-balance">
                                {DEFAULT_RESPONDER_NAME} is the first agent here.
                            </DialogTitle>
                            {/* "Answers where you already chat" was the weakest
                                true thing we could have said: it makes Lem sound
                                like a support desk waiting to be asked. The
                                point of a surface is the opposite — once it is
                                on Telegram it can start the conversation. */}
                            <DialogDescription className="mt-2.5 text-[13.5px] leading-[1.55] text-[var(--text-secondary)]">
                                It builds what you ask for, and it can work from
                                Telegram or Slack.
                            </DialogDescription>
                        </div>

                        {/* The one control that asks for nothing — no decision,
                            no sentence — so it belongs with the introduction
                            rather than at the end of a list of decisions. */}
                        <button
                            type="button"
                            onClick={() => onStart({
                                message: POD_WELCOME_SURPRISE.message,
                                instructions: POD_WELCOME_SURPRISE.instructions,
                                optionId: POD_WELCOME_SURPRISE.id,
                            })}
                            className="group mt-1 flex w-fit items-center gap-2 rounded-[var(--radius-md)] border border-[color:color-mix(in_srgb,var(--lm-identity-tone)_32%,var(--border-default))] bg-[var(--surface-1)] px-3 py-2 text-[13.5px] text-[var(--text-primary)] shadow-[var(--shadow-raised-quiet)] transition-gentle hover:border-[color:var(--lm-identity-tone)] focus-ring"
                        >
                            {POD_WELCOME_SURPRISE.title}
                            <ArrowRight className="h-3.5 w-3.5 text-[var(--lm-identity-tone)] transition-gentle group-hover:translate-x-0.5" />
                        </button>
                    </aside>

                    {/* The menu half. */}
                    <div className="flex flex-col gap-3 p-5">
                        <div className="pr-7 text-xs uppercase tracking-[0.07em] text-[var(--text-soft)]">
                            Point it somewhere
                        </div>

                        <div className="flex flex-col gap-1">
                            {POD_WELCOME_OPTIONS.map((option) => {
                                const Mark = POD_WELCOME_ART[option.id];
                                return (
                                    <button
                                        key={option.id}
                                        type="button"
                                        onClick={() => onStart({
                                            message: option.message,
                                            instructions: option.instructions,
                                            optionId: option.id,
                                        })}
                                        className={cn(
                                            // `hue` rather than `tone`: the row adopts
                                            // the colour for its tile without it
                                            // inheriting onto the title and the note.
                                            `lm-identity-hue-${option.tone}`,
                                            'group flex items-center gap-3 rounded-[var(--radius-md)] border border-transparent p-2 text-left transition-gentle hover:border-[color:var(--border-subtle)] hover:bg-[var(--surface-2)] focus-ring',
                                        )}
                                    >
                                        <span className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-[var(--radius-md)] bg-[color:color-mix(in_srgb,var(--lm-identity-tone)_15%,var(--surface-1))] text-[var(--lm-identity-tone)] transition-gentle group-hover:bg-[color:color-mix(in_srgb,var(--lm-identity-tone)_24%,var(--surface-1))]">
                                            <Mark />
                                        </span>
                                        <span className="min-w-0">
                                            <span className="block text-[13.5px] text-[var(--text-primary)]">
                                                {option.title}
                                            </span>
                                            <span className="block text-xs text-[var(--text-tertiary)]">
                                                {option.note}
                                            </span>
                                        </span>
                                    </button>
                                );
                            })}
                        </div>

                        <div className="mt-auto flex flex-col gap-2 pt-1">
                            <form onSubmit={submitOwnWords} className="flex items-center gap-2.5 rounded-[var(--radius-md)] border border-[color:var(--border-subtle)] bg-[var(--surface-2)] px-3 py-2 focus-within:border-[color:color-mix(in_srgb,var(--action-primary)_55%,var(--border-default))]">
                                <input
                                    type="text"
                                    value={ownWords}
                                    onChange={(event) => setOwnWords(event.target.value)}
                                    placeholder="Or say it in your own words"
                                    aria-label={`Tell ${DEFAULT_RESPONDER_NAME} what you need`}
                                    className="min-w-0 flex-1 bg-transparent text-[13.5px] text-[var(--text-primary)] outline-none placeholder:text-[var(--text-soft)]"
                                />
                                <button
                                    type="submit"
                                    aria-label="Send"
                                    disabled={!ownWords.trim()}
                                    className="grid h-[26px] w-[26px] shrink-0 place-items-center rounded-full border border-[color:var(--border-subtle)] bg-[var(--surface-1)] text-[var(--text-tertiary)] transition-gentle hover:border-[color:var(--action-primary)] hover:text-[var(--action-primary)] disabled:opacity-50 disabled:hover:border-[color:var(--border-subtle)] disabled:hover:text-[var(--text-tertiary)] focus-ring"
                                >
                                    <ArrowRight className="h-3.5 w-3.5" />
                                </button>
                            </form>

                            {/* The way past, and it lands on what this route did
                                before the door existed: the greeting, and the
                                welcome turn. */}
                            <button
                                type="button"
                                onClick={onSkip}
                                className="w-fit text-xs text-[var(--text-soft)] underline decoration-[color:var(--border-default)] underline-offset-[3px] transition-gentle hover:text-[var(--text-secondary)] focus-ring"
                            >
                                Not now
                            </button>
                        </div>
                    </div>
                </div>
            </DialogContent>
        </Dialog>
    );
}
