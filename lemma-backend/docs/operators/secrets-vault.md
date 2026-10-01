# Operating the secrets vault

Every stored secret is sealed under a data key, data keys are wrapped by a
key-encryption key (KEK) in `vault_keys`, and KEKs are wrapped by a **root key
that is never in the database**. This page is about the root and the KEKs.

## Choosing the root

| `SECRET_KEY_PROVIDER` | Root | Configure |
| --- | --- | --- |
| `gcp_kms` | A Cloud KMS symmetric key | `GCP_KMS_KEY_NAME=projects/<p>/locations/<l>/keyRings/<r>/cryptoKeys/<k>`; optionally `GCP_KMS_KEY_VERSION` to pin one version, `GCP_KMS_TIMEOUT_SECONDS`, `GCP_KMS_MAX_ATTEMPTS` |
| `static` | A keyset from the environment | `SECRET_ENCRYPTION_KEYSET` (JSON `[{"kid","key","primary"}]`) or `SECRET_ENCRYPTION_KEY` |
| `gcp_secret_manager` | A keyset stored in Secret Manager | `GCP_SECRET_MANAGER_SECRET_NAME` |
| `keychain` | The OS keychain | Local checkouts only |

`auto` picks `gcp_kms` when `GCP_KMS_KEY_NAME` is set, otherwise `static`.

### Cloud KMS setup

1. Create a symmetric `ENCRYPT_DECRYPT` key. Automatic rotation is fine: new
   wraps use the new primary version, and old versions keep unwrapping.
2. Grant `roles/cloudkms.cryptoKeyEncrypterDecrypter` **on that key** to the
   service account the API, worker and migration job run as (Workload Identity
   on GKE). No key file, no key in the environment.
3. Set `GCP_KMS_KEY_NAME` and deploy. The backend image includes the KMS client.
4. `uv run python scripts/vault_admin.py probe` round-trips a throwaway key.

KMS is called once per KEK when a process starts and when a KEK is created or
rotated — never per secret. Every call is in Cloud Audit Logs. A process that
cannot use its root (no permission, disabled key, malformed name) refuses to
start with a message naming the fix. Once running, a KMS outage does not
affect it.

## Backups and recovery

**Back up the root with the database.** For a keyset root that means the
keyset itself; for Cloud KMS it means never destroying a key version that
wrapped a KEK still in use (`vault_admin status` shows each KEK's `root_key_ref`).
A database restored without its root decrypts nothing.

## Rotation

| What | How |
| --- | --- |
| Root (Cloud KMS) | Rotate in KMS. Processes rewrap their KEKs under the new version only when you pin it with `GCP_KMS_KEY_VERSION`; otherwise old versions simply keep unwrapping. Moving to a different key: set the new `GCP_KMS_KEY_NAME`, keep IAM on the old one, restart — each process rewraps KEKs on load. |
| Root (keyset) | Add a new entry marked `primary`, restart. Processes rewrap KEKs under it on load. Remove the old entry after every process has restarted (`status` shows `needs_root_rewrap: false` for all keys). |
| KEK | `vault_admin rotate-kek`, then `vault_admin rewrap` (moves only the 60-byte wrapped data keys; batches of `VAULT_REWRAP_BATCH_SIZE`, safe beside traffic). When `status` shows the old KEK with 0 secrets, `vault_admin retire-kek <id>`. Other processes start writing under the new KEK within `VAULT_KEK_REFRESH_SECONDS`. |
| Signing key | `vault_admin rotate-sign-key`. Tokens signed with the old key keep verifying. Where `SECRET_ENCRYPTION_KEY` is set it stays the signing primary. |

After a suspected root or KEK compromise: rotate, rewrap, **and rotate the
underlying credentials with their providers** — anyone who held the database
and the old key already has the plaintext.

## Upgrading onto a database from before the vault

Migration `0044_vault_cutover` reads every old encrypted column once and moves it into
the vault, in one transaction. It needs the backend's key configuration: the old
`SECRET_ENCRYPTION_KEY(SET)` (or `CONNECTOR_ENCRYPTION_KEY`) to read, and the
root to create the first KEK. With Cloud KMS, the migration job's service
account needs the same IAM role. Any failure rolls back; fix and re-run.

It cannot be downgraded once secrets have moved — **take a backup before
upgrading**. Pods still running the previous release fail on the dropped
columns until the rollout finishes, so migrate and roll out together.
