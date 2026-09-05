from __future__ import annotations

import uvicorn

from app import create_app
from app.core.config import get_settings


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(create_app(), host="127.0.0.1", port=settings.port, log_level="info")
