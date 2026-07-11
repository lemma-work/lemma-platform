import type { GeneratedClientAdapter } from "../generated.js";
import type { HttpClient } from "../http.js";
import type { FileChildrenResponse } from "../openapi_client/models/FileChildrenResponse.js";
import type { CreateFolderRequest } from "../openapi_client/models/CreateFolderRequest.js";
import type { DatastoreFileUploadRequest } from "../openapi_client/models/DatastoreFileUploadRequest.js";
import type { DirectoryTreeResponse } from "../openapi_client/models/DirectoryTreeResponse.js";
import { FileSearchScopeMode } from "../openapi_client/models/FileSearchScopeMode.js";
import type { FileSignedUrlRequest } from "../openapi_client/models/FileSignedUrlRequest.js";
import type { FileSignedUrlResponse } from "../openapi_client/models/FileSignedUrlResponse.js";
import type { FileUrlResponse } from "../openapi_client/models/FileUrlResponse.js";
import { SearchMethod } from "../openapi_client/models/SearchMethod.js";
import type { update } from "../openapi_client/models/update.js";
import type { attach } from "../openapi_client/models/attach.js";
import type { FileDetailResponse } from "../openapi_client/models/FileDetailResponse.js";
import { FilesService } from "../openapi_client/services/FilesService.js";

function trimLeadingSlashes(value: string): string {
  let start = 0;
  while (start < value.length && value[start] === "/") {
    start += 1;
  }
  return value.slice(start);
}

function trimTrailingSlashes(value: string): string {
  let end = value.length;
  while (end > 0 && value[end - 1] === "/") {
    end -= 1;
  }
  return value.slice(0, end);
}

function joinDatastorePath(basePath: string | undefined, leaf: string): string {
  const normalizedLeaf = trimLeadingSlashes(leaf);
  const trimmedBase = (basePath ?? "/").trim();
  const normalizedBase = trimmedBase.length > 0 ? trimmedBase : "/";
  if (normalizedBase === "/") {
    return `/${normalizedLeaf}`;
  }
  return `${trimTrailingSlashes(normalizedBase)}/${normalizedLeaf}`;
}

function getDirectoryPath(path: string): string {
  const normalized = path.trim();
  if (!normalized || normalized === "/") {
    return "/";
  }
  const withoutTrailing = trimTrailingSlashes(normalized);
  const index = withoutTrailing.lastIndexOf("/");
  if (index <= 0) {
    return "/";
  }
  return withoutTrailing.slice(0, index);
}

function getBaseName(path: string): string {
  const normalized = trimTrailingSlashes(path.trim());
  const index = normalized.lastIndexOf("/");
  if (index === -1) {
    return normalized;
  }
  return normalized.slice(index + 1);
}

type SearchMethodInput = SearchMethod | `${SearchMethod}`;
type FileSearchScopeModeInput = FileSearchScopeMode | `${FileSearchScopeMode}`;

export class FilesNamespace {
  constructor(
    private readonly client: GeneratedClientAdapter,
    private readonly http: HttpClient,
    private readonly podId: () => string,
  ) {}

  list(options: {
    limit?: number;
    pageToken?: string;
    directoryPath?: string;
    parentId?: string;
  } = {}) {
    const directoryPath = options.directoryPath ?? options.parentId ?? "/";
    return this.client.request(() => FilesService.fileList(
      this.podId(),
      directoryPath,
      options.limit ?? 100,
      options.pageToken,
    ));
  }

  get(path: string) {
    return this.client.request(() => FilesService.fileGet(this.podId(), path));
  }

  /**
   * URLs for a file: a short-lived download `url` plus a permanent
   * authenticated `app_url` deep-link that opens the file in the Lemma
   * frontend (the viewer must be a signed-in pod member).
   */
  getUrl(path: string): Promise<FileUrlResponse> {
    return this.client.request(() => FilesService.fileUrl(this.podId(), path));
  }

  /**
   * Mint a public, hit-capped short signed URL (no login needed to open).
   * Expires after `expiresSeconds` (default 3h, max 24h) and serves the file
   * at most `maxHits` times (default 50, max 100); both bounds are clamped
   * server-side. Use it to share a file outside the pod without unbounded egress.
   */
  createSignedUrl(
    path: string,
    options: { expiresSeconds?: number; maxHits?: number } = {},
  ): Promise<FileSignedUrlResponse> {
    const body: FileSignedUrlRequest = {
      expires_seconds: options.expiresSeconds,
      max_hits: options.maxHits,
    };
    return this.client.request(() => FilesService.fileSignedUrl(this.podId(), path, body));
  }

  delete(path: string) {
    return this.client.request(() => FilesService.fileDelete(this.podId(), path));
  }

