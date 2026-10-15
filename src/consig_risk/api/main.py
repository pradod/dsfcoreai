"""API HTTP do sistema. A UI fala só com esta API; o agent será exposto aqui."""

from fastapi import FastAPI

from consig_risk import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="Consig Risk API", version=__version__)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app
