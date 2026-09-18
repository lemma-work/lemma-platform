"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { useAwaitActivation } from "@/lib/billing/use-billing";

/**
 * What the customer sees on the way back from hosted checkout.
 *
 * The redirect proves nothing: the payment provider activates a subscription by
 * webhook, which lands seconds later. So this waits for the status to actually
 * move rather than congratulating anyone on the strength of a query parameter,
 * and says plainly what is happening while it waits.
 *
 * Uses the same `state-surface-*` notice language as the rest of the app rather
 * than a bespoke card, so a billing page does not announce things differently
 * from every other screen.
 */
export function CheckoutReturn({
    outcome,
    scope,
    onDismiss,
}: {
    outcome: "success" | "cancelled" | null;
    scope: { kind: "personal" } | { kind: "organization"; organizationId: string };
    onDismiss: () => void;
}) {
    const awaiting = useAwaitActivation(scope, outcome === "success");
    const [waitedTooLong, setWaitedTooLong] = useState(false);

    useEffect(() => {
        if (outcome !== "success") return;
        const timer = setTimeout(() => setWaitedTooLong(true), 60_000);
        return () => clearTimeout(timer);
    }, [outcome]);

    if (!outcome) return null;

    const active = awaiting.data?.status === "active";
    // The poll gives up after about a minute. Past that, "activating…" would be
    // a spinner that never resolves, so say what is actually true: the payment
    // landed, and the webhook finishes the job without them.
    const stalled = !active && waitedTooLong;

    const tone =
        outcome === "cancelled"
            ? "state-surface-info"
            : active
              ? "state-surface-success"
              : "state-surface-running";

    const message =
        outcome === "cancelled"
            ? "Checkout was cancelled. Nothing has been charged."
            : active
              ? "Payment received — your plan is active."
              : stalled
                ? "Payment received. Activation is taking longer than usual; it will finish on its own and this page will show the new plan."
                : "Payment received. Activating your plan…";

    return (
        <aside
            role="status"
            aria-live="polite"
            className={`${tone} flex flex-wrap items-center justify-between gap-3 rounded-md px-4 py-3 text-sm`}
        >
            <span>{message}</span>
            {outcome === "cancelled" || active ? (
                <Button variant="secondary" size="sm" className="shrink-0" onClick={onDismiss}>
                    Dismiss
                </Button>
            ) : null}
        </aside>
    );
}
