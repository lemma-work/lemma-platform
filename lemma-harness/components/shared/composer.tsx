'use client';

import { useRef, useState, type ChangeEvent, type DragEvent, type KeyboardEvent, type ReactNode, type RefObject } from 'react';
import { cn } from '@/lib/utils';
import { ArrowUp, Plus, Square } from '@/components/ui/icons';
import { StepLoader } from '@/components/brand/loader';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { composerActionState } from '@/lib/composer/action-state';

/**
 * The box you type into. One of them, everywhere.
 *
 * There used to be two: `AssistantExperienceComposer` for conversations and
 * `/new`, and a hundred lines of bespoke JSX inline on pod home. Same job, same
 * controls, two layouts — home put everything on one row and wedged the input
 * between a `+` and the model picker, while the assistant stacked the input
 * above a control row. They also disagreed on the small things, which is how
 * you could tell: one combined runtime chip against two chips joined by a
 * literal `·`, "What should happen next?" against "Message", a filled send
 * square against a bare arrow at the far right of the box.
 *
 * This is presentational on purpose. It holds no controller and no send logic,
 * because that is exactly what kept home out of the shared component — home
 * sends through the assistant context and animates into the conversation route,
 * the assistant sends through its controller. Both can hand this a `draft`, an
 * `onSubmit` and their own controls, and neither has to adopt the other's
 * machinery to look like the same product.
 */
export interface ComposerProps {
    draft: string;
    onDraftChange: (event: ChangeEvent<HTMLTextAreaElement>) => void;
    onSubmit: () => void;
    placeholder?: string;

    /** The runtime, model and context pickers — whatever this surface offers. */
    controls?: ReactNode;
    /** Sits above the input: pending files, a plan strip, a status line. */
    header?: ReactNode;

    /** Something is running. The send button becomes stop when `onStop` is given. */
    isBusy?: boolean;
    onStop?: () => void;
    /**
     * This surface can take a message while something is already running — the
     * assistant, where a follow-up joins the run in flight. Off by default, so
     * a surface whose submit handler refuses while busy (pod home) keeps a
     * disabled Send rather than an enabled one that does nothing.
     */
    busyAcceptsSend?: boolean;
    /** Blocks sending regardless of draft — no write access, an upload in flight. */
    disabled?: boolean;
    /** Files staged with no text is still a message worth sending. */
    hasAttachments?: boolean;

    onAttach?: () => void;
    isAttaching?: boolean;
    /**
     * Files dropped anywhere on the composer. Given here rather than per
     * surface so home, `/new` and a running conversation all accept a drop —
     * they used to accept one nowhere, and the paperclip was the only way in.
     */
    onDropFiles?: (files: FileList) => void;

    onKeyDown?: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
    onSelectionChange?: () => void;
    inputRef?: RefObject<HTMLTextAreaElement | null>;

    /** `roomy` on pod home and `/new`; `tight` inside a running conversation. */
    density?: 'roomy' | 'tight';
    className?: string;
}

