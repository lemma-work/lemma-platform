import { describe, expect, it } from "vitest";
import { chatRoutes } from "../src/areas/chat/routes";
import { sampleChatOf } from "../src/areas/chat/sample-chats";
import { repositoryOf, repositoryPayload } from "../src/thread/repository";
import { byOwner, FIX_FALLBACK, fixLabel, MORE_INSTALLED, NO_REPOSITORY, repositoryFix, repositoryTitle } from "@web/thread/repositories";

/** Picking a GitHub repository when starting a chat (the web's #1192), and
 *  a chat that works in one saying which. */

const refused = (code: string) => Object.assign(new Error("Forbidden"), { statusCode: 403, code });

describe("the repository chip, in the web's words", () => {
    it("offers the step a refusal asks for, and nothing for one it cannot fix", () => {
        expect(repositoryFix(refused("GITHUB_INSTALLATION_REQUIRED"))).toBe("Install the GitHub app");
        expect(repositoryFix(refused("GITHUB_INSTALLATION_UNREACHABLE"))).toBe("Reconnect GitHub");
        expect(repositoryFix(refused("CONNECTION_REQUIRED"))).toBe("Connect GitHub");
        expect(repositoryFix(refused("CONNECTOR_NEEDS_ACCOUNT"))).toBe("Connect GitHub");
        expect(repositoryFix(refused("CONNECTOR_REAUTH_REQUIRED"))).toBe("Reconnect GitHub");
        /* No `github` connector, GitHub not answering, a dropped connection:
           a chat in no repository is the ordinary one. */
        expect(repositoryFix(refused("CONNECTOR_NOT_FOUND"))).toBeNull();
        expect(repositoryFix(new TypeError("Network request failed"))).toBeNull();
        expect(repositoryFix(null)).toBeNull();
        expect(fixLabel("Connect GitHub")).toBe("Connect GitHub to work in a repo");
        expect(FIX_FALLBACK).toBe("GitHub is connected but cannot reach a repository yet.");
    });

    it("says what is picked, and what nothing picked means", () => {
        expect(NO_REPOSITORY).toBe("No repository");
        expect(MORE_INSTALLED).toBe("More are installed than shown");
        expect(repositoryTitle(null)).toBe("Work in a GitHub repository");
        expect(repositoryTitle("acme/web")).toBe("Works in acme/web, on a branch of its own");
    });

    it("lists each GitHub account's repositories together, in the order given", () => {
        const items = [
            { fullName: "acme/web", private: true },
            { fullName: "ada/notes", private: false },
            { fullName: "acme/api", private: true },
        ];
        expect(byOwner(items).map(([owner, repos]) => [owner, repos.map((repo) => repo.fullName)])).toEqual([
            ["acme", ["acme/web", "acme/api"]],
            ["ada", ["ada/notes"]],
        ]);
        expect(byOwner([])).toEqual([]);
    });
});

describe("a new chat, created in a repository", () => {
    it("goes with the create as `repository`, and nothing when none is picked", () => {
        expect(repositoryPayload("acme/web")).toEqual({ repository: "acme/web" });
        expect(repositoryPayload(null)).toEqual({});
    });

    it("rides to the chat it opens with the words and the files", () => {
        expect(chatRoutes.start("pod-1", "Fix the footer", "files-1", "acme/web")).toBe("/chat/pod-1/new?ask=Fix%20the%20footer&files=files-1&repository=acme%2Fweb");
        expect(chatRoutes.start("pod-1", "Fix the footer", null, "acme/web")).toBe("/chat/pod-1/new?ask=Fix%20the%20footer&repository=acme%2Fweb");
        expect(chatRoutes.start("pod-1", "Fix the footer")).toBe("/chat/pod-1/new?ask=Fix%20the%20footer");
    });
});

describe("the repository a chat works in", () => {
    it("is read off its worktree, and off the shared checkout one made before worktrees works in", () => {
        expect(repositoryOf({ workspace_cwd: "/home/user/lemma/worktrees/acme/web/k3m9q2ab" })).toBe("acme/web");
        expect(repositoryOf({ workspace_cwd: "/home/user/lemma/worktrees/Acme-Labs/site.v2/k3m9q2ab/" })).toBe("Acme-Labs/site.v2");
        expect(repositoryOf({ workspace_cwd: "/home/user/lemma/repos/acme/web" })).toBe("acme/web");
    });

    it("is none for every other directory", () => {
        expect(repositoryOf({ workspace_cwd: "/home/user/lemma/c/2026-11-10/k3m9q2ab" })).toBeNull();
        expect(repositoryOf({ workspace_cwd: "/home/user/lemma/worktrees/acme/web" })).toBeNull();
        expect(repositoryOf({ workspace_cwd: "/home/user/lemma/worktrees/acme/web/k3/more" })).toBeNull();
        expect(repositoryOf({ workspace_cwd: "/Users/sam/code/repos/acme/web" })).toBeNull();
        expect(repositoryOf({ workspace_cwd: "/home/user/lemma/repos/-acme/web" })).toBeNull();
        expect(repositoryOf({ workspace_cwd: "/home/user/lemma/repos/acme/.." })).toBeNull();
        expect(repositoryOf({})).toBeNull();
        expect(repositoryOf(null)).toBeNull();
    });

    it("shows in a sample chat that works in one", () => {
        const now = Date.UTC(2026, 10, 10, 12);
        expect(repositoryOf(sampleChatOf("launch-copy", now)?.conversation)).toBe("northwind-lamps/storefront");
        expect(repositoryOf(sampleChatOf("support-refund", now)?.conversation)).toBeNull();
    });
});
