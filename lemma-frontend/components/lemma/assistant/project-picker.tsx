"use client";

/**
 * Choosing what a conversation works on, before it starts.
 *
 * The chip sits in the composer next to the model picker and answers one
 * question: which repository is this conversation in? Picking one binds the
 * conversation to that project — the agent's working directory becomes the
 * checkout, and the repo is cloned before its first command.
 *
 * It is deliberately quiet when there is nothing to choose. A workspace with no
 * GitHub account connected shows the scratchpad it already has, and offers the
 * connection rather than an empty list, because an empty picker reads as
 * breakage.
 *
 * Once a conversation exists the choice is fixed: its directory is stamped in
 * metadata, an agent is already working there, and swapping it underneath would
 * strand the run. `readOnly` renders the same chip as a plain label.
 */

import Link from "next/link";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Check, ChevronDown, Folder, Github, Lock } from "@/components/ui/icons";
import { cn } from "@/lib/utils";
import type { GithubProject } from "@/lib/hooks/use-github-projects";
import { projectLabel, type ProjectSelection } from "@/lib/assistant/project-selection";
import { folderLabel } from "@/lib/hooks/use-conversation-folder";

const CHIP_CLASS =
  "inline-flex h-8 min-w-0 max-w-[14rem] items-center gap-1.5 rounded-md px-2 text-xs text-[var(--text-secondary)]";

// The branch is deliberately absent: `ProjectBranchChip` sits next to this one
// and names it, and printing it in both places says the same thing twice.
function ChipBody({
  project,
  localFolder,
}: {
  project: ProjectSelection | null;
  localFolder?: string | null;
}) {
  if (project) {
    return (
      <>
        <Github className="size-3.5 shrink-0" />
        <span className="truncate">{projectLabel(project)}</span>
      </>
    );
  }
  if (localFolder) {
    return (
      <>
        <Folder className="size-3.5 shrink-0" />
        <span className="truncate">{folderLabel(localFolder)}</span>
      </>
    );
  }
  return (
    <>
      <Folder className="size-3.5 shrink-0" />
      <span className="truncate">Scratchpad</span>
    </>
  );
}

export interface ProjectPickerProps {
  value: ProjectSelection | null;
  onChange: (project: ProjectSelection | null) => void;
  projects: GithubProject[];
  isConnected: boolean;
  isLoadingProjects: boolean;
  /** A failed list is not an empty one, and must never be shown as one. */
  error?: unknown;
  accountId?: string;
  /** A conversation that already exists cannot be moved; show, don't offer. */
  readOnly?: boolean;
  /** Where "Connect GitHub" goes — org-scoped, so the caller knows it. */
  connectHref: string;
  /**
   * The folder on this computer this conversation works in, if any.
   *
   * Only ever set on a local install. The path is display-only here: choosing
   * one is `onPickLocalFolder`, which raises a dialog in the desktop shell, so
   * this page never names a directory.
   */
  localFolder?: string | null;
  /** Absent when this installation cannot offer a folder at all. */
  onPickLocalFolder?: () => void;
  onClearLocalFolder?: () => void;
  /**
   * Is GitHub worth offering?
   *
   * A GitHub connection needs an app registration, secrets in the environment
   * and a reachable webhook, none of which a local install generally has — so
   * "Connect GitHub" there is a button that cannot be finished. Offered only
   * where a connection already exists, or where one could be made.
   */
  canConnectGithub?: boolean;
  className?: string;
}

