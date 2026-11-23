import { Albert_Sans } from "next/font/google";

/* The product's face, defined once. Every call to a font loader is hosted as
   its own copy, so the app's layout and the way in -- which also draws on the
   public site, on a plugin's install page -- import this one rather than each
   loading it. Figures stay on Inter (`--ui`), because Albert Sans as served
   has no tabular numerals. */
export const productFace = Albert_Sans({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-outcomes", display: "swap" });
