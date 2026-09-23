'use client';

import { useEffect } from 'react';
import Link from 'next/link';
import QRCode from 'react-qr-code';

import { Button } from '@/components/ui/button';
import { ArrowRight, Download, Mail } from '@/components/ui/icons';
import { ResourceIcon } from '@/components/shared/resource-icon';
import { captureEvent } from '@/lib/analytics/client';
import { contactChannels, type ContactCardSpec } from '@/lib/share/contact-card';
import { getSurfaceDefinition } from '@/lib/surfaces/registry';

interface ContactLandingProps {
    card: ContactCardSpec;
    /** Workspace-relative path, already validated as `/pod/…` on the server. */
    destination: string;
    /** Same-origin path to the `.vcf`, built from this page's own segments. */
    downloadHref: string;
    /** This page's own absolute URL — what the QR encodes. */
    pageUrl: string;
}

/**
 * An agent, offered the way a person's contact details are offered.
 *
 * Nothing on this page is fetched. The name, the handles and the face all
 * arrive in the link, which is what lets it answer the reader it is actually
 * for: someone who was sent this in a group chat, has no Lemma account, and is
 * not about to make one before deciding whether to say hello.
 *
 * So the workspace is the quiet link at the bottom rather than the button in
 * the middle. `ShareLanding` is built the other way round — it asks whether you
 * can open the resource and sends you into the pod — and that is the right shape
 * for a document and the wrong one here. The promise a contact card makes is
 * that you never have to come to Lemma at all.
 */
export function ContactLanding({
    card,
    destination,
    downloadHref,
    pageUrl,
}: ContactLandingProps) {
    const channels = contactChannels(card);

    useEffect(() => {
        captureEvent('share_link.viewed', { kind: 'contact', viewer_is_member: false });
    }, []);

    return (
        <main className="flex min-h-dvh items-center justify-center px-6 py-16">
            <div className="w-full max-w-sm">
                <div className="rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface-2)] p-6 shadow-[var(--shadow-md)]">
                    <div className="flex flex-col items-center text-center">
                        <ResourceIcon
                            iconUrl={card.icon}
                            alt={`${card.name} picture`}
                            label={card.name}
                            identityKind="being"
                            identitySeed={card.seed}
                            identitySize={88}
                            className="h-22 w-22 rounded-2xl"
                        />
                        <h1 className="mt-4 text-xl font-medium text-[var(--text-primary)]">
                            {card.name}
                        </h1>
                        <p className="mt-1 text-sm text-[var(--text-secondary)]">
                            {/* Lem's card is named for its pod, because its own
                                name is the same in every pod — which would print
                                that pod twice, once on each line, if the subtitle
                                repeated it unconditionally. */}
                            {card.org && !card.name.includes(card.org)
                                ? `${card.org} · Agent on Lemma`
                                : 'Agent on Lemma'}
                        </p>
                        {card.note ? (
                            <p className="mt-3 text-sm text-[var(--text-secondary)]">{card.note}</p>
                        ) : null}
                    </div>

                    {channels.length > 0 ? (
                        <ul className="mt-6 flex flex-col gap-2">
                            {channels.map((channel) => {
                                const definition = getSurfaceDefinition(channel.platform);
                                return (
                                    <li key={channel.key}>
                                        <a
                                            href={channel.href}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            onClick={() =>
                                                captureEvent('share_link.contact_opened', {
                                                    channel: channel.key,
                                                })
                                            }
                                            className="flex items-center gap-3 rounded-md border border-[color:var(--border-subtle)] px-3 py-2 text-left hover:bg-[var(--surface-3)]"
                                        >
                                            {definition?.logoSrc ? (
                                                // eslint-disable-next-line @next/next/no-img-element -- a fixed-size mark, nothing for the optimizer to do.
                                                <img
                                                    src={definition.logoSrc}
                                                    alt=""
                                                    width={18}
                                                    height={18}
                                                    className="h-[18px] w-[18px] shrink-0"
                                                />
                                            ) : (
                                                <Mail className="h-[18px] w-[18px] shrink-0 text-[var(--text-secondary)]" />
                                            )}
                                            <span className="min-w-0 flex-1">
                                                <span className="block text-sm text-[var(--text-primary)]">
                                                    {channel.label}
                                                </span>
                                                <span className="block truncate text-xs text-[var(--text-tertiary)]">
                                                    {channel.value}
                                                </span>
                                            </span>
                                            <ArrowRight className="h-4 w-4 shrink-0 text-[var(--text-tertiary)]" />
                                        </a>
                                    </li>
                                );
                            })}
                        </ul>
                    ) : (
                        /* A card with no rows is not a broken page — it is an
                           agent nobody has given an outside address yet, and
                           saying so is more use than an empty list. */
                        <p className="mt-6 rounded-md border border-[color:var(--border-subtle)] px-3 py-2 text-center text-sm text-[var(--text-secondary)]">
                            This agent has no outside address yet. It answers inside Lemma.
                        </p>
                    )}

                    {channels.length > 0 ? (
                        <div className="mt-4 flex flex-col items-center gap-4">
                            {/* The one thing a reader has to know before saving,
                                and the one the card cannot deliver on its own:
                                inbound senders are resolved to a Lemma user, and
                                an unrecognised one gets a signup or request-access
                                link instead of the agent (`surface_inbound.py`).
                                Said here it is a fact about who to ask for an
                                invite; found out after saving, it reads as the
                                agent ignoring you. Same promise the reach card
                                makes inside the workspace, worded for the person
                                who is not in it yet. */}
                            <p className="text-center text-xs text-[var(--text-tertiary)]">
                                Answers members of this pod. Anyone else gets a link to request
                                access.
                            </p>

                            <Button variant="primary" asChild size="lg" className="w-full gap-2">
                                {/* A plain navigation, not a scripted save: the
                                    response carries its own Content-Disposition,
                                    which is the one route that works in an
                                    in-app browser as well as a real one. */}
                                <a
                                    href={downloadHref}
                                    onClick={() => captureEvent('share_link.contact_saved', {})}
                                >
                                    <Download className="h-4 w-4" />
                                    Save contact
                                </a>
                            </Button>

                            <div className="flex flex-col items-center gap-2">
                                <div className="text-[var(--text-primary)]" aria-hidden>
                                    <QRCode
                                        value={pageUrl}
                                        size={104}
                                        bgColor="transparent"
                                        fgColor="currentColor"
                                    />
                                </div>
                                <p className="text-xs text-[var(--text-tertiary)]">
                                    Scan to save it on your phone
                                </p>
                            </div>
                        </div>
                    ) : null}
                </div>

                <p className="mt-6 text-center text-xs text-[var(--text-tertiary)]">
                    <Link
                        href={destination}
                        prefetch={false}
                        className="hover:text-[var(--text-secondary)]"
                    >
                        Open it in Lemma
                    </Link>
                </p>
            </div>
        </main>
    );
}