  search(query: string, options: {
    limit?: number;
    scopeMode?: FileSearchScopeModeInput;
    scopePath?: string | null;
    searchMethod?: SearchMethodInput;
  } = {}) {
    return this.client.request(() => FilesService.fileSearch(this.podId(), {
      query,
      limit: options.limit ?? 10,
      scope_mode: options.scopeMode as FileSearchScopeMode | undefined,
      scope_path: options.scopePath,
      search_method: (options.searchMethod ?? SearchMethod.HYBRID) as SearchMethod,
    }));
  }

  download(path: string): Promise<Blob> {
    const encodedPath = encodeURIComponent(path);
    return this.http.requestBytes(
      "GET",
      `/pods/${this.podId()}/datastore/files/download?path=${encodedPath}`,
    );
  }

  tree(options: {
    rootPath?: string;
    filesPerDirectory?: number;
  } = {}): Promise<DirectoryTreeResponse> {
    return this.client.request(() =>
        FilesService.fileTree(
          this.podId(),
          options.rootPath ?? "/",
          options.filesPerDirectory ?? 3,
        ),
    );
  }

  upload(
    file: Blob,
    options: {
      name?: string;
      directoryPath?: string;
      parentId?: string;
      searchEnabled?: boolean;
      description?: string;
    } = {},
  ) {
    const payload: DatastoreFileUploadRequest = {
      data: file,
      name: options.name ?? (file instanceof File ? file.name : undefined),
      description: options.description,
      directory_path: options.directoryPath ?? options.parentId ?? "/",
      search_enabled: options.searchEnabled ?? true,
    };
    return this.client.request(() => FilesService.fileUpload(this.podId(), payload));
  }

  update(
    path: string,
    options: {
      file?: Blob;
      name?: string;
      description?: string;
      directoryPath?: string;
      parentId?: string;
      newPath?: string;
      searchEnabled?: boolean;
      visibility?: string | null;
    } = {},
  ) {
    const targetDirectory = options.directoryPath ?? options.parentId;
    const resolvedNewPath = options.newPath
      ?? (options.name
        ? joinDatastorePath(targetDirectory ?? getDirectoryPath(path), options.name)
        : undefined)
      ?? (targetDirectory
        ? joinDatastorePath(targetDirectory, getBaseName(path))
        : undefined);

    const payload: update = {
      path,
      data: options.file,
      description: options.description,
      new_path: resolvedNewPath,
      search_enabled: options.searchEnabled,
      visibility: options.visibility,
    };
    return this.client.request(() => FilesService.fileUpdate(this.podId(), payload));
  }

  readonly folder = {
    create: (
      name: string,
      options: {
        directoryPath?: string;
        parentId?: string;
        description?: string;
      } = {},
    ) => {
      const payload: CreateFolderRequest = {
        path: joinDatastorePath(options.directoryPath ?? options.parentId, name),
        description: options.description,
      };
      return this.client.request(() => FilesService.fileFolderCreate(this.podId(), payload));
    },
  };

  // Derived child files of a processed document (converted markdown, extracted
  // figures, and on-demand page renders), addressed by `<file-path>/<artifact>`.
  readonly children = {
    list: (path: string): Promise<FileChildrenResponse> =>
      this.client.request(() => FilesService.fileChildrenList(this.podId(), path)),

    content: (
      childPath: string,
      options: { pageStart?: number; pageEnd?: number } = {},
    ): Promise<Blob> => {
      const params = new URLSearchParams({ path: childPath });
      if (options.pageStart != null) params.set("page_start", String(options.pageStart));
      if (options.pageEnd != null) params.set("page_end", String(options.pageEnd));
      return this.http.requestBytes(
        "GET",
        `/pods/${this.podId()}/datastore/files/children/content?${params.toString()}`,
      );
    },

    markdown: (
      path: string,
      options: { pageStart?: number; pageEnd?: number } = {},
    ): Promise<Blob> => this.children.content(`${path}/document.md`, options),
  };

  // Bring-your-own markdown: replace a document's agent-facing `document.md`
  // (and its referenced images) with a user-authored version, or drop it to
  // revert to extraction. `attach` applies to non-markdown documents (PDF,
  // Word, HTML, …); companion images are named to match the markdown's
  // `![](fig.png)` references and served as sibling child artifacts.
  readonly markdown = {
    attach: (
      path: string,
      markdown: Blob,
      options: { images?: Blob[] } = {},
    ): Promise<FileDetailResponse> => {
      const formData: attach = {
        path,
        data: markdown,
        images: options.images,
      };
      return this.client.request(() =>
        FilesService.fileMarkdownAttach(this.podId(), formData),
      );
    },

    detach: (path: string): Promise<FileDetailResponse> =>
      this.client.request(() => FilesService.fileMarkdownDetach(this.podId(), path)),
  };
}
