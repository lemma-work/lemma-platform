"""In-process document processor for local and desktop installs.

Only the transport differs from the Kreuzberg adapter, so only the transport is
replaced: everything downstream — markdown assembly, page markers, image
de-duplication, chunk page-mapping — is inherited, and stays byte-identical to
the container path instead of being written a second time and drifting.
"""

from __future__ import annotations

import importlib.util

from app.modules.datastore.infrastructure.document_processor import (
    KreuzbergDocumentProcessor,
)
from app.modules.datastore.infrastructure.xberg_local_client import (
    _INSTALL_HINT,
    XbergLocalClient,
)


class XbergDocumentProcessor(KreuzbergDocumentProcessor):
    """Kreuzberg's normalizer over an in-process extractor.

    Page rendering is inherited too, and deliberately stays pypdfium2: xberg
    exposes no page rasteriser, and the shared mixin exists so page images are
    identical whichever extractor produced the text.
    """

    def __init__(self, client: object | None = None):
        if client is None and importlib.util.find_spec("xberg") is None:
            raise ImportError(_INSTALL_HINT)
        super().__init__(client or XbergLocalClient())  # type: ignore[arg-type]
