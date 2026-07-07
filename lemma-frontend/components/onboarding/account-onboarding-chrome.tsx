import { ArrowLeft, Sparkles } from "lucide-react";

import { Logo } from "@/components/brand/logo";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { SETUP_STEPS, type SetupStep } from "./account-onboarding-helpers";

export function SetupShell({
  children,
  fullBleed = false,
}: {
  children: React.ReactNode;
  fullBleed?: boolean;
}) {
  return (
    <main
      className={[
        "setup-shell relative flex min-h-screen overflow-hidden text-[var(--text-primary)]",
        fullBleed ? "" : "items-center justify-center px-4 py-8",
      ].join(" ")}
    >
      <div className="setup-shell-bottom-glow absolute inset-x-0 bottom-0 h-72" />
      <div
        className={[
          "relative flex w-full",
          fullBleed ? "" : "items-center justify-center",
        ].join(" ")}
      >
        {children}
      </div>
    </main>
  );
}

export function SetupChrome({ intro = false }: { intro?: boolean }) {
  return (
    <header
      className={[
        "flex items-center justify-between",
        intro ? "setup-chrome-intro" : "",
      ].join(" ")}
    >
      <Logo size="sm" className="text-[var(--text-primary)]" />
      <div className="setup-badge rounded-full px-3 py-1 text-xs font-medium">
        Setup
      </div>
    </header>
  );
}

export function SetupPanel({
  title,
  subtitle,
  children,
  titleClassName = "",
  subtitleClassName = "",
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  titleClassName?: string;
  subtitleClassName?: string;
}) {
  return (
    <div className="m-auto w-full max-w-4xl px-6 py-10 text-center">
      <h1
        className={[
          "setup-panel-title mx-auto max-w-4xl font-normal tracking-normal text-[var(--text-primary)]",
          titleClassName,
        ].join(" ")}
      >
        {title}
      </h1>
      {subtitle ? (
        <p
          className={[
            "mx-auto mt-3 max-w-2xl text-base leading-7 text-[var(--text-secondary)]",
            subtitleClassName,
          ].join(" ")}
        >
          {subtitle}
        </p>
      ) : null}
      {children}
    </div>
  );
}

// The full-viewport two-column layout used by every onboarding step after
// Boot: a form column (left) and a live preview column (right) that fills
// the entire remaining height — true split-screen, not a form-plus-preview
// floating inside the old centered card. Back button, logo, and step
// progress all live inside the left column so the vertical divider between
// panes runs the full height of the screen uninterrupted.
export function SetupSplitPanel({
  title,
  subtitle,
  children,
  preview,
  onBack,
  currentStep,
  steps,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  preview: React.ReactNode;
  onBack?: () => void;
  currentStep: SetupStep;
  steps?: SetupStep[];
}) {
  return (
    <div className="grid w-full flex-1 lg:grid-cols-2">
      <div className="relative flex flex-col overflow-hidden px-6 py-6 sm:px-10 lg:px-16 lg:py-10">
        <div className="setup-split-glow absolute inset-0" aria-hidden="true" />
        <div className="flex items-center justify-between">
          {onBack ? (
            <Button
              type="button"
              variant="ghost"
              onClick={onBack}
              className="h-auto gap-1.5 px-0 text-sm text-[var(--text-tertiary)] hover:bg-transparent hover:text-[var(--text-primary)]"
            >
              <ArrowLeft className="h-4 w-4" />
              Back
            </Button>
          ) : (
            <span />
          )}
          <Logo size="sm" className="text-[var(--text-primary)]" />
        </div>
        {/* Pinned right under the header at a fixed height, independent of
            title/subtitle/form length, so it sits in the same spot on every
            step instead of drifting with the vertically-centered content
            below it. */}
        <div className="mt-6 w-full max-w-xl">
          <SetupProgressBar currentStep={currentStep} steps={steps} />
        </div>
        <div className="flex flex-1 flex-col justify-center">
          <div className="w-full max-w-xl text-left">
            <h1 className="setup-split-title text-[var(--text-primary)]">
              {title}
            </h1>
            {subtitle ? (
              <p className="mt-2.5 text-sm leading-6 text-[var(--text-secondary)]">
                {subtitle}
              </p>
            ) : null}
          </div>
          <div className="mt-8 w-full">{children}</div>
        </div>
      </div>
      <div className="setup-preview-pane hidden lg:flex lg:flex-col">
        <div className="setup-path-pane-content flex h-full flex-col p-8 xl:p-10">
          {preview}
        </div>
      </div>
    </div>
  );
}

export function SetupPrimaryButton({
  children,
  className = "",
  ...props
}: React.ComponentProps<typeof Button>) {
  return (
    <Button
      {...props}
      className={[
        "setup-primary-action !flex mx-auto mt-8 h-12 min-w-56 gap-3 px-7 text-sm font-medium",
        className,
      ].join(" ")}
    >
      <Sparkles className="h-5 w-5" />
      {children}
    </Button>
  );
}

// Thin fill bar tracking how far through setup the operator is, in place of
// the previous step dots — a single glance at percentage-complete reads
// faster than counting dots, and it frees the bottom of the left column.
export function SetupProgressBar({
  currentStep,
  steps = SETUP_STEPS,
  className,
}: {
  currentStep: SetupStep;
  steps?: SetupStep[];
  className?: string;
}) {
  const currentIndex = steps.indexOf(currentStep);
  const percent =
    steps.length > 1 ? (currentIndex / (steps.length - 1)) * 100 : 100;

  return (
    <div
      role="progressbar"
      aria-valuenow={Math.round(percent)}
      aria-valuemin={0}
      aria-valuemax={100}
      className={cn("setup-progress-track h-[3px] w-full max-w-[120px]", className)}
    >
      <div
        className="setup-progress-fill h-full"
        /* eslint-disable-next-line no-restricted-syntax -- Fill width is a computed percentage, not a themeable style. */
        style={{ width: `${percent}%` }}
      />
    </div>
  );
}
