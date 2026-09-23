import { EmbedPreview } from "./embed-preview";
import { AppsIcon } from "@/ui/icons";
import { useQuery } from "@tanstack/react-query";
import { source } from "@/data";
import { sameSiteWithApi } from "@/session/client";

/** A widget is not an HTML snippet, it is a small app.
 *
 *  Putting its markup in a `srcDoc` iframe renders the shell and nothing
 *  else: the widget's browser SDK looks for a runtime configuration that an
 *  inline document can never have, so it reports "Lemma runtime configuration
 *  is unavailable" and stops. The platform does not inline them either — it
 *  mints a short-lived signed URL whose serve route is authenticated and
 *  carries that configuration, which is also why the widget can read live pod
 *  data every time it draws.
 *
 *  Inline content is the fallback, not the plan: it is all there is when the
 *  conversation or the tool call is unknown. */
export function WidgetView({
    podId,
    conversationId,
    toolCallId,
    content,
    label,
}: {
    podId: string;
    conversationId?: string | null;
    toolCallId?: string;
    content?: string;
    label: string;
}) {
    const canEmbed = Boolean(conversationId && toolCallId);

    const embed = useQuery({
        queryKey: ["widget", podId, conversationId, toolCallId],
        queryFn: () => source.widgetEmbedUrl(podId, conversationId as string, toolCallId as string),
        enabled: canEmbed,
        staleTime: 60_000,
        retry: false,
    });

    if (canEmbed && embed.isPending) {
        return (
            <div className="resource">
                <span className="resource__glyph"><AppsIcon size={22} /></span>
                <span className="resource__body">
                    <span className="resource__name">{label}</span>
                    <span className="resource__type">preparing…</span>
                </span>
            </div>
        );
    }

    /* A minted URL is the real widget; the iframe keeps its own origin so the
       SDK inside it can authenticate and fetch. */
    if (embed.data) {
        const crossSite = !sameSiteWithApi();
        return (
            <figure className="resource resource--widget">
                <EmbedPreview title={label} src={embed.data} sandbox="allow-scripts allow-same-origin allow-forms allow-popups" />
                {/* The preview's own toolbar already carries the name. Printing
                    it again underneath was the same label twice, inside two
                    borders — so the caption is kept for what the toolbar cannot
                    say, and drops out entirely when there is nothing to add. */}
                {crossSite && (
                    <figcaption className="resource__caption">
                        <span className="resource__warn">
                            Served from {window.location.hostname}, so the widget&rsquo;s own session cookie
                            for the API is partitioned away — it will say it cannot read the pod until this app is
                            served from the API&rsquo;s domain
                        </span>
                    </figcaption>
                )}
            </figure>
        );
    }

    if (content) {
        return (
            <figure className="resource resource--widget">
                <EmbedPreview title={label} html={content} />
                {canEmbed && <figcaption className="resource__caption">Shown without live data</figcaption>}
            </figure>
        );
    }

    return (
        <div className="resource">
            <span className="resource__glyph"><AppsIcon size={22} /></span>
            <span className="resource__body">
                <span className="resource__name">{label}</span>
                <span className="resource__type">widget · nothing to show</span>
            </span>
        </div>
    );
}
