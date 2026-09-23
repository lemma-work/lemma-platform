import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Lemma",
    short_name: "Lemma",
    description:
      "Build AI pods for long-running agentic work. Design agents, records, workflows, and apps in one place.",
    start_url: "/",
    display: "standalone",
    background_color: "rgb(253 253 248)",
    theme_color: "rgb(253 253 248)",
    icons: [
      {
        src: "/icon.svg",
        sizes: "any",
        type: "image/svg+xml",
      },
      {
        src: "/icon-192.png",
        sizes: "192x192",
        type: "image/png",
      },
      {
        src: "/icon-512.png",
        sizes: "512x512",
        type: "image/png",
      },
    ],
  };
}
