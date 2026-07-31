import type { GeneratedClientAdapter } from "../generated.js";
import type { IconUploadRequest } from "../openapi_client/models/IconUploadRequest.js";
import { IconsService } from "../openapi_client/services/IconsService.js";

export class IconsNamespace {
  constructor(private readonly client: GeneratedClientAdapter) {}

  upload(file: Blob) {
    const payload: IconUploadRequest = {
      file,
    };
    return this.client.request(() => IconsService.iconUpload(payload));
  }

  getPublic(iconPath: string) {
    return this.client.request(() => IconsService.iconPublicGet(iconPath));
  }
}
