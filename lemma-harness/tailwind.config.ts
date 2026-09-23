import type { Config } from "tailwindcss";

// Tailwind v4 uses CSS-first configuration
// Most config is now in globals.css using @theme
const config: Config = {
    content: [
        "./pages/**/*.{js,ts,jsx,tsx,mdx}",
        "./components/**/*.{js,ts,jsx,tsx,mdx}",
        "./app/**/*.{js,ts,jsx,tsx,mdx}",
        "./node_modules/lemma-sdk/dist/react/**/*.{js,mjs}",
    ],
};

export default config;
