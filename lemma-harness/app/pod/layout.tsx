import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Pod Workspace",
  description: "Run and configure a Lemma pod with apps, data, files, agents, and processes.",
  robots: {
    index: false,
    follow: false,
    nocache: true,
    googleBot: {
      index: false,
      follow: false,
      noimageindex: true,
    },
  },
};

export default function PodRouteLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
