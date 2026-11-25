import { Jost } from "next/font/google";

/* The guide's headings: thin, lowercase and geometric. Loaded here, once, and
   only by the guide; the reading text is the serif the whole site already
   loads. */
export const guideFace = Jost({ subsets: ["latin"], weight: "variable", variable: "--font-guide", display: "swap" });
