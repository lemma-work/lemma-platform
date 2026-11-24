import type { AgentSurfaceResponse, SurfaceSetupResponse, SurfaceUpdateRequest } from "lemma-sdk";
import type { AccountConnect } from "../connect/accounts";
import type { ConnectOutcome } from "../connect/outcome";
import type { ConfigSource } from "@web/connect/install";
import type { ConnectorAccount } from "@web/data/accounts";
import type { PodConnector } from "@web/data/pod-connectors";
import type { Values } from "@web/connect/schema";
import type { Answerer, BotGroup, GuidedSetup, InviteLink, MySurface, PlaceOption, SlackAppWorkspace, SlackAppWorkspaces, SlackWorkspace, Surface } from "./types";
import type { Connectable } from "@web/data/connectable";
import type { SourceSettings } from "@web/channels/sources";

/** Where a Lemma is reached: WhatsApp, Telegram, email, Slack, Teams, Discord (PARITY.md, F).
 *
 *  Everything this part reads or changes, the same calls the web makes
 *  (`lemma-frontend/src/data/live.ts`, `channels/api.ts`). The live side is
 *  `live.ts`, the sample side `sample.ts`; screens reach it through
 *  `hooks.ts`, never the SDK directly, so sample data and a live account are
 *  the same screens. */
export interface ReachSource {
    /* ── where it answers ── */

    /** The places a Lemma answers, its agents' included. */
    places(podId: string): Promise<Surface[]>;
    /** Every place it could answer, and what each would take: Lemma's own
     *  identity, a setup of a minute, an account, or nothing yet. Cheapest
     *  first. */
    catalog(podId: string): Promise<Connectable[]>;
    /** The organization's connected accounts: one already authorised is not
     *  something to authorise again. */
    accounts(orgId: string): Promise<ConnectorAccount[]>;
    /** Where the reader's Lemmas answer, across the organization: the only
     *  way to learn that another Lemma already holds a platform. */
    mine(): Promise<MySurface[]>;

    /** Answer on Lemma's own number, bot or address. No account, no sign-in. */
    connectSystem(podId: string, platform: string): Promise<Surface>;
    /** The Lemma's connectors. A place answers as one account for everyone,
     *  so only a SHARED one can carry it. */
    podConnectors(podId: string): Promise<PodConnector[]>;
    /** Answer through one of the Lemma's shared connectors, by slug. */
    connectPodConnector(podId: string, platform: string, podConnector: string): Promise<Surface>;
    /** Answer as an account of the reader's own, just connected: shared into
     *  the Lemma as a SHARED connector first (the web's `connectOwnAccount`). */
    connectOwnAccount(podId: string, platform: string, accountId: string): Promise<Surface>;
    /** Stop answering there. Frees the account, and the organization's claim
     *  on Lemma's shared identity. */
    disconnect(podId: string, name: string): Promise<void>;

    /* ── one place ── */

    /** Its settings: who answers, what it may do, where. */
    detail(podId: string, name: string): Promise<AgentSurfaceResponse>;
    /** Whether it works, an administrator's approval, and the steps left. */
    setup(podId: string, name: string): Promise<SurfaceSetupResponse>;
    /** The Slack or Teams conversations it could answer in. */
    options(podId: string, name: string): Promise<PlaceOption[]>;
    /** Who could answer it: the Lemma, or one of its agents. */
    answerers(podId: string): Promise<Answerer[]>;
    /** Change what the screen changed and nothing else. */
    update(podId: string, name: string, patch: SurfaceUpdateRequest): Promise<void>;

    /* ── its groups ── */

    /** The groups it is in, with its settings in each. */
    groups(podId: string, name: string): Promise<BotGroup[]>;
    /** Join a private group the reader is not in (an admin). */
    joinGroup(podId: string, channelId: string): Promise<void>;
    /** One group's settings, read again: a WhatsApp group's invite link
     *  arrives a moment after the group. */
    group(podId: string, channelId: string, sourceId: string): Promise<SourceSettings>;
    /** Have its WhatsApp number start a group. */
    startWhatsAppGroup(podId: string, body: { surfaceName: string; title: string; answersOutsiders: boolean }): Promise<SourceSettings>;
    /** A link that adds its Telegram bot to a group the person picks. */
    telegramLink(podId: string, name: string): Promise<InviteLink>;

    /* ── connecting an account ── */

    /** Start a provider sign-in: the provider's page, the accounts usable
     *  before it, and the install it runs against. */
    startAccount(orgId: string, connectorId: string, options?: { authConfigId?: string; configSource?: ConfigSource }): Promise<AccountConnect>;
    /** Open the provider's page in the phone's browser and wait for the
     *  person to come back. Answers the outcome when the page named one. */
    signIn(url: string): Promise<ConnectOutcome | null>;
    /** The account that sign-in made, once it is usable; "" until then. */
    findAccount(orgId: string, connectorId: string, before: readonly string[], authConfigId?: string): Promise<string>;
    /** An account from credentials typed in (a bot token). Answers its id. */
    createAccount(orgId: string, entry: Connectable, credentials: Values): Promise<string>;
    removeAccount(orgId: string, accountId: string): Promise<void>;

    /* ── Telegram: Lemma's setup bot makes one ── */

    startGuided(podId: string, platform: string): Promise<GuidedSetup>;
    checkGuided(podId: string, setupId: string): Promise<GuidedSetup>;

    /* ── Slack ── */

    /** Workspaces Lemma's own app looks after for this organization. */
    slackManaged(podId: string): Promise<SlackWorkspace[]>;
    /** Make Lemma's own app the one that looks after the workspace this
     *  account authorised it in. */
    bindSlackManager(podId: string, accountId: string): Promise<SlackWorkspace>;
    /** Workspaces Lemma may make apps in, and whether the reader may let it. */
    slackAppWorkspaces(podId: string): Promise<SlackAppWorkspaces>;
    /** Save a configuration token's refresh token; Slack is asked at once. */
    saveSlackToken(podId: string, refreshToken: string): Promise<SlackAppWorkspace>;
    /** Make the Lemma's own Slack app; the same key answers with the same app. */
    createSlackApp(podId: string, teamId: string, key: string): Promise<{ appId: string; authConfigId: string }>;
    /** The manifest for an app made by hand, named after the Lemma. */
    slackManifest(name: string): Promise<Record<string, unknown>>;
    /** Register an app made by hand as the organization's own install.
     *  Answers the install's id. */
    addOwnApp(orgId: string, connectorId: string, name: string, config: Record<string, unknown>, kind?: string): Promise<string>;
}
