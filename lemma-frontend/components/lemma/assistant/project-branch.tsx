"use client";

/**
 * Where in a project a conversation is working, next to which project it is.
 *
 * The chip names the branch; opening it says what is happening on that branch —
 * the pull request it is in, how big that pull request is, and the two places
 * you would go next (GitHub's compare view, or opening the pull request itself).
 *
 * Before a conversation exists the branch is a choice, and picking one changes
 * what gets cloned. After it exists the branch is a fact: the checkout is on
 * disk and an agent is working in it, so the list is gone and the panel is a
 * readout. The same component does both, because it is the same information.
 *
 * Everything here is what GitHub knows. It cannot see uncommitted work in the
 * agent's checkout, so nothing in it may be phrased as if it can.
 */

import { useState } from "react";

import { StepLoader } from "@/components/brand/loader";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Check,
  ChevronDown,
  ExternalLink,
  GitBranch,
  GitPullRequest,
} from "@/components/ui/icons";
import { cn } from "@/lib/utils";
import {
  compareBranchUrl,
  pullRequestDiffstat,
  pullRequestStateLabel,
  pullRequestTitleFromBranch,
  type ProjectPullRequest,
} from "@/lib/github/project-branch";
import {
  useBranchPullRequest,
  useCreatePullRequest,
  useRepoBranches,
} from "@/lib/hooks/use-github-branches";
import type { ProjectSelection } from "@/lib/assistant/project-selection";

const CHIP_CLASS =
  "inline-flex h-8 min-w-0 max-w-[11rem] items-center gap-1.5 rounded-md px-2 text-xs text-[var(--text-secondary)]";

/** Merged is the one state worth a colour; the rest read fine as plain text. */
const STATE_CLASS: Record<ProjectPullRequest["state"], string> = {
  draft: "text-[var(--text-tertiary)]",
  open: "text-[var(--status-success-text,var(--text-secondary))]",
  merged: "text-[var(--accent-primary,var(--text-secondary))]",
  closed: "text-[var(--text-tertiary)]",
};

function Diffstat({ pullRequest }: { pullRequest: ProjectPullRequest }) {
  const diffstat = pullRequestDiffstat(pullRequest);
  if (!diffstat) return null;
  return (
    <span className="shrink-0 tabular-nums text-[var(--text-tertiary)]">
      <span className="text-[var(--status-success-text,var(--text-secondary))]">
        +{diffstat.additions.toLocaleString()}
      </span>{" "}
      <span className="text-[var(--status-danger-text,var(--text-secondary))]">
        −{diffstat.deletions.toLocaleString()}
      </span>
    </span>
  );
}

function PullRequestSummary({
  pullRequest,
  project,
}: {
  pullRequest: ProjectPullRequest;
  project: ProjectSelection;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-xs">
        <GitPullRequest className="size-3.5 shrink-0 text-[var(--text-tertiary)]" />
        <span className="text-[var(--text-secondary)]">#{pullRequest.number}</span>
        <span className={cn("shrink-0", STATE_CLASS[pullRequest.state])}>
          {pullRequestStateLabel(pullRequest.state)}
        </span>
        <span className="ml-auto">
          <Diffstat pullRequest={pullRequest} />
        </span>
      </div>
      <p className="truncate text-sm text-[var(--text-primary)]" title={pullRequest.title}>
        {pullRequest.title}
      </p>
      <div className="flex items-center gap-3 text-xs">
        {pullRequest.url ? (
          <a
            className="inline-flex items-center gap-1 text-[var(--text-secondary)] hover:underline"
            href={pullRequest.url}
            target="_blank"
            rel="noreferrer"
          >
            Pull request <ExternalLink className="size-3" />
          </a>
        ) : null}
        {pullRequest.base ? (
          <a
            className="inline-flex items-center gap-1 text-[var(--text-secondary)] hover:underline"
            href={compareBranchUrl(
              project.owner,
              project.repo,
              pullRequest.base,
              pullRequest.head || project.ref || "",
            )}
            target="_blank"
            rel="noreferrer"
          >
            Compare branch <ExternalLink className="size-3" />
          </a>
        ) : null}
      </div>
    </div>
  );
}

