import { ArrowLeft, Sparkles } from "@/components/ui/icons";

import { Logo } from "@/components/brand/logo";
import { ThemeToggle } from "@/components/theme/theme-toggle";
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
        fullBleed ? "h-dvh min-h-0" : "items-center justify-center px-4 py-8",
      ].join(" ")}
    >
      <div className="setup-shell-bottom-glow absolute inset-x-0 bottom-0 h-72" />
      <div
        className={[
          "relative flex w-full",
          fullBleed ? "min-h-0" : "items-center justify-center",
        ].join(" ")}
      >
        {children}
      </div>
    </main>
  );
}

function SetupFooter({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <footer
      aria-label="Setup actions"
      className={cn("relative shrink-0 border-t border-[var(--border-subtle)] py-4 [&_.setup-primary-action]:!mt-0", className)}
    >
      {children}
    </footer>
  );
}

export function SetupStandalonePage({
  children,
  onBack,
  meta,
  footer,
}: {
  children: React.ReactNode;
  onBack?: () => void;
  meta?: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <div className="relative flex h-dvh min-h-0 w-full flex-col">
      <header className="grid shrink-0 min-h-16 grid-cols-[1fr_auto_1fr] items-center gap-4 px-5 py-4 sm:px-8 lg:px-10">
        <div className="justify-self-start">
          {onBack ? (
            <Button
              type="button"
              variant="quiet"
              onClick={onBack}
              className="h-auto gap-1.5 px-0 text-sm text-[var(--text-tertiary)] hover:bg-transparent hover:text-[var(--text-primary)]"
            >
              <ArrowLeft className="h-4 w-4" />
              Back
            </Button>
          ) : null}
        </div>
        <Logo size="sm" className="text-[var(--text-primary)]" />
        <div className="flex min-w-0 items-center gap-3 justify-self-end text-right text-xs text-[var(--text-tertiary)]">
          {meta}
          <ThemeToggle variant="icon" />
        </div>
      </header>
      <div data-testid="setup-content" className="relative flex min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pb-5 pt-2 sm:px-8 lg:px-10">
        {children}
      </div>
      {footer ? (
        <SetupFooter className="px-5">
          <div className="mx-auto w-full max-w-lg">{footer}</div>
        </SetupFooter>
      ) : null}
    </div>
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

export function SetupChoicesPage({ title, subtitle, children, footer, onBack }: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
  footer: React.ReactNode;
  onBack?: () => void;
}) {
  return (
    <SetupStandalonePage onBack={onBack} meta="Local setup" footer={footer}>
      <div className="mx-auto w-full max-w-5xl py-2 text-left">
        <h1 className="text-xl font-semibold tracking-tight text-[var(--text-primary)]">{title}</h1>
        <p className="mt-2 text-sm text-[var(--text-secondary)]">{subtitle}</p>
        <div className="mt-5 grid gap-6 md:grid-cols-2 md:gap-8">{children}</div>
      </div>
    </SetupStandalonePage>
  );
}

export function SetupPanel({
  title,
  subtitle,
  children,
  footer,
  titleClassName = "",
  subtitleClassName = "",
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  titleClassName?: string;
  subtitleClassName?: string;
}) {
  return (
    <div className={cn("mx-auto flex w-full max-w-4xl flex-col px-6 text-center", footer ? "h-dvh min-h-0" : "my-auto py-10")}>
      <div data-testid="setup-content" className={footer ? "min-h-0 flex-1 overflow-y-auto overscroll-contain py-6" : ""}>
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
      {footer ? <SetupFooter>{footer}</SetupFooter> : null}
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
  footer,
  onBack,
  currentStep,
  steps,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  preview: React.ReactNode;
  footer?: React.ReactNode;
  onBack?: () => void;
  currentStep: SetupStep;
  steps?: SetupStep[];
}) {
  return (
    <div className="grid h-dvh min-h-0 w-full flex-1 lg:grid-cols-2">
      <div className="relative flex min-h-0 flex-col overflow-hidden px-6 pt-5 sm:px-10 lg:px-12">
        <div className="setup-split-glow absolute inset-0" aria-hidden="true" />
        <div className="relative flex shrink-0 items-center justify-between">
          {onBack ? (
            <Button
              type="button"
              variant="quiet"
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
        <div className="relative my-4 w-full max-w-xl shrink-0">
          <SetupProgressBar currentStep={currentStep} steps={steps} />
        </div>
        <div className="relative min-h-0 flex-1 overflow-y-auto overscroll-contain pb-5" data-testid="setup-content">
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
          <div className="mt-5 w-full">{children}</div>
        </div>
        {footer ? <SetupFooter>{footer}</SetupFooter> : null}
      </div>
      <div className="setup-preview-pane min-h-0 overflow-y-auto hidden lg:flex lg:flex-col">
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
    <Button variant="primary"
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
  // Clamped, because a step can legitimately be off this list: the local flow
  // falls back to asking for a name when provisioning fails, and that step is
  // not one of the questions the bar is counting. An index of -1 used to make
  // the fill a negative width.
  const currentIndex = Math.max(0, steps.indexOf(currentStep));
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