export function Composer({
    draft,
    onDraftChange,
    onSubmit,
    placeholder = 'What should happen next?',
    controls,
    header,
    isBusy = false,
    onStop,
    busyAcceptsSend = false,
    disabled = false,
    hasAttachments = false,
    onAttach,
    isAttaching = false,
    onDropFiles,
    onKeyDown,
    onSelectionChange,
    inputRef,
    density = 'roomy',
    className,
}: ComposerProps) {
    // Drag events fire for every child the pointer crosses, so a plain
    // enter/leave pair flickers the highlight as it moves over the input and
    // the buttons. Counting them means the state only clears when the pointer
    // has actually left the composer.
    const dragDepth = useRef(0);
    const [isDropTarget, setIsDropTarget] = useState(false);
    const acceptsDrop = Boolean(onDropFiles) && !disabled;

    const endDrag = () => {
        dragDepth.current = 0;
        setIsDropTarget(false);
    };

    const { canSend, showStop } = composerActionState({
        hasDraft: draft.trim().length > 0,
        hasAttachments,
        disabled,
        isBusy,
        busyAcceptsSend,
        canStop: Boolean(onStop),
    });

    return (
        <form
            className={cn('lm-composer', density === 'tight' && 'lm-composer-tight', className)}
            data-drop-target={isDropTarget ? 'true' : undefined}
            onSubmit={(event) => {
                event.preventDefault();
                if (canSend) onSubmit();
            }}
            onDragEnter={acceptsDrop ? (event: DragEvent<HTMLFormElement>) => {
                if (!event.dataTransfer?.types.includes('Files')) return;
                event.preventDefault();
                dragDepth.current += 1;
                setIsDropTarget(true);
            } : undefined}
            onDragOver={acceptsDrop ? (event: DragEvent<HTMLFormElement>) => {
                if (!event.dataTransfer?.types.includes('Files')) return;
                // Without this the browser navigates to the dropped file.
                event.preventDefault();
                event.dataTransfer.dropEffect = 'copy';
            } : undefined}
            onDragLeave={acceptsDrop ? () => {
                dragDepth.current -= 1;
                if (dragDepth.current <= 0) endDrag();
            } : undefined}
            onDrop={acceptsDrop ? (event: DragEvent<HTMLFormElement>) => {
                if (!event.dataTransfer?.files?.length) return;
                event.preventDefault();
                endDrag();
                onDropFiles?.(event.dataTransfer.files);
            } : undefined}
        >
            {isDropTarget ? (
                <span className="lm-composer-drop-hint" aria-hidden="true">Drop to attach</span>
            ) : null}
            {header ? <div className="lm-composer-header">{header}</div> : null}

            {/*
              * The input owns its own row and the full width of the box. On home
              * it used to share one row with the pickers, which left it about a
              * third of the width and made the model chip — bordered, filled —
              * the heaviest thing in a control whose entire purpose is the
              * sentence you are writing.
              */}
            <Textarea
                ref={inputRef}
                value={draft}
                onChange={onDraftChange}
                onKeyDown={onKeyDown}
                onKeyUp={onSelectionChange}
                onClick={onSelectionChange}
                onSelect={onSelectionChange}
                placeholder={placeholder}
                rows={1}
                disableFocusRing
                disabled={disabled && !isBusy}
                className="lm-composer-input"
            />

            <div className="lm-composer-controls">
                {onAttach ? (
                    <Button
                        type="button"
                        variant="quiet"
                        size="icon"
                        aria-label="Attach files"
                        title="Attach files"
                        onClick={onAttach}
                        disabled={disabled || isAttaching}
                        className="lm-composer-attach custom-focus-ring"
                    >
                        {isAttaching ? <StepLoader size="xs" /> : <Plus className="h-4 w-4" />}
                    </Button>
                ) : null}

                {controls ? <div className="lm-composer-slot">{controls}</div> : null}

                <span className="lm-composer-spacer" />

                {/*
                  * Send stays a filled target whether or not there is a draft.
                  * It used to drop to the quiet variant while empty, which read
                  * as disabled furniture rather than as the thing you are aiming
                  * at — and it is the one control whose position a person learns.
                  *
                  * One button, three meanings, in this order: Stop while
                  * something runs and there is nothing to send; Send the moment
                  * there is, even mid-run, on a surface that takes a follow-up;
                  * and a spinner where the surface is busy and will not take one.
                  */}
                <Button
                    type={showStop ? 'button' : 'submit'}
                    variant="quiet"
                    size="icon"
                    onClick={showStop ? onStop : undefined}
                    disabled={!showStop && !canSend}
                    aria-label={showStop ? 'Stop' : 'Send'}
                    title={showStop ? 'Stop' : 'Send'}
                    data-state={showStop ? 'busy' : canSend ? 'ready' : 'idle'}
                    className="lm-composer-send custom-focus-ring"
                >
                    {showStop ? (
                        <Square className="h-3 w-3" />
                    ) : isBusy && !canSend ? (
                        <StepLoader size="sm" />
                    ) : (
                        <ArrowUp className="h-4 w-4" />
                    )}
                </Button>
            </div>
        </form>
    );
}
