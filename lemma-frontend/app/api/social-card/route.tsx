/* eslint-disable no-restricted-syntax -- Next ImageResponse renders with Satori, so its 1200x630 layout requires serializable inline styles rather than app CSS. */
import { ImageResponse } from 'next/og';
import type { NextRequest } from 'next/server';

import {
    resolveSocialCardCopy,
    SOCIAL_CARD_COLORS,
    SOCIAL_CARD_HEIGHT,
    SOCIAL_CARD_WIDTH,
} from '@/lib/share/social-card';

export const runtime = 'edge';

export function GET(request: NextRequest) {
    const colors = SOCIAL_CARD_COLORS;
    const copy = resolveSocialCardCopy({
        variant: request.nextUrl.searchParams.get('variant'),
        title: request.nextUrl.searchParams.get('title'),
        detail: request.nextUrl.searchParams.get('detail'),
        label: request.nextUrl.searchParams.get('label'),
    });

    return new ImageResponse(
        (
            <div
                style={{
                    width: '100%',
                    height: '100%',
                    display: 'flex',
                    position: 'relative',
                    overflow: 'hidden',
                    background: colors.paper,
                    color: colors.ink,
                    fontFamily: 'Arial, Helvetica, sans-serif',
                }}
            >
                <div
                    style={{
                        width: 790,
                        height: '100%',
                        padding: '56px 64px 42px',
                        display: 'flex',
                        flexDirection: 'column',
                    }}
                >
                    <div style={{ display: 'flex', alignItems: 'flex-end', height: 36 }}>
                        <div style={{ width: 10, height: 14, borderRadius: 2, background: colors.ink }} />
                        <div style={{ width: 10, height: 24, borderRadius: 2, marginLeft: 5, background: colors.ink }} />
                        <div style={{ width: 10, height: 34, borderRadius: 2, marginLeft: 5, background: colors.ink }} />
                        <div style={{ marginLeft: 16, fontSize: 27, fontWeight: 700, letterSpacing: -1 }}>Lemma</div>
                    </div>

                    <div
                        style={{
                            marginTop: 88,
                            fontFamily: 'monospace',
                            color: colors.muted,
                            fontSize: 18,
                            fontWeight: 700,
                            letterSpacing: 3,
                        }}
                    >
                        {copy.eyebrow}
                    </div>
                    <div
                        style={{
                            marginTop: 28,
                            maxWidth: 680,
                            fontSize: copy.title.length > 34 ? 66 : 78,
                            fontWeight: 700,
                            letterSpacing: -3.5,
                            lineHeight: 0.98,
                        }}
                    >
                        {copy.title}
                    </div>
                    <div
                        style={{
                            marginTop: 24,
                            maxWidth: 650,
                            color: colors.muted,
                            fontSize: 27,
                            lineHeight: 1.25,
                        }}
                    >
                        {copy.detail}
                    </div>

                    <div
                        style={{
                            marginTop: 'auto',
                            paddingTop: 24,
                            borderTop: `2px solid ${colors.rule}`,
                            color: colors.ink,
                            fontFamily: 'monospace',
                            fontSize: 16,
                        }}
                    >
                        {copy.label}
                    </div>
                </div>

                <div
                    style={{
                        width: 410,
                        height: '100%',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        background: colors.panel,
                    }}
                >
                    <div
                        style={{
                            width: 278,
                            height: 438,
                            border: `2px solid ${colors.border}`,
                            borderRadius: 28,
                            padding: 32,
                            display: 'flex',
                            flexDirection: 'column',
                            background: colors.card,
                        }}
                    >
                        <div style={{ width: 112, height: 14, borderRadius: 7, background: colors.ink }} />
                        <div style={{ width: 174, height: 8, borderRadius: 4, marginTop: 12, background: colors.placeholder }} />
                        {[
                            [colors.greenSoft, colors.greenDot, colors.greenLine],
                            [colors.blueSoft, colors.blueDot, colors.blueLine],
                        ].map(([background, dot, line], index) => (
                            <div
                                key={background}
                                style={{
                                    height: 84,
                                    marginTop: index === 0 ? 38 : 24,
                                    padding: 22,
                                    display: 'flex',
                                    alignItems: 'flex-start',
                                    borderRadius: 16,
                                    background,
                                    border: `1px solid ${index === 0 ? colors.greenBorder : colors.blueBorder}`,
                                }}
                            >
                                <div style={{ width: 20, height: 20, borderRadius: 10, background: dot }} />
                                <div style={{ marginLeft: 18, display: 'flex', flexDirection: 'column' }}>
                                    <div style={{ width: 112, height: 10, borderRadius: 5, background: line }} />
                                    <div style={{ width: 88, height: 7, borderRadius: 4, marginTop: 10, opacity: 0.45, background: line }} />
                                </div>
                            </div>
                        ))}
                        <div
                            style={{
                                height: 74,
                                marginTop: 24,
                                padding: '24px 24px',
                                display: 'flex',
                                alignItems: 'center',
                                borderRadius: 16,
                                background: colors.ink,
                            }}
                        >
                            <div style={{ width: 104, height: 10, borderRadius: 5, background: colors.card }} />
                            <div
                                style={{
                                    width: 26,
                                    height: 26,
                                    marginLeft: 'auto',
                                    borderRadius: 13,
                                    display: 'flex',
                                    alignItems: 'center',
                                    justifyContent: 'center',
                                    background: colors.paper,
                                    fontSize: 19,
                                }}
                            >
                                →
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        ),
        {
            width: SOCIAL_CARD_WIDTH,
            height: SOCIAL_CARD_HEIGHT,
            headers: {
                'Cache-Control': 'public, max-age=3600, s-maxage=86400, stale-while-revalidate=604800',
            },
        },
    );
}
