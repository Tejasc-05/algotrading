"""FastAPI application factory: assembles middleware, exception handlers, and
every route module behind one consistent app instance."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.api.routes.health import router as health_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.database.database import engine

settings = get_settings()
configure_logging(debug=settings.debug)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("AlgoFlow API starting", extra={"environment": settings.environment})
    yield
    await engine.dispose()
    logger.info("AlgoFlow API shut down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AlgoFlow API",
        description="No-code AI-powered algorithmic trading platform — backend engine.",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(api_router)

    from app.websocket.manager import websocket_endpoint
    app.add_api_websocket_route("/ws", websocket_endpoint)

    return app


app = create_app()
