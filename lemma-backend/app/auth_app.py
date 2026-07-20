from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from supertokens_python import get_all_cors_headers
from supertokens_python.framework.fastapi import get_middleware

from app.core.cors import get_allowed_cors_origin_regex, get_allowed_cors_origins
from app.modules.identity.infrastructure.supertokens_auth.initialization import (
    initialize_supertokens,
)
from app.modules.identity.infrastructure.supertokens_auth.abuse_middleware import (
    AuthAbuseMiddleware,
)


def get_auth_app():
    fastapi_app = FastAPI()
    initialize_supertokens()
    fastapi_app.add_middleware(get_middleware())
    fastapi_app.add_middleware(AuthAbuseMiddleware)

    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=get_allowed_cors_origins(),
        allow_origin_regex=get_allowed_cors_origin_regex(),
        allow_credentials=True,
        allow_methods=["GET", "PUT", "POST", "DELETE", "OPTIONS", "PATCH"],
        allow_headers=["Content-Type", "x-altcha-payload"] + get_all_cors_headers(),
        # SuperTokens sets these as expose headers per-response; list them
        # explicitly so this stays correct regardless of middleware layering.
        expose_headers=[
            "front-token",
            "anti-csrf",
            "st-access-token",
            "st-refresh-token",
        ],
    )

    return fastapi_app
