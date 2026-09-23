import { memo, useId, useMemo } from "react";
import {
    CRESTS,
    FORMS,
    FORM_DEPTH,
    PIP_MIN_SIZE,
    RICH_MOTION_MIN_SIZE,
    STATE_LOOKS,
    identityGenes,
    type IdentityState,
} from "@/identity/seeded-identity";
import {
    IDENTITY_PUPIL,
    IDENTITY_SCLERA,
    IDENTITY_SHADE,
    toneColor,
} from "@/identity/palette";

export const Being = memo(function Being({
    seed,
    label,
    size = 40,
    state = "idle",
    muted,
    className,
}: {
    seed: string;
    label?: string;
    size?: number;
    state?: IdentityState;
    /** Drawn in ink rather than in its own tone — for a creature that is not
     *  anybody yet. A face in full colour is a claim about whose it is. */
    muted?: boolean;
    className?: string;
}) {
    const genes = useMemo(() => identityGenes(seed), [seed]);
    const uid = useId().replace(/[^a-zA-Z0-9-]/g, "");
    const look = STATE_LOOKS[state] ?? STATE_LOOKS.idle;
    const clipId = "being-" + uid;

    const radius = look.eye.kind === "wide" ? genes.eyeR * 1.24 : genes.eyeR;
    const centreY = genes.eyeY;
    const eyes = [-1, 1].map((side) => 50 + side * genes.eyeSpacing);
    const rich = size >= RICH_MOTION_MIN_SIZE;

    return (
        <svg
            viewBox="-2 -2 104 104"
            width={size}
            height={size}
            role={label ? "img" : undefined}
            aria-label={label}
            aria-hidden={label ? undefined : true}
            data-anim={rich ? "rich" : "plain"}
            /* Every creature on a page starting its cycle at mount reads as the
               page glitching rather than as a pod of people, so each one is
               dealt a phase and begins mid-breath. */
            style={{
                color: muted ? "var(--ink-2)" : toneColor(genes.tone),
                opacity: muted ? 0.55 : look.opacity,
                ["--phase" as string]: -0.65 * genes.phase + "s",
            }}
            className={"being" + (className ? " " + className : "")}
        >
            <defs>
                <clipPath id={clipId}>
                    <path d={FORMS[genes.form]} />
                </clipPath>
            </defs>

            <g className="being__body">
                <g fill="currentColor" dangerouslySetInnerHTML={{ __html: CRESTS[genes.crest] }} />
                <path d={FORMS[genes.form]} fill="currentColor" />
                <g clipPath={"url(#" + clipId + ")"} dangerouslySetInnerHTML={{ __html: FORM_DEPTH[genes.form] }} />
                <path
                    d={FORMS[genes.form]}
                    fill="none"
                    stroke={IDENTITY_SHADE}
                    strokeOpacity=".14"
                    strokeWidth="1.5"
                />

                <g className="being__eyes">
                    {look.eye.kind === "closed" ? (
                        eyes.map((cx) => (
                            <path
                                key={cx}
                                d={"M" + (cx - radius) + " " + (centreY - 2) + "q" + radius + " 9 " + radius * 2 + " 0"}
                                stroke={IDENTITY_SCLERA}
                                strokeWidth="4"
                                strokeLinecap="round"
                                fill="none"
                            />
                        ))
                    ) : look.eye.kind === "line" ? (
                        eyes.map((cx) => (
                            <path
                                key={cx}
                                d={"M" + (cx - radius) + " " + centreY + "h" + radius * 2}
                                stroke={IDENTITY_SCLERA}
                                strokeWidth="4"
                                strokeLinecap="round"
                                fill="none"
                            />
                        ))
                    ) : (
                        <>
                            {eyes.map((cx) => (
                                <ellipse
                                    key={cx}
                                    cx={cx}
                                    cy={centreY}
                                    rx={radius}
                                    ry={radius * 1.12}
                                    fill={IDENTITY_SCLERA}
                                />
                            ))}
                            <g className="being__pupils">
                                {eyes.map((cx) => (
                                    <circle
                                        key={cx}
                                        cx={cx + look.eye.pupilX * radius}
                                        cy={centreY + look.eye.pupilY * radius}
                                        r={radius * 0.46}
                                        fill={IDENTITY_PUPIL}
                                    />
                                ))}
                            </g>
                            {look.eye.lid > 0
                                ? eyes.map((cx) => (
                                      <rect
                                          key={cx}
                                          x={cx - radius - 1}
                                          y={centreY - radius * 1.12 - 1}
                                          width={radius * 2 + 2}
                                          height={radius * 2.24 * look.eye.lid}
                                          fill="currentColor"
                                      />
                                  ))
                                : null}
                        </>
                    )}
                </g>
            </g>

            {look.pip && size >= PIP_MIN_SIZE ? (
                <g className="being__pip">
                    <circle cx="84" cy="86" r="11" fill="var(--paper)" />
                    <circle cx="84" cy="86" r="7.5" fill={pipColour(look.pip)} />
                </g>
            ) : null}
        </svg>
    );
});

/** The platform names its pip colours as custom properties from its own state
 *  ramp; this app has no such ramp, so the four are stated here. */
function pipColour(token: string): string {
    if (token.includes("success")) return "#1f8f5f";
    if (token.includes("warning")) return "var(--wait)";
    if (token.includes("error")) return "#c22f15";
    return "var(--accent)";
}
