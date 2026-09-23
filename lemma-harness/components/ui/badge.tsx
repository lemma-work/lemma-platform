import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
    "chip chip-sm transition-gentle",
    {
        variants: {
            variant: {
                default: "",
                secondary: "",
                outline: "chip-quiet text-[var(--text-secondary)]",
                primary: "border-[color:var(--button-secondary-border)] bg-[var(--button-secondary-bg)] text-[var(--text-primary)]",
                brand: "state-badge-brand",
                destructive: "state-badge-error",
                success: "state-badge-success",
                warning: "state-badge-warning",
                error: "state-badge-error",
                info: "state-badge-info",
            },
        },
        defaultVariants: {
            variant: "default",
        },
    }
);

export interface BadgeProps
    extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> { }

const Badge = React.forwardRef<HTMLDivElement, BadgeProps>(({ className, variant, ...props }, ref) => {
    return (
        <div
            ref={ref}
            className={cn(badgeVariants({ variant }), className)}
            {...props}
        />
    );
});
Badge.displayName = "Badge";

export { Badge, badgeVariants };
