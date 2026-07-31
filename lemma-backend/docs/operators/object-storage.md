# Object storage

Lemma uses [Obstore](https://developmentseed.org/obstore/latest/api/store/) for
private datastore documents, app assets, function source, and bundle staging.
Choose the adapter and location with two Lemma settings:

| Backend | `STORAGE_BACKEND` | `STORAGE_BUCKET` |
| --- | --- | --- |
| Local filesystem | `local` | Absolute or relative storage directory |
| Google Cloud Storage | `gcs` | Bucket name |
| Amazon S3 or S3-compatible storage | `s3` | Bucket name |
| Azure Blob Storage | `azure` | Container name |

There is no configurable object prefix. Lemma assigns its own module and
resource namespaces inside the selected location so datastore, app, function,
and staging objects cannot collide.

## Local filesystem

```dotenv
STORAGE_BACKEND=local
STORAGE_BUCKET=/var/lib/lemma/objects
```

The API and worker must mount the same directory when they run in separate
containers or processes.

## Google Cloud Storage

```dotenv
STORAGE_BACKEND=gcs
STORAGE_BUCKET=lemma-documents
GOOGLE_APPLICATION_CREDENTIALS=/var/run/secrets/google/service-account.json
```

Obstore uses Google Application Default Credentials, so production workloads
can use workload identity instead of a credential file. See the
[Obstore GCS adapter documentation](https://developmentseed.org/obstore/latest/api/store/gcs/).

## Amazon S3

```dotenv
STORAGE_BACKEND=s3
STORAGE_BUCKET=lemma-documents
AWS_REGION=us-east-1
```

Obstore reads the standard AWS credential chain, including workload roles and
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN`. Its S3
configuration also supports endpoint environment settings for S3-compatible
services. See the
[Obstore S3 adapter documentation](https://developmentseed.org/obstore/latest/api/store/aws/).

## Azure Blob Storage

```dotenv
STORAGE_BACKEND=azure
STORAGE_BUCKET=lemma-documents
AZURE_STORAGE_ACCOUNT_NAME=exampleaccount
```

Obstore reads Azure credentials from the environment or the configured Azure
identity. For example, deployments can provide an account key, SAS token, or
workload identity through the standard Azure settings described in the
[Obstore Azure adapter documentation](https://developmentseed.org/obstore/latest/api/store/azure/).

## Compatibility

`STORAGE_BACKEND=auto` preserves the previous behavior: local and testing
environments use the filesystem, while a production environment with a bucket
uses GCS. New S3 and Azure deployments must select their backend explicitly.

`GCS_STORAGE_BUCKET`, `LOCAL_OBJECT_STORAGE_ROOT`, and
`LOCAL_FILE_STORAGE_ROOT` remain accepted for existing deployments. New
deployments should use `STORAGE_BUCKET`. `PUBLIC_BUCKET_NAME` remains separate
because it controls public icon assets rather than private object storage.
