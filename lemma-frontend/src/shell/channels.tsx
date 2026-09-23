import { EmailIcon, LinkIcon, DiscordIcon } from "@/ui/icons";

export const CHANNELS = ["WHATSAPP", "TELEGRAM", "EMAIL", "SLACK"] as const;
export function channelKey(platform: string) {
    const key = platform.toUpperCase();
    return ["EMAIL", "RESEND", "GMAIL", "OUTLOOK"].includes(key) ? "EMAIL" : key;
}
export function channelName(platform: string) {
    const key = channelKey(platform);
    return ({ WHATSAPP: "WhatsApp", TELEGRAM: "Telegram", EMAIL: "Email", SLACK: "Slack", TEAMS: "Microsoft Teams", DISCORD: "Discord" } as Record<string, string>)[key] ?? platform;
}
export function ChannelIcon({ platform, size = 18 }: { platform: string; size?: number }) {
    const key = channelKey(platform);
    if (["WHATSAPP", "TELEGRAM", "SLACK", "TEAMS"].includes(key)) {
        return <img className="channel-icon" src={`/connector-logos/${key.toLowerCase()}.svg`} width={size} height={size} alt="" aria-hidden="true" />;
    }
    if (key === "EMAIL") return <EmailIcon size={size} aria-hidden="true" />;
    if (key === "DISCORD") return <DiscordIcon size={size} aria-hidden="true" />;
    return <LinkIcon size={size} aria-hidden="true" />;
}
