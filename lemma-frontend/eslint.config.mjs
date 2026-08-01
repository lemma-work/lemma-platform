import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    files: ["**/*.{js,jsx,ts,tsx}"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='style']",
          message:
            "Use className with Tailwind/CSS tokens instead of inline styles. Keep inline style only for unavoidable runtime geometry.",
        },
        // Loading is a shared system, not a per-screen decision. Every ad-hoc
        // spinner and pulse was a different answer to "what shows while this
        // loads", and the sum of those answers is why the app used to re-flow
        // two or three times per page load.
        //   content coming  → <Skeleton /> or a shape from components/shared/loading
        //   an action running → <Button loading> or <StepLoader />
        //   something is alive → .lemma-live-pulse
        //   a refresh control turning → .lemma-spin
        {
          selector: "Literal[value=/(^|\\s)animate-(spin|pulse)(\\s|$)/]",
          message:
            "Don't hand-roll loading motion. Use components/shared/loading (Skeleton, AsyncRegion), <Button loading>, or <StepLoader />; for liveness use .lemma-live-pulse, and for a spinning refresh control .lemma-spin.",
        },
        {
          selector: "TemplateElement[value.raw=/(^|\\s)animate-(spin|pulse)(\\s|$)/]",
          message:
            "Don't hand-roll loading motion. Use components/shared/loading (Skeleton, AsyncRegion), <Button loading>, or <StepLoader />; for liveness use .lemma-live-pulse, and for a spinning refresh control .lemma-spin.",
        },
      ],
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
