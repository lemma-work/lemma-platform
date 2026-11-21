# pod_bundle contract

What every `pod_bundle` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `pod.bundle.download` | GET | `/pods/bundle/download` | Download A Bundle Archive |
| `pod.bundle.export.get` | GET | `/pods/{pod_id}/bundle/exports/{export_id}` | Get Pod Export Status |
| `pod.bundle.export.start` | POST | `/pods/{pod_id}/bundle/exports` | Start Pod Export |
| `pod.bundle.import.apply` | POST | `/pods/{pod_id}/bundle/imports/{import_id}/apply` | Apply Pod Import |
| `pod.bundle.import.cancel` | DELETE | `/pods/{pod_id}/bundle/imports/{import_id}` | Cancel Pod Import |
| `pod.bundle.import.events` | GET | `/pods/{pod_id}/bundle/imports/{import_id}/events` | Stream Pod Import Progress |
| `pod.bundle.import.get` | GET | `/pods/{pod_id}/bundle/imports/{import_id}` | Get Pod Import Status |
| `pod.bundle.import.replan` | POST | `/pods/{pod_id}/bundle/imports/{import_id}/replan` | Re-plan Pod Import |
| `pod.bundle.import.start` | POST | `/pods/{pod_id}/bundle/imports` | Start Pod Import |
| `pod.bundle.install.get` | GET | `/pods/{pod_id}/bundle/installs/{install_id}` | Get Plugin Install Status |
| `pod.bundle.install.retry` | POST | `/pods/{pod_id}/bundle/installs/{install_id}/retry` | Retry Failed Plugins |
| `pod.bundle.install.start` | POST | `/pods/{pod_id}/bundle/installs` | Install Plugins |
| `pod.bundle.publish.events` | GET | `/pods/{pod_id}/bundle/publishes/{publish_id}/events` | Stream Pod Publish Progress |
| `pod.bundle.publish.get` | GET | `/pods/{pod_id}/bundle/publishes/{publish_id}` | Get Pod Publish Status |
| `pod.bundle.publish.start` | POST | `/pods/{pod_id}/bundle/publishes` | Publish Pod To GitHub |
| `pod.bundle.templates` | GET | `/pods/bundle/templates` | Templates to start a Lemma from |
| `pod.bundle.upload` | POST | `/pods/{pod_id}/bundle/uploads` | Stage A Local Bundle Upload |
| `pod.start` | POST | `/pods/start` | Start a Lemma |

<!-- /generated:operations -->

## Pod machines in a bundle

`pod.bundle.export.start` writes each of the pod's own machines as
`machines/<name>/<name>.json` (definition only: name, display name, template,
size, visibility, run slots). `pod.bundle.import.*` plans one `MACHINE` step per
bundled machine — `UPDATE` when the pod has one of that name, `CREATE`
otherwise — ahead of the agents that run on it and the grants that name it.
Creating one is refused as the `/pods/{pod_id}/machines` route refuses it: the
importer must be a pod editor, and the plan must offer the template and size
and have room for another machine; the step then fails with that reason.

## Installing several plugins

`pod.bundle.install.start` needs pod update, as an import does, and refuses
before anything is written: a plugin named twice or more than ten, a
repository it cannot read from `repo_url` or `owner` and `repo`, or a daily
import limit the plugins would pass (each counts as one import). It writes one
`QUEUED` import per plugin and one `pod_bundle.install_plugin` work item for
each, in one transaction, and answers `202` before any is fetched. The items
run one at a time in the order named, whether or not anybody reads the
install. Each fetches, plans and applies its plugin with the install page's
defaults, checked as `pod.bundle.import.apply` checks a person's choices. A
plugin the page would ask about is withdrawn with nothing applied
(`NEEDS_ANSWER`, the question in `reason`; its import is `CANCELLED`); a plugin
that fails keeps the steps it applied (`FAILED`). Neither stops the plugins
after it. When the last one settles, the Lemma posts one note in the starter's
own chat with it, in the item's completion transaction: never pushed outside
the app.

`pod.bundle.install.get` needs pod read and answers only the person who
started the install; anyone else, and an id that names none, gets 404
(`POD_BUNDLE_IMPORT_NOT_FOUND`).

`pod.bundle.install.retry` needs pod update and the same starter. It queues
each `FAILED` plugin again, with the next attempt: one with a plan is approved
and applied again as the same import, so steps already applied are not
repeated; one that failed before planning starts over. Others are untouched,
and an install with nothing failed is answered as it is.
