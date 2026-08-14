'use client';

import { useId, useMemo } from 'react';
import { cn } from '@/lib/utils';
import type { LemmaIcon } from '@/components/ui/icons';
import {
    APP_SCREENS,
    CRESTS,
    FORMS,
    FORM_DEPTH,
    GROUNDS,
    PIP_MIN_SIZE,
    RICH_MOTION_MIN_SIZE,
    STATE_LOOKS,
    identityGenes,
    podFields,
    type IdentityState,
} from '@/lib/identity/seeded-identity';

interface ResourceIdentityProps {
    /** Stable per resource — an id where one exists, a name only as a fallback. */
    seed: string;
    /** Read out to assistive tech; the art itself is decorative. */
    label: string;
    size?: number;
    className?: string;
    /**
     * `being` for things with agency, `mark` for everything inert, `team` for a
     * pod — which is neither, being the boundary a group of both shares.
     */
    kind?: 'being' | 'mark' | 'team';
    /** Marks only: the type's glyph, so a table still reads as a table. */
    glyph?: LemmaIcon;
    /** Beings only. */
    state?: IdentityState;
}

/**
 * The seeded tone arrives as a class rather than an inline fill, which lets
 * every shape below say `currentColor` and lets the whole palette live in CSS
 * where the appearance switch can reach it.
 */
function toneClass(tone: number): string {
    return `lm-identity-tone-${tone}`;
}

/**
 * A wide cover for an app card — the seeded stand-in for a screenshot.
 *
 * Separate from `ResourceIdentity` because it is the one identity that is not a
 * square: it fills the width it is given at a fixed 16:9, the shape a card
 * header wants and the shape a real thumbnail would arrive in.
 */
export function ResourceCover({ seed, label, className }: { seed: string; label?: string; className?: string }) {
    const genes = identityGenes(seed);
    const screen = APP_SCREENS[genes.ground % APP_SCREENS.length];
    // Mirroring costs nothing and doubles the number of distinguishable covers.
    const flip = genes.crest % 2 === 1;

    return (
        <svg
            viewBox="0 0 160 90"
            preserveAspectRatio="xMidYMid slice"
            className={cn('lm-identity-cover', toneClass(genes.tone), className)}
            role={label ? 'img' : undefined}
            aria-label={label || undefined}
            aria-hidden={label ? undefined : true}
        >
            <rect x="0" y="0" width="160" height="90" fill="var(--lm-identity-soft)" />
            {/*
              * Window chrome, common to every layout. Without it these read as
              * skeleton loaders — pastel bars on a pastel ground is exactly the
              * visual language of "content is still arriving", which is the one
              * thing an app card must not say. A title bar with three controls
              * says "this is a running app" before any of the blocks below are
              * even parsed.
              */}
            <rect x="0" y="0" width="160" height="15" fill="currentColor" opacity=".5" />
            <circle cx="9" cy="7.5" r="2.3" fill="var(--lm-identity-soft)" />
            <circle cx="17" cy="7.5" r="2.3" fill="var(--lm-identity-soft)" />
            <circle cx="25" cy="7.5" r="2.3" fill="var(--lm-identity-soft)" />
            <g
                fill="currentColor"
                transform={flip ? 'translate(160 0) scale(-1 1)' : undefined}
                dangerouslySetInnerHTML={{ __html: screen }}
            />
        </svg>
    );
}

