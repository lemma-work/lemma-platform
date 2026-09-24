"use client";

import { useMyLimits } from "./queries";
import { allowanceOf, formatPercent, resetsIn } from "./allowance";
import { offered, paying } from "@/billing/plan";
import { useMyPlan, usePlans } from "@/billing/queries";
import { UpgradeIcon } from "@/ui/icons";
import { isLocalDeployment } from "@/site/config";

export function AllowanceNote({
    orgId,
    compact = false,
    onOpenPlan,
}: {
    orgId?: string | null;
    compact?: boolean;
    onOpenPlan: () => void;
}) {
    /* A failure here is not worth a word. The allowance is a courtesy ahead of
       an error the run itself will give properly; an app that cannot read it
       should say nothing rather than claim a limit it does not know. */
    const limits = useMyLimits(orgId);
    const mine = useMyPlan();
    const plans = usePlans();

    const state = allowanceOf(limits.data);
    const blocked = state.kind === "blocked";
    const warning = blocked || (state.kind === "within" && state.warn);

    const theirsToPay = limits.data?.plan_type === "TEAM";
    const somethingToSell = offered(plans.data, "PERSONAL").length > 0;
    const canUpgrade = !theirsToPay && !paying(mine.data) && somethingToSell;

    /* Nothing to say: paying already, or covered by an organization, and not
       near a limit. A local installation has no plans to open either -- its
       settings do not have that section. */
    if (isLocalDeployment() || (!warning && !canUpgrade)) return null;

    const window = state.kind === "uncapped" ? null : state.window;
    const tone = blocked ? "bad" : warning ? "warn" : "quiet";
    const percent = window ? formatPercent(window.used_percent, window.allowed) : "";
    const resets = window ? resetsIn(window.reset_at) : "";
    const planName = mine.data?.plan?.name ?? limits.data?.plan_name ?? "Free plan";

    if (compact) {
        /* The narrow rail cannot fit a sentence. A warning keeps its dot —
           losing the signal entirely is worse than losing the words — and the
           offer becomes the one thing it can be at 64px, which is a control
           with a real name on it rather than a dot nobody can click. */
        if (warning) {
            return (
                <button
                    className="allowance allowance--compact"
                    data-tone={tone}
                    onClick={onOpenPlan}
                    title={(blocked ? "Usage limit reached — " : "") + (window?.label ?? "") + " " + percent + (resets ? " · " + resets : "")}
                >
                    <span className="allowance__dot" aria-hidden="true" />
                    <span className="sr-only">
                        {blocked ? "Usage limit reached. " : ""}{window?.label} {percent}. Open plans.
                    </span>
                </button>
            );
        }
        return (
            <button
                className="allowance allowance--compact allowance--offer"
                data-tone="quiet"
                onClick={onOpenPlan}
                title={planName + " — upgrade"}
            >
                <UpgradeIcon size={18} />
                <span className="sr-only">On {planName}. Upgrade your plan.</span>
            </button>
        );
    }

    return (
        <div className="allowance" data-tone={tone} role={warning ? "status" : undefined}>
            <div className="allowance__line">
                <span className="allowance__what">
                    {blocked ? "Usage limit reached" : warning ? window?.label : planName}
                </span>
                {percent && <span className="allowance__much">{percent}</span>}
            </div>

            {/* The bar is capped at full width: past the limit the number keeps
                climbing and a bar that kept pace would leave the box. */}
            {window && (
                <span className="allowance__bar" aria-hidden="true">
                    <i style={{ width: Math.min(100, Math.max(0, window.used_percent)) + "%" }} />
                </span>
            )}

            <small>
                {warning ? (
                    <>
                        {blocked ? window?.label : null}
                        {blocked && resets ? " · " : null}
                        {resets}
                    </>
                ) : (
                    /* Uncapped and free is a real combination — a deployment
                       that states no limits still sells plans — so the line
                       says what the plan is rather than inventing a ceiling. */
                    window ? resets || "Free plan" : "No limit set on this account"
                )}
            </small>

            {canUpgrade ? (
                <button className="allowance__act" onClick={onOpenPlan}>
                    <UpgradeIcon size={15} /> Upgrade
                </button>
            ) : (
                <button className="allowance__act" onClick={onOpenPlan}>See plans</button>
            )}
        </div>
    );
}
