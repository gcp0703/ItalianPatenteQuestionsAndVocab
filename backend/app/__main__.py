"""CLI entry point for Quiz Patente B.

Usage:
    quizpatenteb              # start with defaults
    quizpatenteb --port 9000  # custom port
    quizpatenteb --no-browser # don't open browser
"""

from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Quiz Patente B study tool")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8500, help="Port to bind to (default: 8500)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser on startup")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print(f"\n  Quiz Patente B is starting at {url}")
    print(f"  Press Ctrl+C to stop.\n")

    if not args.no_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()

    uvicorn.run(
        "backend.app.main:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
