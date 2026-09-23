import * as React from "react";
import { cn } from "@/lib/utils";

export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
    disableFocusRing?: boolean;
}

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
    ({ className, disableFocusRing = false, ...props }, ref) => {
        return (
            <textarea
                className={cn(
                    "form-field-control flex min-h-[96px] w-full px-3 py-2.5 text-sm text-[var(--text-primary)] outline-none",
                    "placeholder:text-[var(--text-soft)]",
                    disableFocusRing && "focus-visible:transform-none focus-visible:shadow-none",
                    "disabled:cursor-not-allowed disabled:opacity-50",
                    className
                )}
                ref={ref}
                {...props}
            />
        );
    }
);
Textarea.displayName = "Textarea";

export { Textarea };
