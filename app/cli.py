"""The `openstride` console script."""
import uvicorn

from app.config import settings


def main() -> None:
    uvicorn.run("app.main:app", host=settings.host, port=settings.port)
