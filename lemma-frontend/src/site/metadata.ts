import type { Metadata } from "next";
import { absoluteUrl } from "./seo/site-url";
import { socialCardPath } from "./share/social-card";
export function pageMetadata(
    title: string,
    description: string,
    path: string,
): Metadata {
    const image = socialCardPath({
        variant: "build",
        title,
        detail: description,
    });
    return {
        title,
        description,
        alternates: { canonical: absoluteUrl(path) },
        openGraph: {
            title,
            description,
            url: absoluteUrl(path),
            type: "website",
            images: [{ url: image, width: 1200, height: 630 }],
        },
        twitter: {
            card: "summary_large_image",
            title,
            description,
            images: [image],
        },
    };
}
