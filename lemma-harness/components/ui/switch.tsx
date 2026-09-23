import * as React from "react";
import { cn } from "@/lib/utils";

export interface SwitchProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "role" | "aria-checked"> {
    checked?: boolean;
    onCheckedChange?: (checked: boolean) => void;
}

const Switch = React.forwardRef<HTMLButtonElement, SwitchProps>(
    ({ className, checked = false, disabled, onClick, onCheckedChange, type = "button", ...props }, ref) => {
        return (
            <button
                type={type}
                role="switch"
                aria-checked={checked}
                className={cn("ui-switch", className)}
                disabled={disabled}
                onClick={(event) => {
                    onClick?.(event);
                    if (!event.defaultPrevented && !disabled) {
                        onCheckedChange?.(!checked);
                    }
                }}
                ref={ref}
                {...props}
            />
        );
    }
);
Switch.displayName = "Switch";

const SwitchTrack = React.forwardRef<HTMLSpanElement, React.HTMLAttributes<HTMLSpanElement>>(
    ({ className, ...props }, ref) => (
        <span
            className={cn("relative inline-flex h-5 w-9 items-center rounded-full bg-[var(--bg-muted)] transition-colors", className)}
            ref={ref}
            {...props}
        />
    )
);
SwitchTrack.displayName = "SwitchTrack";

const SwitchThumb = React.forwardRef<HTMLSpanElement, React.HTMLAttributes<HTMLSpanElement>>(
    ({ className, ...props }, ref) => (
        <span
            className={cn("inline-block h-4 w-4 translate-x-0.5 rounded-full bg-[var(--surface-1)] shadow-[var(--shadow-xs)] transition-transform", className)}
            ref={ref}
            {...props}
        />
    )
);
SwitchThumb.displayName = "SwitchThumb";

export { Switch, SwitchTrack, SwitchThumb };
