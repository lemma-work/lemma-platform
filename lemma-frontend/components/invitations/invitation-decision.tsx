"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { format } from "date-fns";
import { ArrowRight, CheckCircle2, UserRound, XCircle } from "@/components/ui/icons";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { InlineLoader } from "@/components/brand/loader";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import {
    useAcceptOrganizationInvitation,
    useOrganizationInvitation,
    useRejectOrganizationInvitation,
} from "@/lib/hooks/use-organizations";
import { OrganizationInvitationStatus } from "@/lib/types";
import { formatRoleLabel } from "@/lib/utils/role-labels";

type InvitationDecisionMode = "accept" | "reject";

export function InvitationDecision({
    invitationId,
    mode,
}: {
    invitationId: string;
    mode: InvitationDecisionMode;
}) {
    const router = useRouter();
    const { data: invitation, isLoading, error, refetch, isFetching } = useOrganizationInvitation(invitationId);
    const { mutate: acceptInvitation, isPending: isAccepting } = useAcceptOrganizationInvitation();
    const { mutate: rejectInvitation, isPending: isRejecting } = useRejectOrganizationInvitation();
    const isBusy = isAccepting || isRejecting;
    const podName = invitation?.pod_name?.trim() || null;
    const isPodInvitation = Boolean(podName);
    const title = podName || (mode === "reject" ? "Reject invitation" : "Organization invitation");
    const description = isPodInvitation
        ? invitation?.pod_description || "You have been invited to join this pod."
        : "Review the invitation details before continuing.";

    const navigateAfterAccept = (redirectUri?: string | null) => {
        const destination = redirectUri || invitation?.redirect_uri || "/";

        if (/^https?:\/\//i.test(destination)) {
            window.location.assign(destination);
            return;
        }

        router.replace(destination.startsWith("/") ? destination : `/${destination}`);
    };

    const handleAccept = () => {
        acceptInvitation(invitationId, {
            onSuccess: (response) => {
                toast.success(response.message || "Invitation accepted");
                navigateAfterAccept(response.redirect_uri);
            },
            onError: (err) => {
                toast.error(`Failed to accept invitation: ${err.message}`);
            },
        });
    };

    const handleReject = () => {
        rejectInvitation(invitationId, {
            onSuccess: () => {
                toast.success("Invitation rejected");
                router.replace("/");
            },
            onError: (err) => {
                toast.error(`Failed to reject invitation: ${err.message}`);
            },
        });
    };

    return (
        <main className="flex min-h-screen items-center justify-center bg-transparent px-6 py-12 text-[var(--text-primary)]">
            <div className="w-full max-w-xl">
                {isLoading && (
                    <Card className="surface-panel-quiet">
                        <CardContent className="flex items-center justify-center gap-3 py-10">
                            <InlineLoader size="sm" label="Loading invitation" />
                        </CardContent>
                    </Card>
                )}

                {!isLoading && (error || !invitation) && (
                    <Card className="surface-panel-quiet">
                        <CardHeader>
                            <CardTitle>Invitation not available</CardTitle>
                            <CardDescription>
                                We could not load this invitation. It may not exist or you may not have access.
                            </CardDescription>
                        </CardHeader>
                        <CardFooter className="gap-3">
                            <Button onClick={() => void refetch()} disabled={isFetching}>
                                {isFetching ? "Retrying..." : "Retry"}
                            </Button>
                            <Button asChild variant="ghost">
                                <Link href="/">Back to home</Link>
                            </Button>
                        </CardFooter>
                    </Card>
                )}

                {!isLoading && invitation && isPodInvitation && mode === "accept" && (
                    <Card className="surface-panel-shadowless px-7 py-10 text-center sm:px-12 sm:py-12">
                        <CardHeader className="items-center gap-5 pb-7">
                            <div className="inline-flex items-center gap-2 rounded-full border border-[color:var(--border-subtle)] bg-[var(--surface-1)] px-4 py-2 type-eyebrow">
                                <span className="h-2 w-2 rounded-full bg-[var(--delight)]" />
                                {invitation.status === OrganizationInvitationStatus.PENDING ? "Access ready" : invitation.status}
                            </div>

                            <div className="space-y-4">
                                <CardTitle className="[font-family:var(--font-landing-serif)] font-light tracking-normal text-[var(--text-primary)]">
                                    <span className="block text-4xl leading-tight text-[var(--text-primary)]">
                                        You&rsquo;re joining
                                    </span>
                                    <span className="mt-1 block text-6xl leading-none">
                                        {podName}
                                    </span>
                                </CardTitle>
                                <CardDescription className="mx-auto max-w-md text-sm leading-6 text-[var(--text-secondary)]">
                                    {description}
                                </CardDescription>
                            </div>
                        </CardHeader>

                        <CardContent className="space-y-6">
                            <section className="rounded-xl bg-[color:color-mix(in_srgb,var(--bg-subtle)_82%,transparent)] px-6 py-5 text-left">
                                <p className="text-sm font-semibold text-[var(--text-primary)]">Access type</p>
                                <div className="mt-4 flex items-center gap-4">
                                    <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-[color:var(--border-subtle)] bg-[var(--surface-1)] text-[var(--text-secondary)]">
                                        <UserRound className="h-5 w-5" />
                                    </span>
                                    <span className="text-base font-medium text-[var(--text-primary)]">
                                        {formatRoleLabel(invitation.pod_role || invitation.role)}
                                    </span>
                                </div>
                            </section>
                        </CardContent>

                        <CardFooter className="flex-col gap-4 pt-6">
                            {invitation.status === OrganizationInvitationStatus.PENDING ? (
                                <Button
                                    onClick={handleAccept}
                                    disabled={isBusy}
                                    loading={isAccepting}
                                    loadingLabel="Accepting"
                                    className="h-14 w-full gap-3 text-base"
                                >
                                    <span className="min-w-0 truncate">Accept and open {podName}</span>
                                    <ArrowRight className="h-5 w-5 shrink-0" />
                                </Button>
                            ) : (
                                <Button asChild className="h-14 w-full text-base">
                                    <Link href="/">Go to home</Link>
                                </Button>
                            )}
                            <Button asChild variant="ghost" className="text-base">
                                <Link href="/">Back</Link>
                            </Button>
                        </CardFooter>
                    </Card>
                )}

                {!isLoading && invitation && (!isPodInvitation || mode === "reject") && (
                    <Card className="surface-panel-quiet px-7 py-7 sm:px-8 sm:py-8">
                        <CardHeader className="gap-5 pb-5">
                            <div className="flex flex-wrap items-center justify-between gap-3">
                                <span className="type-eyebrow">
                                    {isPodInvitation ? "Pod invitation" : "Organization invitation"}
                                </span>
                                <Badge variant={badgeVariantForStatus(invitation.status)}>
                                    {invitation.status}
                                </Badge>
                            </div>

                            <div className={isPodInvitation ? "flex gap-4" : "space-y-2"}>
                                {isPodInvitation ? (
                                    <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-lg border border-[color:var(--border-subtle)] bg-[var(--bg-subtle)] text-xl font-semibold text-[var(--text-primary)]">
                                        {getPodInitial(podName ?? "")}
                                    </div>
                                ) : null}
                                <div className="min-w-0 space-y-2">
                                    <CardTitle className={isPodInvitation ? "text-3xl sm:text-4xl" : "text-2xl"}>
                                        {title}
                                    </CardTitle>
                                    <CardDescription className="max-w-2xl text-base">
                                        {description}
                                    </CardDescription>
                                </div>
                            </div>
                        </CardHeader>

                        <CardContent className="space-y-4">
                            <DetailRow label="Email" value={invitation.email} />
                            {invitation.pod_role ? (
                                <DetailRow label="Pod role" value={formatRoleLabel(invitation.pod_role)} emphasis />
                            ) : null}
                            <DetailRow label="Organization role" value={formatRoleLabel(invitation.role)} />
                            {invitation.redirect_uri ? (
                                <DetailRow label="Opens after accept" value={invitation.redirect_uri} mono />
                            ) : null}
                            <DetailRow label="Expires" value={format(new Date(invitation.expires_at), "PPP p")} />
                            <DetailRow label="Sent" value={format(new Date(invitation.created_at), "PPP p")} />

                            {invitation.accepted_at && (
                                <DetailRow label="Accepted" value={format(new Date(invitation.accepted_at), "PPP p")} />
                            )}
                            {invitation.revoked_at && (
                                <DetailRow label="Revoked" value={format(new Date(invitation.revoked_at), "PPP p")} />
                            )}
                        </CardContent>

                        <CardFooter className="flex-wrap gap-3">
                            {invitation.status === OrganizationInvitationStatus.PENDING ? (
                                mode === "reject" ? (
                                    <>
                                        <Button
                                            variant="destructive"
                                            onClick={handleReject}
                                            disabled={isBusy}
                                            loading={isRejecting}
                                            loadingLabel="Rejecting"
                                            className="gap-2"
                                        >
                                            <XCircle className="h-4 w-4" />
                                            Reject invitation
                                        </Button>
                                        <Button asChild variant="ghost">
                                            <Link href={`/invitations/${invitationId}/accept`}>Back to accept</Link>
                                        </Button>
                                    </>
                                ) : (
                                    <>
                                        <Button
                                            onClick={handleAccept}
                                            disabled={isBusy}
                                            loading={isAccepting}
                                            loadingLabel="Accepting"
                                            className="gap-2"
                                        >
                                            <CheckCircle2 className="h-4 w-4" />
                                            Accept invitation
                                        </Button>
                                        <Button asChild variant="outline">
                                            <Link href={`/invitations/${invitationId}/reject`}>Reject</Link>
                                        </Button>
                                        <Button asChild variant="ghost">
                                            <Link href="/">Cancel</Link>
                                        </Button>
                                    </>
                                )
                            ) : (
                                <Button asChild>
                                    <Link href="/">Go to home</Link>
                                </Button>
                            )}
                        </CardFooter>
                    </Card>
                )}
            </div>
        </main>
    );
}

function DetailRow({
    label,
    value,
    mono = false,
    emphasis = false,
}: {
    label: string;
    value: string;
    mono?: boolean;
    emphasis?: boolean;
}) {
    return (
        <div className="flex flex-col gap-1 rounded-lg border border-[color:var(--border-subtle)] bg-[var(--bg-subtle)] px-3 py-2">
            <span className="type-eyebrow-medium">
                {label}
            </span>
            <span className={[
                mono ? "break-all font-mono" : "",
                emphasis ? "text-base font-semibold" : "text-sm",
                "text-[var(--text-primary)]",
            ].filter(Boolean).join(" ")}>
                {value}
            </span>
        </div>
    );
}

function getPodInitial(name: string): string {
    return name.trim().charAt(0).toUpperCase() || "P";
}

function badgeVariantForStatus(status: OrganizationInvitationStatus): "info" | "success" | "warning" | "error" {
    switch (status) {
        case OrganizationInvitationStatus.ACCEPTED:
            return "success";
        case OrganizationInvitationStatus.EXPIRED:
            return "warning";
        case OrganizationInvitationStatus.REVOKED:
            return "error";
        case OrganizationInvitationStatus.PENDING:
        default:
            return "info";
    }
}
