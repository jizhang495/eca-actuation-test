#!/usr/bin/env python
"""
Convenience script to run the backend server.

Usage:
    python run_backend.py
"""

import uvicorn
from pathlib import Path
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

if __name__ == "__main__":
    print("=" * 70)
    print("  ECA Testing Webapp - Backend Server")
    print("=" * 70)
    print()
    print("  Backend API:  http://localhost:8000")
    print("  API Docs:     http://localhost:8000/docs")
    print("  Health:       http://localhost:8000/health")
    print()
    print("  Press Ctrl+C to stop")
    print("=" * 70)
    print()

    # Restrict the reload watcher to the backend directory and
    # exclude virtualenv/node/build dirs to avoid noisy reloads on Windows.
    backend_dir = Path(__file__).parent
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,  # Enable auto-reload during development
        reload_dirs=[str(backend_dir)],
        reload_excludes=[
            ".venv/*",
            "data/*",
            "data/**/*",
            "frontend/*",
            "node_modules/*",
            "**/__pycache__/*",
            "**/*.pyc",
            "**/.git/*",
        ],
        log_level="info",
    )
