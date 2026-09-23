import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Account Access",
  description: "Sign in or create an account to access your Lemma workspace.",
  robots: {
    index: false,
    follow: false,
    nocache: true,
  },
};

export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
