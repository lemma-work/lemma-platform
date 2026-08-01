import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
import { StepLoader } from "@/components/brand/loader";

/**
 * Buttons are surfaces you press, so they are lit like surfaces: a hairline that
 * is darker than the fill, a one-pixel highlight along the top edge, and a
 * shadow small enough to read as "raised" rather than "floating". Pressing
 * inverts that — the highlight goes, an inset shadow arrives, and the whole
 * thing drops a pixel. Flat rectangles with a border are what these were.
 */
const RAISED_SOLID = "shadow-[var(--shadow-raised-solid)] active:translate-y-px active:shadow-[var(--shadow-pressed-solid)]";
const RAISED_QUIET = "shadow-[var(--shadow-raised-quiet)] active:translate-y-px active:shadow-[var(--shadow-pressed-quiet)]";

/**
 * Variants name a ROLE, not an appearance.
 *
 * Naming them after how they look (outline, ghost) made every call site a fresh
 * aesthetic judgement, which is why `outline` and `secondary` drifted into being
 * the same button under two names. Pick by what the action IS:
 *
 *   primary     the one action this view exists for — at most one per view
 *   secondary   a real alternative to the primary action
 *   quiet       utilities, row actions, dismissals — present but not competing
 *   destructive removes or revokes something
 *   link        an action that belongs inside a sentence
 *
 * `secondary` is the default on purpose: nothing should read as the primary
 * call to action without someone having said so.
 */
const buttonVariants = cva(
    "tap-target inline-flex select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-sm font-medium tracking-normal transition-gentle focus-ring disabled:pointer-events-none disabled:opacity-50 disabled:shadow-none",
    {
        variants: {
            variant: {
                primary:
                    `border border-[color:color-mix(in_srgb,var(--button-primary-bg)_84%,black)] bg-[var(--button-primary-bg)] text-[var(--button-primary-fg)] hover:bg-[var(--button-primary-bg-hover)] ${RAISED_SOLID}`,
                secondary:
                    `border border-[color:var(--button-secondary-border)] bg-[var(--button-secondary-bg)] text-[var(--button-secondary-fg)] hover:border-[color:var(--border-strong)] hover:bg-[var(--button-secondary-bg-hover)] ${RAISED_QUIET}`,
                // Uses var(--text-secondary), not var(--text-tertiary): tertiary on
                // canvas is ~2.6:1, which reads as disabled and fails the 3:1 floor
                // for controls.
                quiet:
                    "bg-transparent text-[var(--text-secondary)] hover:bg-[var(--surface-2)] hover:text-[var(--text-primary)] active:bg-[var(--surface-3)]",
                destructive:
                    `border border-[color:var(--button-destructive-border)] bg-[var(--state-error)] text-[var(--text-on-brand)] hover:brightness-95 ${RAISED_SOLID}`,
                link:
                    "bg-transparent px-0 text-[var(--action-primary)] underline-offset-4 hover:underline",
            },
            size: {
                default: "h-9 px-3.5 text-sm",
                xs: "h-7 px-2.5 text-xs",
                sm: "h-8 px-3 text-sm",
                md: "h-9 px-3.5 text-sm",
                lg: "h-10 px-4 text-sm",
                icon: "h-9 w-9",
            },
        },
        defaultVariants: {
            variant: "secondary",
            size: "md",
        },
    }
);

export interface ButtonProps
    extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
    asChild?: boolean;
    loading?: boolean;
    loadingLabel?: React.ReactNode;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
    ({ className, variant, size, asChild = false, loading = false, loadingLabel, disabled, children, ...props }, ref) => {
        const Comp = asChild ? Slot : "button";
        const isLoading = loading && !asChild;

        return (
            <Comp
                className={cn(buttonVariants({ variant, size, className }))}
                ref={ref}
                disabled={disabled || isLoading}
                aria-busy={isLoading || undefined}
                data-loading={isLoading ? "true" : undefined}
                {...props}
            >
                {isLoading ? (
                    <>
                        <StepLoader size={size === "xs" ? "xs" : "sm"} className="mr-2 text-current" />
                        {loadingLabel ?? children}
                    </>
                ) : (
                    children
                )}
            </Comp>
        );
    }
);
Button.displayName = "Button";

export { Button, buttonVariants };
