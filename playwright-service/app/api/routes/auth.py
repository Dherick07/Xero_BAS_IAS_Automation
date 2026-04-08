from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from app.db.connection import get_db
from app.services.browser_manager import BrowserManager
from app.services.xero_auth import XeroAuthService
from app.services.xero_session import XeroSessionService
from app.api.dependencies import verify_api_key
from app.models import SwitchTenantRequest

router = APIRouter()
logger = structlog.get_logger()


@router.post("/setup")
async def setup_auth(api_key: str = Depends(verify_api_key), db: AsyncSession = Depends(get_db)):
    """
    Start manual login flow.

    Opens the Xero login page in a new Chrome tab via the DevTools HTTP API.
    No CDP WebSocket connection is established, so Akamai cannot detect
    automation during the login flow.  After the user logs in manually,
    call POST /api/auth/complete to connect CDP and capture the session.
    """
    browser_manager = await BrowserManager.get_instance()
    auth_service = XeroAuthService(browser_manager)
    
    result = await auth_service.start_manual_login()
    return result


@router.post("/complete")
async def complete_auth(api_key: str = Depends(verify_api_key), db: AsyncSession = Depends(get_db)):
    """
    Complete authentication and save session.

    Connects CDP to the Chrome instance (deferred from /setup to avoid
    Akamai detection during login), verifies login, captures cookies,
    and saves them to the database.  The browser stays in headed mode
    so report automation runs in the same visible Chrome window.
    """
    browser_manager = await BrowserManager.get_instance()
    auth_service = XeroAuthService(browser_manager)
    session_service = XeroSessionService(db)

    # Complete the login — this also establishes the CDP connection
    result = await auth_service.complete_login()

    if not result.get("success"):
        return result

    # Save cookies to DB for persistence/recovery
    cookies = result.get("cookies", [])
    saved = await session_service.save_session(cookies)

    if not saved:
        return {
            "success": False,
            "error": "Failed to save session to database"
        }

    # NO headless restart — automation uses this same Chrome window
    return {
        "success": True,
        "message": "Login confirmed. Ready for report automation.",
        "current_tenant": result.get("current_tenant"),
    }


@router.get("/status")
async def auth_status(api_key: str = Depends(verify_api_key), db: AsyncSession = Depends(get_db)):
    """
    Check if current session is valid.
    
    Returns the current authentication status including:
    - Whether logged into Xero
    - Current tenant/organisation
    - Whether re-authentication is needed
    """
    browser_manager = await BrowserManager.get_instance()
    session_service = XeroSessionService(db)
    
    # First check stored session status
    session_status = await session_service.get_session_status()
    
    # If no session or expired, need reauth
    if not session_status.get("has_session") or not session_status.get("is_valid"):
        return {
            "logged_in": False,
            "current_tenant": None,
            "needs_reauth": True,
            "session_status": session_status,
            "message": "No valid session found. Please run /api/auth/setup"
        }
    
    # Check if browser has active session
    if browser_manager.is_initialized:
        auth_service = XeroAuthService(browser_manager)
        browser_status = await auth_service.check_auth_status()
        
        return {
            "logged_in": browser_status.get("logged_in", False),
            "current_tenant": browser_status.get("current_tenant"),
            "needs_reauth": browser_status.get("needs_reauth", True),
            "session_status": session_status
        }
    
    # Browser not initialized - session exists but not loaded
    return {
        "logged_in": False,
        "current_tenant": None,
        "needs_reauth": False,
        "session_status": session_status,
        "message": "Session exists but browser not initialized. Call /api/auth/restore to load session."
    }


@router.post("/restore")
async def restore_session(api_key: str = Depends(verify_api_key), db: AsyncSession = Depends(get_db)):
    """
    Restore session from stored cookies.
    
    Loads the stored session cookies into the browser and verifies login.
    """
    browser_manager = await BrowserManager.get_instance()
    session_service = XeroSessionService(db)
    auth_service = XeroAuthService(browser_manager)
    
    # Get stored session
    session_data = await session_service.get_session()
    
    if not session_data:
        return {
            "success": False,
            "error": "No stored session found. Please run /api/auth/setup first."
        }
    
    cookies = session_data.get("cookies", [])
    
    if not cookies:
        return {
            "success": False,
            "error": "Stored session has no cookies."
        }
    
    # Restore session
    result = await auth_service.restore_session(cookies)
    
    if result.get("success"):
        # Update session timestamp
        await session_service.save_session(cookies)
    
    return result


@router.get("/tenants")
async def list_tenants(api_key: str = Depends(verify_api_key), db: AsyncSession = Depends(get_db)):
    """
    List available Xero tenants/organisations.
    
    Returns a list of all organisations the logged-in user has access to.
    """
    browser_manager = await BrowserManager.get_instance()
    
    if not browser_manager.is_initialized:
        return {
            "success": False,
            "tenants": [],
            "error": "Browser not initialized. Please restore session first."
        }
    
    auth_service = XeroAuthService(browser_manager)
    result = await auth_service.get_available_tenants()
    
    return result


