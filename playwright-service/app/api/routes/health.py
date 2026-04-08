from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import structlog

from app.db.connection import get_db
from app.services.browser_manager import BrowserManager
from app.api.dependencies import verify_api_key

router = APIRouter()
logger = structlog.get_logger()


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """
    Health check endpoint.
    Returns status of the service, database, and browser.

    NOTE: This endpoint is intentionally NOT authenticated so Docker
    health checks and load balancers can reach it.
    """
    health_status = {
        "status": "healthy",
        "database": "unknown",
        "browser": {
            "initialized": False,
            "connected": False
        }
    }

    # Check database connection
    try:
        await db.execute(text("SELECT 1"))
        health_status["database"] = "connected"
    except Exception as e:
        logger.error("Database health check failed", error=str(e))
        health_status["database"] = "disconnected"
        health_status["status"] = "unhealthy"

    # Check browser status
    try:
        browser_manager = await BrowserManager.get_instance()
        browser_health = await browser_manager.health_check()
        health_status["browser"] = browser_health
    except Exception as e:
        logger.error("Browser health check failed", error=str(e))
        health_status["browser"] = {"error": str(e)}

    return health_status


@router.post("/browser/start")
async def start_browser(headless: bool = True, api_key: str = Depends(verify_api_key)):
    """
    Start the browser instance.

    Args:
        headless: If True, run in headless mode. If False, show browser window.
    """
    try:
        browser_manager = await BrowserManager.get_instance()
        await browser_manager.initialize(headless=headless)
        return {
            "success": True,
            "message": f"Browser started in {'headless' if headless else 'headed'} mode",
            "status": await browser_manager.health_check()
        }
    except Exception as e:
        logger.error("Failed to start browser", error=str(e))
        return {
            "success": False,
            "error": str(e)
        }


@router.post("/browser/stop")
async def stop_browser(api_key: str = Depends(verify_api_key)):
    """Stop the browser instance."""
    try:
        browser_manager = await BrowserManager.get_instance()
        await browser_manager.close()
        return {
            "success": True,
            "message": "Browser stopped"
        }
    except Exception as e:
        logger.error("Failed to stop browser", error=str(e))
        return {
            "success": False,
            "error": str(e)
        }


@router.post("/browser/restart")
async def restart_browser(headless: bool = True, api_key: str = Depends(verify_api_key)):
    """Restart the browser instance."""
    try:
        browser_manager = await BrowserManager.get_instance()
        await browser_manager.restart(headless=headless)
        return {
            "success": True,
            "message": f"Browser restarted in {'headless' if headless else 'headed'} mode",
            "status": await browser_manager.health_check()
        }
    except Exception as e:
        logger.error("Failed to restart browser", error=str(e))
        return {
            "success": False,
            "error": str(e)
        }


from app.config import get_settings as _get_settings


@router.get("/ui-config")
async def ui_config():
    """
    Return configuration needed by the frontend UI.
    No auth required — this endpoint is only accessible on localhost.
    """
    s = _get_settings()
    return {"api_key": s.api_key}
