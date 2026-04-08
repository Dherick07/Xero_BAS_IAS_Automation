"""
Entry point for running the FastAPI application.

IMPORTANT FOR WINDOWS USERS:
Playwright requires ProactorEventLoop for subprocess support on Windows.
When uvicorn runs with --reload, it switches to SelectorEventLoop which is
incompatible with Playwright. Therefore, DO NOT use --reload on Windows.

Usage:
    python run.py              # Standard mode (recommended for Windows)
    python run.py --reload     # Development mode with reload (Linux/Mac only)
    
For Windows development, restart the server manually after code changes,
or use Docker which handles process isolation properly.
"""

import sys
import uvicorn

if __name__ == "__main__":
    # Check for --reload flag
    reload_mode = "--reload" in sys.argv
    
    # Warn Windows users about --reload incompatibility
    if sys.platform == "win32" and reload_mode:
        print("=" * 70)
        print("WARNING: Using --reload on Windows with Playwright may cause errors!")
        print("Playwright requires ProactorEventLoop, but uvicorn's reload mode")
        print("uses SelectorEventLoop which doesn't support subprocesses.")
        print("")
        print("If you encounter 'NotImplementedError', restart without --reload:")
        print("    python run.py")
        print("=" * 70)
        print("")
    
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=reload_mode,
        log_level="info",
    )
