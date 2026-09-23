import type { Plan, Subscription } from "./plan";

const monthly = (slug: string, credits: number, seat: boolean, lines: string[]) => ({
    slug,
    audience: seat ? "team" : "personal",
    billing_mode: "fixed_subscription",
    billing_interval: "MONTHLY",
    included_llm_credits_cents: credits,
    highlights: lines,
    ...(seat ? { price_unit: "seat", remove_lemma_branding: true } : { seats: 1 }),
});

function plan(
    id: string,
    name: string,
    description: string,
    priceCents: number,
    audience: "PERSONAL" | "TEAM",
    features: Record<string, unknown>,
): Plan {
    return {
        id,
        name,
        description,
        plan_type: audience,
        price_cents: priceCents,
        currency: "USD",
        features,
        seat_limit: audience === "PERSONAL" ? 1 : null,
        usage_limits: {},
        is_active: true,
    };
}

const PERSONAL = [
    "Fair usage for everyday agent work",
    "Usage shown as a percentage of your limit",
    "Your own private workspace",
];
const TEAM = [
    "Usage shown as a percentage of your limit",
    "Shared team workspace and pods",
    "Your own branding, not ours",
];

export const samplePlans: Plan[] = [
    plan("00000000-0000-4000-8000-00000000f000", "Free", "Default free personal plan",
        0, "PERSONAL", { billing_mode: "fixed_subscription" }),
    plan("00000000-0000-4000-8000-000000000001", "Lemma Personal",
        "For individuals. Fair LLM and compute sandbox usage for everyday agent work.",
        1000, "PERSONAL", monthly("personal", 1000, false, PERSONAL)),
    plan("00000000-0000-4000-8000-000000000002", "Lemma Personal Plus",
        "For individuals. Higher LLM and compute sandbox usage, for longer and heavier agent runs.",
        2500, "PERSONAL", monthly("personal_plus", 3000, false,
            ["Higher limits — around three times Starter", ...PERSONAL.slice(1)])),
    plan("00000000-0000-4000-8000-000000000003", "Lemma Personal Max",
        "For individuals. Our highest LLM and compute sandbox usage, for continuous heavy work.",
        20000, "PERSONAL", monthly("personal_max", 30000, false,
            ["Our highest limits — around ten times Plus", ...PERSONAL.slice(1)])),
    plan("00000000-0000-4000-8000-000000000004", "Lemma Team",
        "For teams. Fair LLM and compute sandbox usage for everyday agent work, billed per seat.",
        1000, "TEAM", monthly("team", 1000, true, ["Fair usage for everyday agent work", ...TEAM])),
    plan("00000000-0000-4000-8000-000000000005", "Lemma Team Plus",
        "For teams. Higher LLM and compute sandbox usage, for longer and heavier agent runs. Billed per seat.",
        2500, "TEAM", monthly("team_plus", 3000, true, ["Higher limits — around three times Starter", ...TEAM])),
    plan("00000000-0000-4000-8000-000000000006", "Lemma Team Max",
        "For teams. Our highest LLM and compute sandbox usage, for continuous heavy work. Billed per seat.",
        20000, "TEAM", monthly("team_max", 30000, true, ["Our highest limits — around ten times Plus", ...TEAM])),
    plan("00000000-0000-4000-8000-000000000007", "Lemma Enterprise",
        "For large organizations. Custom usage, security and support — talk to us.",
        0, "TEAM", {
            slug: "enterprise", audience: "team", billing_mode: "contact_sales",
            billing_interval: "MONTHLY", price_unit: "custom", included_llm_credits_cents: 0,
            highlights: [
                "Custom LLM and compute allowance",
                "SSO and advanced audit controls",
                "Custom contracts and dedicated support",
            ],
        }),
];

/** Free, which is where the sample account sits — and the state the rest of the
 *  app is drawn against, because it is the one most people are in. */
export const sampleSubscription: Subscription = {
    id: "00000000-0000-4000-8000-0000000000aa",
    user_id: "00000000-0000-4000-8000-0000000000bb",
    organization_id: null,
    plan_id: "00000000-0000-4000-8000-00000000f000",
    plan: samplePlans[0],
    status: "active",
    dodo_subscription_id: null,
    current_period_start: null,
    current_period_end: null,
    seat_count: 1,
    cancel_at_period_end: false,
};
