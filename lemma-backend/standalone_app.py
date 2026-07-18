from __future__ import annotations

from fastapi import FastAPI

from app.app import create_app as create_api_app
from app.standalone import build_standalone_app


def create_standalone_app() -> FastAPI:
    from app.events import streaq_worker

    return build_standalone_app(create_api_app(), streaq_worker)


app = create_standalone_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "standalone_app:app",
        host="0.0.0.0",
        port=8711,
        reload=False,
        ws="websockets-sansio",
        access_log=False,
    )
