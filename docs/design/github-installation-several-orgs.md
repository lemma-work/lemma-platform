# One GitHub App installation, several Lemma organizations

**Status:** part 1 agreed and built on 2026-11-27. It changes an audit H4
rule: the three points under "For the reviewer to accept" need a yes from the
connectors owner before it merges. Parts 2 and 3 follow.

GitHub allows one installation of an App per GitHub account. A person who
works in two Lemma organizations against the same GitHub organization can
connect only one of them today, and nothing tells them why.

## What happens today

H4 (`services/auth/github_bindings.py`) binds an installation to exactly one
Lemma organization. `UNIQUE (connector_id, external_id)` on
`connector_installations` is the rule. A second organization's claim:

- is **refused** when the first binding is proven;
- **takes the binding over** when it is unproven (derived by migration 0063)
  and the claimant proves admin. The first organization silently loses its
  App tokens and its webhooks;
- **waits** (`PENDING_APPROVAL`) when the admin proof fails.

The admin proof, `github_installation.administers`, reads
`GET /user/memberships/orgs/{org}`. A GitHub App user token can read that only
when the installation granted the App **Members: read**. The manifest asks for
it, but an App registered before the manifest gained it does not. An
installation made before then has not accepted it either. In both cases every
admin of a GitHub *organization* is reported as "not an admin". Only
installations on a personal GitHub account can be proven.

Three pages then disagree:

- the account says `INSTALL_REQUIRED`;
- `/installations` says `PENDING_APPROVAL`;
- `/pods/{id}/connections` says `READY`, because standing comes from the pod
  connector's status and ignores the installation.

A plugin's Connect sends the person back to GitHub. GitHub reopens the same
installation, and the person comes back to the same state.

## Part 1: a binding per organization

**Rule.** An installation may be bound to any number of Lemma organizations.
Each binding is earned on its own, exactly as a first claim is today: the
claimant's GitHub user owns the installation, or is an admin of the GitHub
organization it is on, *and* holds `org.installation.manage` in that Lemma
organization. Nothing is inherited from another organization's binding.

**Table.** `connector_installations` keeps one row per (installation,
organization). The unique constraint becomes
`UNIQUE (connector_id, external_id, organization_id)`. This needs a migration
that only drops and re-creates the constraint. No data moves, and every
existing row stays valid. The new constraint's leading columns still serve
lookups by installation. Holding the extra organizations in `details` instead
would avoid the migration but lose the indexed `organization_id`, the
per-organization `proven_at`, and `bound_by_user_id`, which every read
filters or audits on. Downgrade keeps the oldest row per installation.

**What reads the binding:**

| Reader | Today | After |
|---|---|---|
| App token for an account (`routable_external_ref`) | installation bound to the account's org | same: a row for (installation, account's org) exists |
| Schedule routing key (`contracts/triggers.py`) | same | same |
| Webhooks (`webhook_sources/github.py`) | one `organization_id` | `organization_ids`: every bound org. `_in_organization` keeps schedules in any of them |
| APP mode (`bound_app_installations`) | proven rows of the org | unchanged: already per org |
| Project repositories (`bound_installations`) | rows of the org | unchanged |
| `installation.deleted` (`release_installation`) | deletes the row | deletes every row for the installation (the query already does) |

**Claims.**

- **Bound here:** nothing to do. A proven claim stamps an unproven row.
- **Bound elsewhere, proven or not:** no longer a refusal and no longer a
  takeover. A proven claim adds this organization's row. An unproven claim
  waits on an admin, as for an unbound installation.
- **Takeover is removed.** An organization holding an unproven, derived
  binding keeps it, as it would against an unbound claimant today. APP mode
  already ignores unproven rows. Its own next proven claim stamps it.

**Letting go.** `DELETE /organizations/{org}/connectors/github/installations/{id}`,
with `org.installation.manage`, deletes this organization's row only. It
clears `external_ref` on this organization's accounts that name the
installation, so they re-resolve. Other organizations are untouched.

**What H4 still protects, with a test each:**

- *Forged state* claims nothing: unchanged (H2, connect request state).
- *Seeing is not owning:* a contractor with read on one repository sees the
  installation and fails `administers`, so no row.
- *Squatting:* there is no first-come advantage left to squat on. A row needs
  its own proof, and holding one gives no say over another organization's row.
- *Webhook leakage:* a delivery fires only schedules in bound organizations.
  Each one is entitled to the installation on its own proof. Nothing from one
  organization reaches another: runs carry only the delivery and their own
  pod's context.
- *Uninstall* drops every binding and retires every account naming it, in
  every organization.

**For the reviewer to accept:**

1. A proven admin can bind the installation into any Lemma organization they
   may install apps for. GitHub's own model is the same: they could install
   any App on it.
2. A binding outlives its claimant's admin role, as today. A periodic
   re-proof is possible later. It is not proposed here.
3. Takeover goes away, so a squatted binding from before H4 is no longer
   evicted by the real owner's claim. It never reaches APP mode, and the
   accounts on it keep only what they had before H4.

## Part 2: one state everywhere

`/pods/{id}/connections` stops saying `READY` for GitHub unless this
organization holds the installation the person's account names (or APP mode
has a proven one). Otherwise `me.standing` is `install` and a new
`me.github` says why, with one action:

| `reason` | When | Says | Action |
|---|---|---|---|
| `install` | the account sees no installation | Install Lemma's GitHub App | GitHub install URL |
| `claim` | it sees one this org lacks, and the person is an admin | "Use {login}'s GitHub App here too" | bind it (existing endpoint) |
| `admin_approval` | not an admin | "An admin of {login} has to add it" | ask link |
| `accept_permission` | the installation lacks Members: read | "An owner of {login} must accept the App's new permission on GitHub" | installation settings URL |
| `choose` | several, none bound | pick one | bind it |

Another Lemma organization is named only if the viewer belongs to it.
Otherwise the copy says nothing about where else the installation is used.

The connect and reconnect callback ends on the same reasons. Coming back from
GitHub with an installation that is already bound claims it (with proof) or
shows the reason. It never returns silently.

## Part 3: plugins

The kit's `Connect` reads `/pods/{id}/connections` (SDK method added) and
draws the same reasons, so an app and the Connections page cannot disagree.
GitHub plugins prefer APP mode for anything that is not a person's own act.
Repositories are whatever the installation reaches, with no picking step. PR
review watches all of them by default, with a per-repository off switch. A
person's own GitHub is needed only for approve, merge and send back.

## Order

1. Land part 1. Granting Members: read *first* would let the next proven claim
   take an unproven binding over, and its holder would lose the installation.
2. Then grant Members: read on the App and accept it on the installation.
3. Parts 2 and 3.
