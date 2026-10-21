from __future__ import annotations

from fastapi import FastAPI

from app.app import create_app as create_api_app
from app.standalone import build_standalone_app


def create_standalone_app() -> FastAPI:
    return build_standalone_app(create_api_app())


app = create_standalone_app()


if __name__ == "__main__":
    import uvicorn

    from app.core.server_limits import WS_MAX_BYTES

    uvicorn.run(
        "standalone_app:app",
        host="0.0.0.0",
        port=8711,
        reload=False,
        ws="websockets-sansio",
        ws_max_size=WS_MAX_BYTES,
        access_log=False,
    )
