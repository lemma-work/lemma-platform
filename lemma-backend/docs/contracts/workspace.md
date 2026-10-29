# workspace contract

What every `workspace` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `machine.browser.access` | POST | `/machines/{ref}/browser:access` | A signed link to a machine's browser |
| `machine.browser.beacon` | POST | `/machines/{ref}/browser:beacon` | Report how a live screen session went |
| `machine.browser.command` | POST | `/machines/{ref}/browser:command` | Steer a machine's browser from the screen's toolbar |
| `machine.browser.current_page_url` | GET | `/machines/{ref}/browser/current-page-url` | What page a sign-in's browser is actually showing |
| `machine.browser.display` | POST | `/machines/{ref}/browser:display` | Set the size of a machine's browser display |
| `machine.browser.navigate` | POST | `/machines/{ref}/browser:navigate` | Open a page in a machine's browser |
| `machine.browser.status` | GET | `/machines/{ref}/browser` | Whether a machine's browser can be watched |
| `machine.consent.grant` | POST | `/machine-consents` | Let an app run code on your machine |
| `machine.consent.list` | GET | `/machine-consents` | Apps you let run code on your machine |
| `machine.consent.revoke` | DELETE | `/machine-consents/{app_id}` | Stop an app running code on your machine |
| `machine.files.content` | GET | `/machines/{ref}/files:content` | Read a file on a machine |
| `machine.files.delete` | DELETE | `/machines/{ref}/files` | Delete a file or directory on a machine |
| `machine.files.list` | GET | `/machines/{ref}/files` | List a directory on a machine |
| `machine.files.move` | POST | `/machines/{ref}/files:move` | Move or rename a path on a machine |
| `machine.files.stat` | GET | `/machines/{ref}/files:stat` | Stat one path on a machine |
| `machine.files.sync` | POST | `/machines/{ref}/files:sync` | Copy files between a machine and pod files |
| `machine.files.write` | PUT | `/machines/{ref}/files/content` | Write a file on a machine |
| `machine.get` | GET | `/machines/{ref}` | Get one machine |
| `machine.list` | GET | `/machines` | List the machines you can see |
| `machine.pod.create` | POST | `/pods/{pod_id}/machines` | Create a machine the pod's agents share |
| `machine.pod.delete` | DELETE | `/pods/{pod_id}/machines/{name}` | Delete a pod machine and its disk |
| `machine.pod.grants.get` | GET | `/pods/{pod_id}/machines/{name}/grants` | Who is granted a pod machine |
| `machine.pod.grants.set` | PUT | `/pods/{pod_id}/machines/{name}/grants` | Replace who is granted a pod machine |
| `machine.pod.update` | PATCH | `/pods/{pod_id}/machines/{name}` | Rename, resize or change who sees a pod machine |
| `machine.policy` | GET | `/machines:policy` | Which machines you or a pod may create |
| `machine.preview.create` | POST | `/machines/{ref}/previews` | Open a port on a machine as a preview URL |
| `machine.preview.list` | GET | `/machines/{ref}/previews` | A machine's live previews |
| `machine.preview.open` | GET | `/machine-previews/{label}:open` | Open a private preview as the signed-in person |
| `machine.preview.revoke` | DELETE | `/machines/{ref}/previews/{preview_id}` | Revoke a preview; its URL stops answering |
| `machine.process.get` | GET | `/machines/{ref}/processes/{process_id}` | One process |
| `machine.process.input` | POST | `/machines/{ref}/processes/{process_id}:input` | Type into a process |
| `machine.process.kill` | DELETE | `/machines/{ref}/processes/{process_id}` | Stop a process |
| `machine.process.list` | GET | `/machines/{ref}/processes` | What is running on a machine |
| `machine.process.output` | GET | `/machines/{ref}/processes/{process_id}/output` | A page of a process's output |
| `machine.process.resize` | POST | `/machines/{ref}/processes/{process_id}:resize` | Resize a process's terminal |
| `machine.process.start` | POST | `/machines/{ref}/processes` | Start a process on a machine |
| `machine.release` | POST | `/machines/{ref}:release` | Stop a machine's compute, keeping its disk |
| `machine.reset` | POST | `/machines/{ref}:reset` | Clean a machine by scope |
| `machine.restart` | POST | `/machines/{ref}:restart` | Stop a machine and start it again |
| `machine.startup` | GET | `/machines/{ref}/startup` | Whether a machine is ready, without waking it |

<!-- /generated:operations -->

## `machine.startup`

What a machine is doing, for a page waiting on it. Never
starts or wakes anything: asking must not be what wakes a sleeping computer.

- `ready`: running.
- `downloading`: fetching its image, which the first start after an update does.
- `starting`: coming up.
- `asleep`: not running; starts on first use.
- `unavailable`: the answer could not be read; poll again.

`detail` is a sentence for a person while `downloading` or `starting`.
`done_mb` and `total_mb` report download progress while `downloading`, once
the guest has the image's manifest; before that, and on fabrics that cannot
measure it, they are absent.

## `machine.files.list`

Lists one directory on a machine. The ref is resolved like every machine route:
`me` is the caller's own, and a pod machine needs access to it.

**Ambient by default.** A paused machine is not started to answer; the response
comes back with `sleeping: true` and no entries. `wake=true` starts it. This is
deliberate: idle release is 900 seconds, so a file pane that started a machine on
every render would hold compute open for as long as the pane was on screen.

Paths resolve under the machine's home (`home_root`) and refuse anything outside
it, `/tmp` included — that is where a credential is staged, and an HTTP route
able to read it would publish it to any request carrying the caller's session.
The browser's profile, inside the home, is never served either.

Returns at most 1000 entries with `truncated: true` when there are more.

## `machine.files.stat`

One entry's kind, size and modification time. Same path rules as the listing.
Unlike the listing this always starts the machine, because a caller asking
about one named path wants an answer rather than a "not now".

## `machine.files.content`

Streams a byte range of one file, defaulting to the first 8 MB. Served as
`application/octet-stream` with `Content-Disposition: attachment` and
`X-Content-Type-Options: nosniff`, because a machine's files are the person's own
content and must never render as markup on the API origin.

Refuses with 404 for a path that is not there, 413 for a file larger than the
endpoint serves in one request, and 503 when the machine cannot be reached.

## `machine.browser.current_page_url`

What page the browser signing in to `origin` is actually showing, for the
anti-phishing display on the sign-in page to poll. Scoped to the machine the
ref names, resolved as every machine route is. `null`, not an error, whenever nothing can be
read (the sandbox asleep, the browser not up, the fabric unreachable): this
renders next to a live picture, so it answers with "nothing to show yet"
rather than breaking the page. Never wakes a sandbox or starts a browser --
only a sign-in beginning does that, elsewhere -- so polling it costs nothing
extra to a session already idle.
