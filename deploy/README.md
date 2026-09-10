# Deploying Lemma

Everything here puts Lemma somewhere other than a laptop. For a laptop, install
[Lemma Desktop](../docs/installation.md) — it needs none of this.

The full guide is **[docs/self-hosting.md](../docs/self-hosting.md)**.

## The short version

| Where | How | Agent sandboxes |
|---|---|---|
| **A VM you own** | [`compose/`](compose) — `./bootstrap.sh && docker compose up -d` | Local, on the VM's own Docker |
| A new DigitalOcean Droplet, Hetzner Server, EC2 instance… | [`cloud-init/lemma.yaml`](cloud-init/lemma.yaml) as user data | Local |
| Render | [`render.yaml`](../render.yaml) | **E2B only** |
| DigitalOcean App Platform | [`.do/deploy.template.yaml`](../.do/deploy.template.yaml) | **E2B only** |
| Railway | [`railway/railway.json`](railway/railway.json) | **E2B only** |

## The thing to know before choosing

Agents, functions and workflows all run in **sandboxes**, and a sandbox is a
container Lemma creates. On a VM it creates them on that machine's own Docker
daemon, which costs nothing extra and needs no account anywhere.

**Railway, Render and DigitalOcean App Platform do not give a container a Docker
socket.** No amount of configuration changes that. On those platforms Lemma has
to rent sandboxes from [E2B](https://e2b.dev) instead — a separate account, a
separate bill, and an `E2B_API_KEY`. Without one, the product runs and its
agents do not.

So: a $24/month Droplet running the compose stack does more than a
managed-platform deployment costing several times that. The managed templates
are here because people ask for them and because a managed database and TLS are
worth something — not because they are the better way to run Lemma.

If you use E2B, set `E2B_METADATA_NAMESPACE` to something unique per
deployment. Two deployments sharing an E2B account and a namespace will each
see the other's sandboxes as orphans and destroy them.

## Status of each template

Release CI installs the published images and runs this. To test a commit on
main before it is tagged, dispatch **Release Local Stack Images** from main with
publish unchecked, then dispatch **Compose deployment** with that run's id as
`images_run_id` — it installs exactly those images. Both are manual: images get
built when somebody asks for them.

The **compose stack is tested**: brought up from the published release images,
migrated, served over TLS on real hostnames, and provisioned with the product
scenario suite's cast over HTTPS. Release CI does the same on every release, so
it cannot rot quietly.

The **cloud-init file is the same commands wrapped for first boot**, but it has
not been run on a provider — it installs Docker and calls `bootstrap.sh`, which
is what is tested.

The **Render, App Platform and Railway templates are written but not deployed**
— validating them takes an account and a bill on each platform. They encode the
right service topology, the right environment, and the constraints above, and
they will need the small corrections a first real deploy always turns up. If you
run one, a pull request fixing what did not work is very welcome.

## Files that are not in this directory

`render.yaml` and `.do/deploy.template.yaml` live at the repository root because
Render and DigitalOcean only look for them there. They are listed above so this
page is still the index.
