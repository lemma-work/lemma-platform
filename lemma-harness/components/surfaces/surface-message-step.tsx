'use client';

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { usePodMembers } from '@/lib/hooks/use-pod-members';

/**
 * Send a proactive message to a pod member over this surface.
 *
 * A state of the modal rather than a dialog of its own: it acts on the surface
 * that is already open, and stacking a second dialog on top of the first would
 * be two overlays deep for one text box.
 */
export function SurfaceMessageStep({
    podId,
    userId,
    onUserIdChange,
    message,
    onMessageChange,
}: {
    podId: string;
    userId: string;
    onUserIdChange: (userId: string) => void;
    message: string;
    onMessageChange: (message: string) => void;
}) {
    const { data: membersData } = usePodMembers(podId);
    const members = membersData?.items ?? [];

    return (
        <div className="grid gap-3">
            <p className="text-sm leading-6 text-[var(--text-secondary)]">
                This lands only if they already have a conversation here. Nobody gets messaged
                out of the blue.
            </p>
            <div className="grid gap-1.5">
                <label className="type-eyebrow-medium">Pod member</label>
                <Select value={userId} onValueChange={onUserIdChange}>
                    <SelectTrigger className="h-10 bg-[var(--field-bg)]">
                        <SelectValue placeholder="Select a member" />
                    </SelectTrigger>
                    <SelectContent>
                        {members.map((member) => (
                            <SelectItem key={member.user_id} value={member.user_id}>
                                {member.user_name || member.user_email}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </div>
            <div className="grid gap-1.5">
                <label className="type-eyebrow-medium">Message</label>
                <Textarea
                    value={message}
                    onChange={(event) => onMessageChange(event.target.value)}
                    placeholder="What should the agent say?"
                    rows={4}
                />
            </div>
        </div>
    );
}
