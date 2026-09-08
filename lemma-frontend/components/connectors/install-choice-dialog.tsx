'use client';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { ExternalLink } from '@/components/ui/icons';
import type { InstallationChoiceSchema } from 'lemma-sdk';
import { StepLoader } from '@/components/brand/loader';

/**
 * Which installation an account speaks for, when it can reach several.
 *
 * Somebody who belongs to two organisations that have both installed the app
 * has two, and picking whichever came back first would route the other
 * organisation's events at them. So the choice is theirs — one account works
 * as one installation, and connecting again is how a second one is added.
 */
export function InstallChoiceDialog({
    choices,
    accountLabel,
    isSubmitting,
    onOpenChange,
    onChoose,
}: {
    choices: InstallationChoiceSchema[] | null;
    accountLabel: string;
    isSubmitting: boolean;
    onOpenChange: (open: boolean) => void;
    onChoose: (installationId: string) => void;
}) {
    return (
        <Dialog open={Boolean(choices?.length)} onOpenChange={onOpenChange}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Which organisation?</DialogTitle>
                    <DialogDescription>
                        {accountLabel} can reach more than one installation. Pick the one this
                        connection should work as — you can connect again to add another.
                    </DialogDescription>
                </DialogHeader>

                <div className="flex flex-col gap-2">
                    {(choices ?? []).map((choice) => (
                        <div
                            key={choice.installation_id}
                            className="flex items-center gap-3 rounded-lg border border-[var(--border-subtle)] px-3 py-2.5"
                        >
                            <div className="min-w-0 flex-1">
                                <p className="truncate text-sm text-[var(--text-primary)]">
                                    {choice.account_login || choice.installation_id}
                                </p>
                                <p className="truncate text-xs leading-5 text-[var(--text-tertiary)]">
                                    {choice.account_type === 'Organization' ? 'Organisation' : 'Personal account'}
                                    {choice.repository_selection === 'all'
                                        ? ' · all repositories'
                                        : choice.repository_selection === 'selected'
                                          ? ' · selected repositories'
                                          : ''}
                                </p>
                            </div>
                            {/*
                              * Repository access is changed on GitHub and nowhere else: the
                              * endpoints that add or remove a repository from an installation
                              * accept only classic personal access tokens, so a picker built
                              * here could show the truth but never change it.
                              */}
                            <a
                                className="shrink-0 text-xs text-[var(--text-tertiary)] underline-offset-2 hover:underline"
                                href={choice.manage_url}
                                target="_blank"
                                rel="noopener noreferrer"
                            >
                                Manage
                                <ExternalLink className="ml-1 inline h-3 w-3" />
                            </a>
                            <Button
                                variant="secondary"
                                size="sm"
                                className="h-8 shrink-0"
                                disabled={isSubmitting}
                                onClick={() => onChoose(choice.installation_id)}
                            >
                                {isSubmitting ? <StepLoader size="xs" className="mr-1.5" /> : null}
                                Use this
                            </Button>
                        </div>
                    ))}
                </div>

                <DialogFooter>
                    <Button variant="quiet" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
                        Cancel
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
