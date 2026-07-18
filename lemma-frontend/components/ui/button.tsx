import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { StepLoader } from "@/components/brand/loader";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
    "tap-target inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium tracking-normal transition-gentle focus-ring disabled:pointer-events-none disabled:opacity-50",
    {
        variants: {
            variant: {
                default:
                    "border border-[color:color-mix(in_srgb,var(--button-primary-bg)_82%,var(--text-primary))] bg-[var(--button-primary-bg)] text-[var(--button-primary-fg)] shadow-[var(--shadow-xs)] hover:bg-[var(--button-primary-bg-hover)] active:brightness-[0.98]",
                primary:
                    "border border-[color:color-mix(in_srgb,var(--button-primary-bg)_82%,var(--text-primary))] bg-[var(--button-primary-bg)] text-[var(--button-primary-fg)] shadow-[var(--shadow-xs)] hover:bg-[var(--button-primary-bg-hover)] active:brightness-[0.98]",
                secondary:
                    "border border-[color:var(--button-secondary-border)] bg-[var(--button-secondary-bg)] text-[var(--button-secondary-fg)] shadow-none hover:border-[var(--border-strong)] hover:bg-[var(--button-secondary-bg-hover)]",
                ghost:
                    "bg-transparent text-[var(--text-tertiary)] hover:bg-[var(--surface-2)] hover:text-[var(--text-primary)]",
                outline:
                    "border border-[color:var(--button-secondary-border)] bg-[var(--button-secondary-bg)] text-[var(--text-primary)] hover:border-[var(--border-strong)] hover:bg-[var(--button-secondary-bg-hover)]",
                accent:
                    "border border-[color:var(--button-accent-border)] bg-[var(--button-accent-bg)] text-[var(--button-accent-fg)] shadow-[var(--shadow-xs)] hover:border-[var(--delight)] hover:text-[var(--delight)] active:translate-y-px",
                destructive:
                    "border border-tone-error bg-[var(--state-error)] text-[var(--text-on-brand)] shadow-sm hover:brightness-95",
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
            variant: "primary",
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