export function ResourceIdentity({
    seed,
    label,
    size = 40,
    className,
    kind = 'being',
    glyph: Glyph,
    state = 'idle',
}: ResourceIdentityProps) {
    const genes = useMemo(() => identityGenes(seed), [seed]);
    const uid = useId().replace(/[^a-zA-Z0-9-]/g, '');

    if (kind === 'team') {
        const [first, second] = podFields(seed);
        const place = (field: typeof first) =>
            `translate(${field.cx} ${field.cy}) scale(${field.scale}) translate(-50 -50)`;
        const clipFirst = `lm-pod-${uid}`;

        return (
            <span
                className={cn('lm-identity lm-identity-mark', toneClass(genes.tone), className)}
                /* eslint-disable-next-line no-restricted-syntax -- The tile scales with the caller's size, from a 20px sidebar row to a 96px card, so its box and corner radius are runtime geometry with no fixed set of classes. */
                style={{ width: size, height: size, borderRadius: Math.round(size * 0.25) }}
                role="img"
                aria-label={label}
            >
                <svg viewBox="0 0 100 100" width={size} height={size} aria-hidden="true" className="lm-identity-ground lm-identity-team">
                    <defs>
                        <clipPath id={clipFirst}>
                            <path d={FORMS[first.form]} transform={place(first)} />
                        </clipPath>
                    </defs>
                    {/*
                      * Both fields are the pod's one hue — the first held back
                      * toward the ground it sits on, the second at full
                      * strength. `currentColor` is the tone class, so this
                      * stays a single source of colour.
                      */}
                    <path
                        d={FORMS[first.form]}
                        transform={place(first)}
                        fill="color-mix(in srgb, currentColor 60%, var(--lm-identity-soft))"
                    />
                    <path d={FORMS[second.form]} transform={place(second)} fill="currentColor" />
                    {/*
                      * The shared region, darkened rather than given a colour of
                      * its own — a fourth step down the same ramp. A separate
                      * hue here would read as a third member and reintroduce
                      * exactly the clash this composition was rebuilt to avoid.
                      */}
                    <g clipPath={`url(#${clipFirst})`}>
                        <path d={FORMS[second.form]} transform={place(second)} fill="var(--identity-shade)" opacity=".18" />
                    </g>
                </svg>
            </span>
        );
    }

    if (kind === 'mark') {
        return (
            <span
                className={cn('lm-identity lm-identity-mark', toneClass(genes.tone), className)}
                /* eslint-disable-next-line no-restricted-syntax -- The tile scales with the caller's size, from a 16px sidebar row to a 96px card, so its box and corner radius are runtime geometry with no fixed set of classes. */
                style={{ width: size, height: size, borderRadius: Math.round(size * 0.25) }}
                role="img"
                aria-label={label}
            >
                <svg viewBox="0 0 100 100" width={size} height={size} aria-hidden="true" className="lm-identity-ground">
                    <g
                        fill="currentColor"
                        stroke="none"
                        transform={`rotate(${genes.groundRotation} 50 50)`}
                        dangerouslySetInnerHTML={{ __html: GROUNDS[genes.ground] }}
                    />
                </svg>
                {Glyph ? (
                    <Glyph size={Math.round(size * 0.46)} weight="regular" className="lm-identity-glyph" />
                ) : null}
            </span>
        );
    }

    const look = STATE_LOOKS[state] ?? STATE_LOOKS.idle;
    const clipId = `lm-clip-${uid}`;
    const radius = look.eye.kind === 'wide' ? genes.eyeR * 1.24 : genes.eyeR;
    const centreY = genes.eyeY;
    const eyes = [-1, 1].map((side) => 50 + side * genes.eyeSpacing);

    return (
        <svg
            viewBox="-2 -2 104 104"
            width={size}
            height={size}
            role="img"
            aria-label={label}
            data-state={state}
            data-anim={size >= RICH_MOTION_MIN_SIZE ? 'rich' : 'plain'}
            className={cn(
                'lm-identity lm-identity-being',
                toneClass(genes.tone),
                `lm-identity-phase-${genes.phase}`,
                className,
            )}
        >
            <defs>
                <clipPath id={clipId}>
                    <path d={FORMS[genes.form]} />
                </clipPath>
            </defs>

            <g className="lm-identity-body">
                <g fill="currentColor" dangerouslySetInnerHTML={{ __html: CRESTS[genes.crest] }} />
                <path d={FORMS[genes.form]} fill="currentColor" />
                <g clipPath={`url(#${clipId})`} dangerouslySetInnerHTML={{ __html: FORM_DEPTH[genes.form] }} />
                <path d={FORMS[genes.form]} fill="none" stroke="var(--identity-shade)" strokeOpacity=".14" strokeWidth="1.5" />

                <g className="lm-identity-eyes">
                    {look.eye.kind === 'closed'
                        ? eyes.map((cx) => (
                              <path
                                  key={cx}
                                  d={`M${cx - radius} ${centreY - 2}q${radius} 9 ${radius * 2} 0`}
                                  stroke="var(--identity-sclera)"
                                  strokeWidth="4"
                                  strokeLinecap="round"
                                  fill="none"
                              />
                          ))
                        : look.eye.kind === 'line'
                          ? eyes.map((cx) => (
                                <path
                                    key={cx}
                                    d={`M${cx - radius} ${centreY}h${radius * 2}`}
                                    stroke="var(--identity-sclera)"
                                    strokeWidth="4"
                                    strokeLinecap="round"
                                    fill="none"
                                />
                            ))
                          : (
                                <>
                                    {eyes.map((cx) => (
                                        <ellipse
                                            key={cx}
                                            cx={cx}
                                            cy={centreY}
                                            rx={radius}
                                            ry={radius * 1.12}
                                            fill="var(--identity-sclera)"
                                        />
                                    ))}
                                    <g className="lm-identity-pupils">
                                        {eyes.map((cx) => (
                                            <circle
                                                key={cx}
                                                cx={cx + look.eye.pupilX * radius}
                                                cy={centreY + look.eye.pupilY * radius}
                                                r={radius * 0.46}
                                                fill="var(--identity-pupil)"
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
                <g className="lm-identity-pip">
                    <circle cx="84" cy="86" r="11" fill="var(--identity-pip-ring)" />
                    <circle cx="84" cy="86" r="7.5" fill={look.pip} />
                </g>
            ) : null}
        </svg>
    );
}
