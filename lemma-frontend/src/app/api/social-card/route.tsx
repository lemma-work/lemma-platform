import { ImageResponse } from "next/og";
import {
  resolveSocialCardSpec,
  socialCardTitleSize,
} from "@/site/share/social-card";
export function GET(request: Request) {
  const p = new URL(request.url).searchParams;
  const spec = resolveSocialCardSpec({
    variant: p.get("variant"),
    title: p.get("title"),
    detail: p.get("detail"),
    label: p.get("label"),
  });
  return new ImageResponse(
    (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          background: "#f4f3ed",
          color: "#20211f",
          width: "100%",
          height: "100%",
          padding: 70,
        }}
      >
        <div style={{ display: "flex", fontSize: 28, color: "#5a3fd4" }}>
          Lemma
        </div>
        <div
          style={{
            display: "flex",
            fontSize: socialCardTitleSize(spec.title),
            letterSpacing: -3,
          }}
        >
          {spec.title}
        </div>
        <div style={{ display: "flex", fontSize: 28 }}>{spec.detail}</div>
        <div style={{ display: "flex", fontSize: 20 }}>{spec.label}</div>
      </div>
    ),
    { width: 1200, height: 630 },
  );
}