export function ProjectPicker({
  value,
  onChange,
  projects,
  isConnected,
  isLoadingProjects,
  error,
  accountId,
  readOnly = false,
  connectHref,
  localFolder = null,
  onPickLocalFolder,
  onClearLocalFolder,
  canConnectGithub = true,
  className,
}: ProjectPickerProps) {
  const [open, setOpen] = useState(false);

  const sorted = useMemo(
    () =>
      [...projects].sort((a, b) => (b.updatedAt || "").localeCompare(a.updatedAt || "")),
    [projects],
  );

  if (readOnly) {
    // No project is the ordinary case, and saying "Scratchpad" on every
    // conversation that never wanted one is noise.
    if (!value && !localFolder) return null;
    return (
      <span
        className={cn(CHIP_CLASS, className)}
        title={value ? projectLabel(value) : (localFolder ?? undefined)}
      >
        <ChipBody project={value} localFolder={localFolder} />
      </span>
    );
  }

  // Choosing a folder and choosing a repo are the same choice, so each clears
  // the other: a conversation works in one place.
  const pickFolder = () => {
    onChange(null);
    onPickLocalFolder?.();
    setOpen(false);
  };

  const selectScratchpad = () => {
    onClearLocalFolder?.();
    onChange(null);
    setOpen(false);
  };

  const select = (project: GithubProject | null) => {
    if (project && localFolder) onClearLocalFolder?.();
    onChange(
      project
        ? { owner: project.owner, repo: project.repo, ref: project.ref, accountId }
        : null,
    );
    setOpen(false);
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="quiet"
          className={cn(CHIP_CLASS, "shrink", className)}
          aria-label={
            value
              ? `Project: ${projectLabel(value)}`
              : localFolder
                ? `Folder: ${localFolder}`
                : "Choose where this conversation works"
          }
        >
          <ChipBody project={value} localFolder={localFolder} />
          <ChevronDown className="size-3 shrink-0 text-[var(--text-tertiary)]" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[20rem] p-0">
        {!isConnected && !canConnectGithub ? (
          // A local install: the agents run here with the person's own tools, so
          // a folder on this computer is the useful answer and a repo clone is
          // not on offer at all.
          <Command>
            <CommandList>
              <CommandGroup heading="Where this conversation works">
                <CommandItem value="__scratchpad__" onSelect={() => selectScratchpad()}>
                  <Folder className="mr-2 size-3.5 shrink-0" />
                  <span className="flex-1 truncate">Scratchpad</span>
                  {!localFolder ? <Check className="size-3.5" /> : null}
                </CommandItem>
                {localFolder ? (
                  <CommandItem value="__bound_folder__" onSelect={() => pickFolder()}>
                    <Folder className="mr-2 size-3.5 shrink-0" />
                    <span className="flex-1 truncate" title={localFolder}>
                      {folderLabel(localFolder)}
                    </span>
                    <Check className="size-3.5" />
                  </CommandItem>
                ) : null}
                {onPickLocalFolder ? (
                  <CommandItem value="__choose_folder__" onSelect={() => pickFolder()}>
                    <Folder className="mr-2 size-3.5 shrink-0" />
                    <span className="flex-1 truncate">
                      {localFolder ? "Choose a different folder…" : "Choose a folder…"}
                    </span>
                  </CommandItem>
                ) : null}
              </CommandGroup>
            </CommandList>
          </Command>
        ) : !isConnected ? (
          <div className="p-3">
            <p className="text-sm text-[var(--text-primary)]">Work in a repository</p>
            <p className="mt-1 text-xs text-[var(--text-tertiary)]">
              Connect GitHub and a conversation can start inside one of your repos,
              cloned and authenticated, instead of an empty directory.
            </p>
            <Button asChild variant="secondary" size="sm" className="mt-3 w-full">
              <Link href={connectHref}>Connect GitHub</Link>
            </Button>
          </div>
        ) : error ? (
          <div className="p-3">
            <p className="text-sm text-[var(--text-primary)]">Couldn&apos;t list your repositories</p>
            <p className="mt-1 text-xs text-[var(--text-tertiary)]">
              GitHub is connected, but the connector could not read your repos. That
              usually means access was revoked, or this environment&apos;s connector
              catalog is out of date.
            </p>
            <Button asChild variant="secondary" size="sm" className="mt-3 w-full">
              <Link href={connectHref}>Check the connection</Link>
            </Button>
          </div>
        ) : (
          <Command>
            <CommandInput placeholder="Search repositories…" />
            <CommandList>
              <CommandEmpty>
                {isLoadingProjects ? "Loading repositories…" : "No repositories found."}
              </CommandEmpty>
              <CommandGroup>
                <CommandItem value="__scratchpad__" onSelect={() => selectScratchpad()}>
                  <Folder className="mr-2 size-3.5 shrink-0" />
                  <span className="flex-1 truncate">Scratchpad</span>
                  {value === null && !localFolder ? <Check className="size-3.5" /> : null}
                </CommandItem>
                {onPickLocalFolder ? (
                  <CommandItem value="__choose_folder__" onSelect={() => pickFolder()}>
                    <Folder className="mr-2 size-3.5 shrink-0" />
                    <span className="flex-1 truncate" title={localFolder ?? undefined}>
                      {localFolder ? folderLabel(localFolder) : "Choose a folder…"}
                    </span>
                    {localFolder ? <Check className="size-3.5" /> : null}
                  </CommandItem>
                ) : null}
              </CommandGroup>
              <CommandGroup heading="Repositories">
                {sorted.map((project) => {
                  const selected =
                    value?.owner === project.owner && value?.repo === project.repo;
                  return (
                    <CommandItem
                      key={project.fullName}
                      value={project.fullName}
                      onSelect={() => select(project)}
                    >
                      <span className="flex-1 truncate">{project.fullName}</span>
                      {project.private ? (
                        <Lock className="ml-2 size-3 shrink-0 text-[var(--text-tertiary)]" />
                      ) : null}
                      {selected ? <Check className="ml-2 size-3.5 shrink-0" /> : null}
                    </CommandItem>
                  );
                })}
              </CommandGroup>
            </CommandList>
          </Command>
        )}
      </PopoverContent>
    </Popover>
  );
}
