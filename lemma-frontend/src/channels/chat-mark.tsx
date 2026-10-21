"use client";

import { ChannelIcon } from "@/shell/channels";
import { BellIcon, LockIcon } from "@/ui/icons";
import { initials, platformsOf, type Channel } from "./model";

/** What a chat that lives on a channel is, at a glance, the way Slack marks
 *  its channels: "#" for a shared chat, a lock for a private one, the
 *  platform for a mirrored group or your private line, a bell for the
 *  notices the teammate sends you, a contact's initials. */
export function ChannelMark({ channel, size = 16 }: { channel: Pick<Channel, "kind" | "sources" | "person"> & { visibility?: Channel["visibility"] }; size?: number }) {
    const [platform] = platformsOf(channel);
    if (platform) return <ChannelIcon platform={platform} size={size} />;
    if (channel.kind === "CONTACT") return <span className="chatmark__face">{initials(channel.person?.name ?? "?")}</span>;
    if (channel.kind === "DIRECT") return <BellIcon size={size} aria-hidden="true" />;
    if (channel.visibility === "PRIVATE") return <LockIcon size={size} aria-hidden="true" />;
    return <span className="chatmark__hash" style={{ fontSize: Math.round(size * 1.1) }} aria-hidden="true">#</span>;
}
