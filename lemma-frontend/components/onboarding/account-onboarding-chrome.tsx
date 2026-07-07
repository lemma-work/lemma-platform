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
      <div className="flex flex-col px-6 py-6 sm:px-10 lg:px-16 lg:py-10">
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
        <ProgressDots
          currentStep={currentStep}
          steps={steps}
          className="flex gap-2"
        />
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

export function ProgressDots({
  currentStep,
  steps = SETUP_STEPS,
  className,
}: {
  currentStep: SetupStep;
  steps?: SetupStep[];
  className?: string;
}) {
  const currentIndex = steps.indexOf(currentStep);
  return (
    <div
      className={cn(
        className ?? "absolute bottom-7 left-1/2 flex -translate-x-1/2 gap-3",
        currentStep === "boot" ? "setup-boot-progress" : "",
      )}
    >
      {steps.map((step, index) => (
        <span
          key={step}
          className={[
            "setup-progress-dot h-2 w-2 transition",
            index === currentIndex
              ? "is-active"
              : index < currentIndex
                ? "is-complete"
                : "",
          ].join(" ")}
        />
      ))}
    </div>
  );
}
