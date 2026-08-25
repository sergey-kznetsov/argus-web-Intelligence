from __future__ import annotations

import uvicorn

from argus.web.app import create_web_app
from argus.web.config import WebSettings


def main() -> None:
    settings = WebSettings()
    uvicorn.run(create_web_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