function CreatePullRequestForm({
  project,
  defaultBranch,
  onDone,
}: {
  project: ProjectSelection;
  defaultBranch: string;
  onDone: () => void;
}) {
  const [title, setTitle] = useState(() =>
    pullRequestTitleFromBranch(project.ref || ""),
  );
  const create = useCreatePullRequest(project);

  return (
    <form
      className="space-y-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (!title.trim() || create.isPending) return;
        create.mutate(
          { title: title.trim(), base: defaultBranch },
          { onSuccess: onDone },
        );
      }}
    >
      <Input
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        placeholder="Pull request title"
        aria-label="Pull request title"
        className="h-8 text-xs"
      />
      <p className="text-xs text-[var(--text-tertiary)]">
        {project.ref} → {defaultBranch}. Only what is pushed will be in it.
      </p>
      {create.error ? (
        <p className="text-xs text-[var(--status-danger-text,var(--text-secondary))]">
          GitHub refused it. The branch may not be pushed yet, or a pull request
          may already exist.
        </p>
      ) : null}
      <Button
        type="submit"
        size="sm"
        variant="secondary"
        className="w-full"
        disabled={!title.trim() || create.isPending}
      >
        {create.isPending ? "Opening…" : "Open pull request"}
      </Button>
    </form>
  );
}

/**
 * What is happening on this branch, or the offer to start it.
 *
 * Its own component so that the half-typed title in the form belongs to one
 * branch: the chip keys this on the branch, so switching branches unmounts the
 * form rather than carrying it across.
 */
function BranchPullRequest({
  project,
  enabled,
}: {
  project: ProjectSelection;
  enabled: boolean;
}) {
  const [composing, setComposing] = useState(false);
  const { defaultBranch } = useRepoBranches(project, { enabled });
  const { pullRequest, isLoading } = useBranchPullRequest(project, { enabled });

  if (isLoading) {
    return (
      <p className="flex items-center gap-2 text-xs text-[var(--text-tertiary)]">
        <StepLoader size="xs" />
        Checking for a pull request…
      </p>
    );
  }
  if (pullRequest) {
    return <PullRequestSummary pullRequest={pullRequest} project={project} />;
  }
  if (composing && defaultBranch) {
    return (
      <CreatePullRequestForm
        project={project}
        defaultBranch={defaultBranch}
        onDone={() => setComposing(false)}
      />
    );
  }
  // Offered only once we know the base, and never for the base itself.
  const canOpen = defaultBranch !== undefined && project.ref !== defaultBranch;
  return (
    <div className="space-y-2">
      <p className="text-xs text-[var(--text-tertiary)]">
        No pull request for this branch.
      </p>
      {canOpen ? (
        <Button
          type="button"
          size="sm"
          variant="secondary"
          className="w-full"
          onClick={() => setComposing(true)}
        >
          Open a pull request
        </Button>
      ) : null}
    </div>
  );
}

export interface ProjectBranchChipProps {
  project: ProjectSelection;
  /** Switching branches changes what gets cloned, so only before a run starts. */
  onChange?: (branch: string) => void;
  readOnly?: boolean;
  className?: string;
}

export function ProjectBranchChip({
  project,
  onChange,
  readOnly = false,
  className,
}: ProjectBranchChipProps) {
  const [open, setOpen] = useState(false);

  // Only ask GitHub about a branch while someone is looking at it.
  const { branches, isLoading: isLoadingBranches } = useRepoBranches(project, {
    enabled: open,
  });
  const branch = project.ref;

  if (!branch) return null;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="quiet"
          className={cn(CHIP_CLASS, "shrink", className)}
          aria-label={`Branch: ${branch}`}
          title={branch}
        >
          <GitBranch className="size-3.5 shrink-0" />
          <span className="truncate">{branch}</span>
          <ChevronDown className="size-3 shrink-0 text-[var(--text-tertiary)]" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[20rem] p-0">
        {!readOnly ? (
          <Command>
            <CommandInput placeholder="Switch branch…" />
            <CommandList className="max-h-48">
              <CommandEmpty>
                {isLoadingBranches ? "Loading branches…" : "No branches found."}
              </CommandEmpty>
              <CommandGroup heading="Branch">
                {branches.map((name) => (
                  <CommandItem
                    key={name}
                    value={name}
                    onSelect={() => {
                      onChange?.(name);
                      setOpen(false);
                    }}
                  >
                    <span className="flex-1 truncate">{name}</span>
                    {name === branch ? <Check className="ml-2 size-3.5 shrink-0" /> : null}
                  </CommandItem>
                ))}
              </CommandGroup>
            </CommandList>
          </Command>
        ) : null}

        <div
          className={cn(
            "space-y-3 p-3",
            !readOnly && "border-t border-[var(--border-subtle)]",
          )}
        >
          {readOnly ? (
            <div className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
              <GitBranch className="size-3.5 shrink-0" />
              <span className="truncate">{branch}</span>
            </div>
          ) : null}

          <BranchPullRequest
            key={`${project.owner}/${project.repo}#${branch}`}
            project={project}
            enabled={open}
          />
        </div>
      </PopoverContent>
    </Popover>
  );
}
