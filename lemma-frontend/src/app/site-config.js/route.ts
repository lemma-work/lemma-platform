export const dynamic = "force-dynamic";
export function GET() {
  const config = {
    analyticsKey: process.env.NEXT_PUBLIC_ANALYTICS_KEY || "",
    analyticsHost:
      process.env.NEXT_PUBLIC_ANALYTICS_HOST || "https://eu.posthog.com",
    deployment: process.env.NEXT_PUBLIC_LEMMA_DEPLOYMENT || "hosted",
  };
  return new Response(
    "window.__LEMMA_SITE__=" +
      JSON.stringify(config).replace(/</g, "\u003c") +
      ";",
    {
      headers: {
        "Content-Type": "application/javascript; charset=utf-8",
        "Cache-Control": "no-store",
      },
    },
  );
}
