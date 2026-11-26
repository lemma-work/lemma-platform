import "@/styles/guide-tokens.css";
import "@/styles/guide.css";
import type { ReactNode } from "react";

/* The guide brings its own palette and stylesheet, and nothing else on the
   site loads them. */
export default function GuideLayout({ children }: { children: ReactNode }) {
    return children;
}