@router.post("/switch-tenant")
async def switch_tenant(request: SwitchTenantRequest, api_key: str = Depends(verify_api_key), db: AsyncSession = Depends(get_db)):
    """
    Switch to a specified Xero tenant/organisation.
    
    This endpoint:
    1. Checks if already on the target tenant (skips switch if so)
    2. Opens the organization menu
    3. Searches for the target tenant
    4. Clicks the matching tenant link
    5. Waits for the homepage to load with the new tenant
    
    Request body:
        tenant_name: Name of the Xero tenant/organisation to switch to
        
    Returns:
        success: Whether the operation succeeded
        current_tenant: The current tenant after the operation
        switched: Whether a switch was actually performed (false if already on target)
        previous_tenant: The tenant before switching (if switched)
    """
    logger.info(f"Tenant switch requested to: {request.tenant_name}")
    
    browser_manager = await BrowserManager.get_instance()
    
    if not browser_manager.is_initialized:
        # Try to restore session first
        session_service = XeroSessionService(db)
        session_data = await session_service.get_session()
        
        if not session_data:
            return {
                "success": False,
                "error": "No session found. Please run /api/auth/setup first."
            }
        
        auth_service = XeroAuthService(browser_manager)
        restore_result = await auth_service.restore_session(session_data.get("cookies", []))
        
        if not restore_result.get("success"):
            return {
                "success": False,
                "error": "Failed to restore session. Please re-authenticate."
            }
    
    # Use automation service to switch tenant
    from app.services.xero_automation import XeroAutomation
    
    automation = XeroAutomation(browser_manager)
    result = await automation.switch_tenant(request.tenant_name, request.tenant_shortcode)
    
    return result


@router.delete("/session")
async def delete_session(api_key: str = Depends(verify_api_key), db: AsyncSession = Depends(get_db)):
    """
    Delete the stored session.
    
    Clears the stored cookies from the database.
    """
    session_service = XeroSessionService(db)
    
    deleted = await session_service.delete_session()
    
    if deleted:
        return {
            "success": True,
            "message": "Session deleted"
        }
    else:
        return {
            "success": False,
            "error": "Failed to delete session"
        }


@router.post("/automated-login")
async def automated_login(api_key: str = Depends(verify_api_key), db: AsyncSession = Depends(get_db)):
    """
    Perform fully automated login to Xero.
    
    Uses configured credentials (XERO_EMAIL, XERO_PASSWORD) and security
    question answers (XERO_SECURITY_ANSWER_1/2/3) to log in without
    manual intervention.
    
    This endpoint:
    1. Navigates to Xero login page
    2. Enters email and password
    3. Selects security questions as MFA method
    4. Answers 1, 2, or 3 security questions dynamically
    5. Saves the session cookies to the database
    
    Returns:
        Dict with login status, current tenant, and session info
    """
    browser_manager = await BrowserManager.get_instance()
    auth_service = XeroAuthService(browser_manager)
    session_service = XeroSessionService(db)
    
    # Perform automated login
    result = await auth_service.automated_login()
    
    if not result.get("success"):
        return result
    
    # Save cookies to database
    cookies = result.get("cookies", [])
    saved = await session_service.save_session(cookies)
    
    if not saved:
        return {
            "success": False,
            "error": "Login succeeded but failed to save session to database"
        }
    
    # Switch to headless mode after successful login
    # Close the headed browser and reinitialize in headless mode with saved cookies
    logger.info("Switching browser to headless mode after successful login")
    try:
        await browser_manager.close()
        await browser_manager.initialize(headless=True)
        await browser_manager.set_cookies(cookies)
        # Navigate to Xero to activate the session
        await browser_manager.goto("https://go.xero.com", wait_until="networkidle")
        logger.info("Browser switched to headless mode successfully")
    except Exception as e:
        logger.warning(f"Failed to switch to headless mode: {e}. Browser will remain in headed mode.")
    
    return {
        "success": True,
        "message": "Automated login successful and session saved",
        "current_tenant": result.get("current_tenant"),
        "current_url": result.get("current_url")
    }


@router.post("/logout")
async def logout(api_key: str = Depends(verify_api_key), db: AsyncSession = Depends(get_db)):
    """
    Log out from Xero.
    
    Clicks the user menu and logs out from the current Xero session.
    Optionally clears the stored session from the database.
    
    Returns:
        Dict with logout status
    """
    browser_manager = await BrowserManager.get_instance()
    
    if not browser_manager.is_initialized:
        return {
            "success": True,
            "message": "Browser not initialized - no active session to logout from"
        }
    
    auth_service = XeroAuthService(browser_manager)
    
    # Perform logout
    result = await auth_service.logout()
    
    return result
