"""
Xero Automation Service - Browser automation for Xero operations.

Handles:
- Tenant/organisation switching
- Report navigation and download
- Activity Statement download
- Payroll Activity Summary download
"""

from typing import Optional, Tuple
from datetime import datetime
import calendar
import structlog
import asyncio
import os
import re

from patchright.async_api import Page, TimeoutError as PlaywrightTimeout

from app.services.browser_manager import BrowserManager
from app.services.file_manager import get_file_manager
from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


def get_month_date_range(month: int, year: int) -> Tuple[str, str]:
    """
    Get the start and end date for a given month/year.
    
    Uses calendar.monthrange to correctly handle months with 28, 29, 30, or 31 days.
    
    Args:
        month: Month number (1-12)
        year: Year (e.g., 2025)
        
    Returns:
        Tuple of (start_date, end_date) in "dd MMMM yyyy" format
        e.g., ("1 October 2025", "31 October 2025")
    """
    # Get the number of days in the month (handles leap years for February)
    _, last_day = calendar.monthrange(year, month)
    
    # Get month name
    month_name = calendar.month_name[month]  # e.g., "October"
    
    start_date = f"1 {month_name} {year}"
    end_date = f"{last_day} {month_name} {year}"
    
    return start_date, end_date


def parse_period_to_month_year(period: str) -> Tuple[int, int]:
    """
    Parse a period string like "October 2025" to (month, year).
    
    Args:
        period: Period string in format "Month Year" (e.g., "October 2025")
        
    Returns:
        Tuple of (month, year) as integers
    """
    # Map month names to numbers
    month_map = {name.lower(): num for num, name in enumerate(calendar.month_name) if num}
    
    parts = period.strip().split()
    if len(parts) >= 2:
        month_name = parts[0].lower()
        year = int(parts[-1])
        month = month_map.get(month_name, 1)
        return month, year
    
    # Default to current month if parsing fails
    now = datetime.now()
    return now.month, now.year


# Xero URL patterns
XERO_URLS = {
    "dashboard": "https://go.xero.com/Dashboard",
    "reports": "https://go.xero.com/Reports",
    "activity_statement": "https://go.xero.com/Reports/Report.aspx?reportId=ActivityStatement",
    "payroll_reports": "https://go.xero.com/Reports/PayrollReports",
}

# Tenant-specific URL templates (use .format(shortcode=...) or f-string)
# These navigate directly to the correct tenant without UI clicks
TENANT_URL_TEMPLATES = {
    "activity_statement": "https://go.xero.com/app/!{shortcode}/bas/overview",
    "payroll_activity_summary": "https://reporting.xero.com/!{shortcode}/v1/Run/2035",
    "balance_sheet": "https://reporting.xero.com/!{shortcode}/v1/Run/1017",
    "profit_loss": "https://reporting.xero.com/!{shortcode}/v1/Run/1016",
    "aged_payables": "https://reporting.xero.com/!{shortcode}/v1/Run/1003",
    "aged_receivables": "https://reporting.xero.com/!{shortcode}/v1/Run/1002",
    "homepage": "https://go.xero.com/app/!{shortcode}/homepage",
    "dashboard": "https://go.xero.com/app/!{shortcode}/dashboard",
}

# Selectors with fallbacks - these may need adjustment based on actual Xero UI
SELECTORS = {
    # Organisation/Tenant switcher
    "org_switcher": [
        '[data-testid="org-switcher"]',
        '[data-automationid="org-switcher"]',
        'button[aria-label*="organisation"]',
        'button[aria-label*="organization"]',
        '[class*="org-switcher"]',
        '[class*="organisation-switcher"]',
        'header button:has-text("Switch")',
    ],
    
    # Organisation list items
    "org_list_item": [
        '[data-testid="org-item"]',
        '[class*="org-list"] li',
        '[role="menuitem"]',
        '[class*="organisation-list"] button',
    ],
    
    # Navigation
    "nav_reports": [
        'button:has-text("Reporting")',  # From codegen: get_by_role("button", name="Reporting")
        'text=Reporting',
        'a:has-text("Reporting")',
        'text=Reports',
        'a:has-text("Reports")',
        '[data-testid="nav-reports"]',
    ],
    
    "nav_accounting": [
        'text=Accounting',
        'a:has-text("Accounting")',
        '[data-testid="nav-accounting"]',
    ],
    
    # Search
    "search_input": [
        'input[placeholder*="Search"]',
        'input[type="search"]',
        '[data-testid="search-input"]',
        'input[aria-label*="Search"]',
    ],
    
    # Report specific
    "report_search": [
        'input[placeholder*="Find a report"]',
        'input[placeholder*="Search reports"]',
        '[data-testid="report-search"]',
    ],
    
    # Date range controls
    "date_range_dropdown": [
        'text=Date range',
        'button:has-text("Date range")',
        '[data-testid="date-range"]',
        'select[name*="date"]',
    ],
    
    "last_month_option": [
        'text=Last month',
        '[data-value="last-month"]',
        'option:has-text("Last month")',
    ],
    
    # Buttons
    "update_button": [
        'button:has-text("Update")',
        'button[type="submit"]:has-text("Update")',
        '[data-testid="update-button"]',
    ],
    
    "export_button": [
        'button:has-text("Export")',  # From codegen
        '[data-testid="export-button"]',
        'button[aria-label*="Export"]',
    ],
    
    "excel_radio": [
        'input[type="radio"]:has-text("Excel")',  # From codegen: radio button
        '[role="radio"][name*="Excel"]',
        'label:has-text("Excel")',
    ],
    
    "more_button": [
        'button:has-text("More")',
        '[data-testid="more-button"]',
        'button[aria-label*="More"]',
    ],
    
    "excel_option": [
        'text=Excel',
        'button:has-text("Excel")',
        '[data-testid="export-excel"]',
        'a:has-text("Excel")',
    ],
    
    # Activity Statement specific
    "activity_statement_link": [
        'a:has-text("Activity Statement"):not(:has-text("Activity Statement Summary"))',  # From codegen: exact match
        'text=Activity Statement',
        'a:has-text("Activity Statement")',
        '[data-testid="activity-statement"]',
    ],
    
    "create_new_statement": [
        'button:has-text("Create new statement")',  # From codegen: exact match
        'button:has-text("Create statement")',
        'text=Create new statement',
        '[data-testid="create-statement"]',
    ],
    
    "period_button": [
        # From codegen: periods are buttons, not dropdown options
        # Format: "October 2025 PAYG W" or similar
        'button:has-text("PAYG W")',
        'button[role="button"]',
    ],
    
    "draft_statement": [
        'text=Draft',
        'text=Unfiled',
        'text=New',
        '[class*="draft"]',
        '[class*="unfiled"]',
    ],
    
    # Payroll specific
    "payroll_activity_summary": [
        'text=Payroll Activity Summary',
        'a:has-text("Payroll Activity Summary")',
        '[data-testid="payroll-activity-summary"]',
    ],
}


class XeroAutomation:
    """
    Automates Xero browser interactions.
    
    Provides methods for:
    - Switching between tenants/organisations
    - Downloading Activity Statements
    - Downloading Payroll Activity Summaries
    """
    
    def __init__(self, browser_manager: BrowserManager, debug_screenshots: bool = None):
        self.browser = browser_manager
        self.file_manager = get_file_manager()
        # Use config setting if not explicitly provided
        if debug_screenshots is None:
            self._debug_screenshots = settings.debug_screenshots
        else:
            self._debug_screenshots = debug_screenshots
    
    @property
    def page(self) -> Page:
        """Get the current browser page."""
        return self.browser.page
    
    async def _find_element(self, selector_key: str, timeout: int = 10000) -> Optional[any]:
        """
        Find an element using multiple fallback selectors.
        
        Args:
            selector_key: Key in SELECTORS dict
            timeout: Timeout in milliseconds
            
        Returns:
            Element if found, None otherwise
        """
        selectors = SELECTORS.get(selector_key, [])
        
        for selector in selectors:
            try:
                element = await self.page.wait_for_selector(
                    selector, 
                    timeout=timeout // len(selectors),
                    state="visible"
                )
                if element:
                    logger.debug(f"Found element with selector: {selector}")
                    return element
            except PlaywrightTimeout:
                continue
            except Exception as e:
                logger.debug(f"Selector {selector} failed: {e}")
                continue
        
        logger.warning(f"Could not find element: {selector_key}")
        return None
    
    async def _click_element(self, selector_key: str, timeout: int = 10000) -> bool:
        """
        Click an element using fallback selectors.
        
        Returns:
            True if clicked successfully
        """
        element = await self._find_element(selector_key, timeout)
        if element:
            await element.click()
            await asyncio.sleep(0.5)  # Brief pause after click
            return True
        return False
    
    async def _take_debug_screenshot(self, name: str) -> Optional[str]:
        """Take a screenshot if debug mode is enabled."""
        if self._debug_screenshots:
            return await self.browser.take_screenshot(name)
        return None
    
    def _get_current_shortcode(self) -> Optional[str]:
        """
        Extract the tenant shortcode from the current browser URL.
        
        Xero URLs contain the shortcode in these formats:
            https://go.xero.com/app/!{shortcode}/...
            https://reporting.xero.com/!{shortcode}/v1/...
        
        Returns:
            The shortcode string (without '!') or None if not found
        """
        current_url = self.page.url
        # Pattern 1: go.xero.com/app/!{shortcode}/...
        match = re.search(r'/app/!([^/]+)/', current_url)
        if match:
            return match.group(1)
        # Pattern 2: go.xero.com/app/!{shortcode} (no trailing slash)
        match = re.search(r'/app/!([^/]+)$', current_url)
        if match:
            return match.group(1)
        # Pattern 3: reporting.xero.com/!{shortcode}/... (direct report URLs)
        match = re.search(r'reporting\.xero\.com/!([^/]+)/', current_url)
        if match:
            return match.group(1)
        return None
    
    async def _verify_tenant_shortcode(self, expected_shortcode: str) -> dict:
        """
        Verify the current browser URL contains the expected tenant shortcode.
        
        This is the most reliable way to confirm we're on the correct tenant,
        since the URL shortcode is unique per tenant and always present.
        
        Args:
            expected_shortcode: The expected tenant shortcode (e.g., "mkK34")
            
        Returns:
            Dict with 'valid' bool and details
        """
        current_shortcode = self._get_current_shortcode()
        current_url = self.page.url
        
        if current_shortcode is None:
            logger.warning(
                "Could not extract shortcode from URL",
                url=current_url,
                expected=expected_shortcode
            )
            return {
                "valid": False,
                "current_shortcode": None,
                "expected_shortcode": expected_shortcode,
                "url": current_url,
                "reason": "Could not extract shortcode from current URL"
            }
        
        if current_shortcode == expected_shortcode:
            logger.info(
                "Tenant shortcode verified",
                shortcode=current_shortcode,
                url=current_url
            )
            return {
                "valid": True,
                "current_shortcode": current_shortcode,
                "expected_shortcode": expected_shortcode,
                "url": current_url
            }
        
        logger.error(
            "Tenant shortcode MISMATCH - wrong tenant!",
            current_shortcode=current_shortcode,
            expected_shortcode=expected_shortcode,
            url=current_url
        )
        return {
            "valid": False,
            "current_shortcode": current_shortcode,
            "expected_shortcode": expected_shortcode,
            "url": current_url,
            "reason": f"Shortcode mismatch: expected '{expected_shortcode}' but got '{current_shortcode}'"
        }
    
    async def switch_tenant(self, target_tenant: str, tenant_shortcode: str = None) -> dict:
        """
        Switch to a specified Xero tenant/organisation.
        
        Primary method: URL-based switching using tenant shortcode
        - Navigate directly to https://go.xero.com/app/!{shortcode}/homepage
        - Much more reliable than UI clicking
        
        Fallback method: UI-based switching (if shortcode not provided)
        - Click "Toggle Organization menu" button
        - Search for target tenant in searchbox
        - Click the matching tenant link
        
        Args:
            target_tenant: Name of the organisation to switch to
            tenant_shortcode: Optional shortcode for URL-based switching (e.g., "mkK34" for Marsill)
            
        Returns:
            Dict with success status, current tenant, and whether switch was needed
        """
        try:
            logger.info(f"Tenant switch requested to: {target_tenant}", shortcode=tenant_shortcode)
            
            # Take initial screenshot
            await self._take_debug_screenshot("switch_tenant_start")
            
            # First, check current tenant
            current_tenant = await self._get_current_tenant_name()
            logger.info(f"Current tenant: {current_tenant}")
            
            # Check if already on the target tenant using URL shortcode (most reliable)
            if tenant_shortcode:
                current_shortcode = self._get_current_shortcode()
                if current_shortcode == tenant_shortcode:
                    logger.info(f"Already on target tenant (shortcode match): {current_tenant}")
                    return {
                        "success": True,
                        "current_tenant": current_tenant,
                        "switched": False,
                        "message": "Already on the requested tenant (shortcode verified)"
                    }
            elif current_tenant:
                # Fallback: name-based comparison when no shortcode provided
                current_normalized = current_tenant.lower().strip()
                target_normalized = target_tenant.lower().strip()
                
                if current_normalized == target_normalized or target_normalized in current_normalized:
                    logger.info(f"Already on target tenant: {current_tenant}")
                    return {
                        "success": True,
                        "current_tenant": current_tenant,
                        "switched": False,
                        "message": "Already on the requested tenant"
                    }
            
            # PRIMARY METHOD: URL-based switching using tenant shortcode
            if tenant_shortcode:
                logger.info(f"Using URL-based tenant switching with shortcode: {tenant_shortcode}")
                tenant_url = f"https://go.xero.com/app/!{tenant_shortcode}/homepage"
                
                try:
                    await self.page.goto(tenant_url, wait_until="domcontentloaded", timeout=60000)
                    try:
                        await self.page.wait_for_load_state("networkidle", timeout=20000)
                    except Exception:
                        logger.debug("networkidle timeout after URL navigation, continuing...")
                    await asyncio.sleep(2)
                    
                    await self._take_debug_screenshot("switch_tenant_url_complete")
                    
                    # Verify switch using URL shortcode (most reliable check)
                    verification = await self._verify_tenant_shortcode(tenant_shortcode)
                    
                    if verification["valid"]:
                        new_tenant = await self._get_current_tenant_name()
                        logger.info(f"Successfully switched to tenant via URL: {new_tenant} (shortcode verified: {tenant_shortcode})")
                        return {
                            "success": True,
                            "current_tenant": new_tenant,
                            "switched": True,
                            "previous_tenant": current_tenant,
                            "method": "url",
                            "shortcode_verified": True
                        }
                    else:
                        # Shortcode mismatch after URL navigation — this is a real failure
                        logger.error(
                            "Tenant switch via URL failed - shortcode mismatch after navigation",
                            expected=tenant_shortcode,
                            actual=verification.get("current_shortcode"),
                            url=verification.get("url")
                        )
                        screenshot = await self.browser.take_screenshot("switch_tenant_shortcode_mismatch")
                        return {
                            "success": False,
                            "error": f"Tenant switch failed: {verification.get('reason')}",
                            "expected_shortcode": tenant_shortcode,
                            "actual_shortcode": verification.get("current_shortcode"),
                            "screenshot": screenshot
                        }
                        
                except Exception as e:
                    logger.warning(f"URL-based switching failed: {e}, falling back to UI method")
                    # Fall through to UI-based method
            
            # FALLBACK METHOD: UI-based switching
            logger.info("Using UI-based tenant switching (fallback)")
            
            # Navigate to homepage first if not there
            current_url = self.page.url
            if "xero.com" not in current_url or "/homepage" not in current_url:
                logger.info("Navigating to Xero dashboard for tenant switch")
                await self.page.goto(XERO_URLS["dashboard"], wait_until="domcontentloaded", timeout=60000)
                try:
                    await self.page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    pass
                await asyncio.sleep(2)
            
            await self._take_debug_screenshot("switch_tenant_before_menu")
            
            # Step 1: Click "Toggle Organization menu" button
            # From codegen: page1.get_by_role("button", name="Toggle Organization menu").click()
            try:
                toggle_btn = self.page.get_by_role("button", name="Toggle Organization menu")
                await toggle_btn.wait_for(state="visible", timeout=10000)
                await toggle_btn.click()
                logger.info("Clicked Toggle Organization menu button")
            except Exception as e:
                logger.warning(f"Toggle Organization menu button not found: {e}")
                # Fallback: Try clicking on current tenant name
                try:
                    if current_tenant:
                        await self.page.get_by_text(current_tenant).nth(1).click(timeout=5000)
                        logger.info("Clicked current tenant name as fallback")
                except Exception:
                    screenshot = await self.browser.take_screenshot("org_menu_not_found")
                    return {
                        "success": False,
                        "error": "Could not open organization menu",
                        "screenshot": screenshot
                    }
            
            await asyncio.sleep(1)
            await self._take_debug_screenshot("switch_tenant_menu_opened")
            
            # Step 2: Click on search box and search for tenant
            # From codegen: page1.get_by_role("searchbox", name="Search organizations").click()
            try:
                search_box = self.page.get_by_role("searchbox", name="Search organizations")
                await search_box.wait_for(state="visible", timeout=10000)
                await search_box.click()
                await search_box.fill(target_tenant)
                logger.info(f"Filled search box with: {target_tenant}")
            except Exception as e:
                logger.error(f"Could not find or fill search box: {e}")
                await self.page.keyboard.press("Escape")
                screenshot = await self.browser.take_screenshot("search_box_not_found")
                return {
                    "success": False,
                    "error": "Could not find organization search box",
                    "screenshot": screenshot
                }
            
            await asyncio.sleep(1.5)  # Wait for search results
            await self._take_debug_screenshot("switch_tenant_search_results")
            
            # Step 3: Click the matching tenant link
            # From codegen: page1.get_by_role("link", name="MPL Marsill Pty Ltd").click()
            # The link name includes a short code prefix, so we search by partial match
            tenant_found = False
            
            try:
                # Try to find link containing the tenant name
                tenant_link = self.page.get_by_role("link", name=target_tenant)
                await tenant_link.wait_for(state="visible", timeout=5000)
                await tenant_link.click()
                tenant_found = True
                logger.info(f"Clicked tenant link: {target_tenant}")
            except Exception:
                # Try partial match - search for links containing the tenant name
                try:
                    # Look for any link that contains the tenant name
                    links = self.page.locator(f'a:has-text("{target_tenant}")')
                    count = await links.count()
                    if count > 0:
                        await links.first.click()
                        tenant_found = True
                        logger.info(f"Clicked tenant link using partial match")
                except Exception as e:
                    logger.warning(f"Partial match failed: {e}")
            
            if not tenant_found:
                # Close the menu and report failure
                await self.page.keyboard.press("Escape")
                screenshot = await self.browser.take_screenshot("tenant_not_found")
                return {
                    "success": False,
                    "error": f"Could not find tenant: {target_tenant}",
                    "screenshot": screenshot
                }
            
            # Step 4: Wait for page to load with new tenant
            try:
                await self.page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                logger.debug("networkidle timeout after tenant switch, continuing...")
            await asyncio.sleep(3)
            
            await self._take_debug_screenshot("switch_tenant_complete")
            
            # Verify switch was successful using URL shortcode (most reliable)
            new_tenant = await self._get_current_tenant_name()
            logger.info(f"Tenant after switch: {new_tenant}")
            
            if tenant_shortcode:
                verification = await self._verify_tenant_shortcode(tenant_shortcode)
                if verification["valid"]:
                    logger.info(f"Successfully switched to tenant: {new_tenant} (shortcode verified)")
                    return {
                        "success": True,
                        "current_tenant": new_tenant,
                        "switched": True,
                        "previous_tenant": current_tenant,
                        "shortcode_verified": True
                    }
                else:
                    logger.error(f"UI tenant switch failed - shortcode mismatch: {verification.get('reason')}")
                    screenshot = await self.browser.take_screenshot("switch_tenant_ui_mismatch")
                    return {
                        "success": False,
                        "error": f"Tenant switch failed: {verification.get('reason')}",
                        "expected_shortcode": tenant_shortcode,
                        "actual_shortcode": verification.get("current_shortcode"),
                        "screenshot": screenshot
                    }
            
            # No shortcode provided — fall back to name-based verification
            if new_tenant:
                new_normalized = new_tenant.lower().strip()
                target_normalized = target_tenant.lower().strip()
                
                if new_normalized == target_normalized or target_normalized in new_normalized:
                    logger.info(f"Successfully switched to tenant: {new_tenant}")
                    return {
                        "success": True,
                        "current_tenant": new_tenant,
                        "switched": True,
                        "previous_tenant": current_tenant
                    }
            
            # Name doesn't match and no shortcode to verify — fail
            screenshot = await self.browser.take_screenshot("switch_tenant_name_mismatch")
            return {
                "success": False,
                "error": f"Tenant switch verification failed: expected '{target_tenant}' but got '{new_tenant}'",
                "current_tenant": new_tenant,
                "screenshot": screenshot
            }
                
        except Exception as e:
            logger.error(f"Error switching tenant: {e}")
            screenshot = await self.browser.take_screenshot("switch_tenant_error")
            return {
                "success": False,
                "error": str(e),
                "screenshot": screenshot
            }
    
    async def _get_current_tenant_name(self) -> Optional[str]:
        """
        Get the name of the currently selected tenant.
        
        Uses multiple strategies:
        1. Page title (e.g., "Homepage – Marsill Pty Ltd – Xero")
        2. URL shortcode pattern
        3. Org switcher element (fallback)
        """
        try:
            # Strategy 1: Extract from page title
            # Title format: "Page Name – Tenant Name – Xero"
            # Note: Xero uses en-dash (–) not hyphen (-)
            title = await self.page.title()
            logger.info(f"Page title: {title}")
            
            if title and "Xero" in title:
                # Try splitting by en-dash first, then regular dash
                for separator in [" – ", " - ", "–", "-"]:
                    if separator in title:
                        parts = title.split(separator)
                        logger.debug(f"Title parts with '{separator}': {parts}")
                        if len(parts) >= 3:
                            tenant_name = parts[-2].strip()  # Second to last part
                            if tenant_name and tenant_name != "Xero":
                                logger.info(f"Got tenant from title: {tenant_name}")
                                return tenant_name
                        elif len(parts) == 2:
                            # Format might be "Tenant Name – Xero"
                            tenant_name = parts[0].strip()
                            if tenant_name and tenant_name != "Xero":
                                logger.info(f"Got tenant from title (2 parts): {tenant_name}")
                                return tenant_name
                        break  # Only try the first matching separator
            
            # Strategy 2: Try org switcher element (with short timeout)
            try:
                element = await self._find_element("org_switcher", timeout=3000)
                if element:
                    text = await element.text_content()
                    if text:
                        logger.info(f"Got tenant from org_switcher: {text.strip()}")
                        return text.strip()
            except Exception:
                pass  # Ignore org_switcher failures
                    
        except Exception as e:
            logger.warning(f"Error getting current tenant name: {e}")
        
        return None
    
    async def download_payroll_activity_summary(
        self,
        tenant_name: str,
        month: Optional[int] = None,
        year: Optional[int] = None,
        tenant_shortcode: str = None
    ) -> dict:
        """
        Download the Payroll Activity Summary report.
        
        Workflow (from codegen):
        1. Click "Reporting" on navbar
        2. Click "All reports" in dropdown
        3. Scroll down and click "Payroll Activity Summary"
        4. Enter start date and end date in date range fields
        5. Click "Update" button
        6. Click "Export" button
        7. Select "Excel" format
        
        Args:
            tenant_name: Name of the tenant (for file naming)
            month: Month (1-12), defaults to last month
            year: Year, defaults to current/last year based on month
            tenant_shortcode: Tenant shortcode for URL-based navigation (preserves tenant context)
            
        Returns:
            Dict with success status and file path
        """
        try:
            # Determine the date range
            if month is None or year is None:
                # Default to last month
                now = datetime.now()
                if now.month == 1:
                    month = 12
                    year = now.year - 1
                else:
                    month = now.month - 1
                    year = now.year
            
            start_date, end_date = get_month_date_range(month, year)
            period_display = f"{calendar.month_name[month]} {year}"
            
            logger.info(
                f"Downloading Payroll Activity Summary for {tenant_name}",
                period=period_display,
                start_date=start_date,
                end_date=end_date
            )
            
            # Navigate to Payroll Activity Summary report
            # Primary: Use direct URL (reporting.xero.com/!{shortcode}/v1/Run/2035)
            # Fallback: UI navigation (Reporting > All reports > Payroll Activity Summary)
            navigated = False
            
            if tenant_shortcode:
                # Primary approach: Direct URL navigation
                payroll_url = TENANT_URL_TEMPLATES["payroll_activity_summary"].format(shortcode=tenant_shortcode)
                logger.info(f"Navigating directly to Payroll Activity Summary: {payroll_url}")
                try:
                    await self.page.goto(payroll_url, wait_until="domcontentloaded", timeout=60000)
                    logger.info(f"Navigated to Payroll Activity Summary, URL: {self.page.url}")
                    
                    try:
                        await self.page.wait_for_load_state("networkidle", timeout=20000)
                    except Exception:
                        logger.debug("networkidle timeout, continuing anyway...")
                    await asyncio.sleep(3)
                    navigated = True
                except Exception as e:
                    logger.warning(f"Direct URL navigation failed: {e}, falling back to UI navigation")
            
            # Fallback: UI-based navigation
            if not navigated:
                current_url = self.page.url
                logger.info(f"Using UI navigation fallback. Current URL: {current_url}")
                
                if tenant_shortcode:
                    tenant_url = TENANT_URL_TEMPLATES["homepage"].format(shortcode=tenant_shortcode)
                    await self.page.goto(tenant_url, wait_until="domcontentloaded", timeout=60000)
                elif "xero.com" not in current_url:
                    await self.page.goto(XERO_URLS["dashboard"], wait_until="domcontentloaded", timeout=60000)
                
                try:
                    await self.page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    logger.debug("networkidle timeout, continuing anyway...")
                await asyncio.sleep(3)
                
                # Step 1: Click "Reporting" on navbar
                reporting_clicked = await self._click_reporting_nav()
                if not reporting_clicked:
                    screenshot = await self.browser.take_screenshot("payroll_reporting_nav_not_found")
                    return {
                        "success": False,
                        "error": "Could not find Reporting navigation link",
                        "screenshot": screenshot
                    }
                
                await asyncio.sleep(2)
                
                # Step 2: Click "All reports" in dropdown
                all_reports_clicked = await self._click_all_reports_link()
                if not all_reports_clicked:
                    screenshot = await self.browser.take_screenshot("payroll_all_reports_not_found")
                    return {
                        "success": False,
                        "error": "Could not find 'All reports' link",
                        "screenshot": screenshot
                    }
                
                try:
                    await self.page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    logger.debug("networkidle timeout after All reports click, continuing...")
                await asyncio.sleep(2)
                
                # Step 3: Scroll down and click "Payroll Activity Summary"
                payroll_clicked = await self._click_payroll_activity_summary_link()
                if not payroll_clicked:
                    screenshot = await self.browser.take_screenshot("payroll_report_not_found")
                    return {
                        "success": False,
                        "error": "Could not find Payroll Activity Summary report",
                        "screenshot": screenshot
                    }
                
                try:
                    await self.page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    logger.debug("networkidle timeout after Payroll Activity Summary click, continuing...")
                await asyncio.sleep(2)
            
            # Verify we're on the correct tenant before proceeding
            if tenant_shortcode:
                verification = await self._verify_tenant_shortcode(tenant_shortcode)
                if not verification["valid"]:
                    logger.error(
                        "Wrong tenant detected before Payroll Activity Summary download",
                        expected=tenant_shortcode,
                        actual=verification.get("current_shortcode")
                    )
                    screenshot = await self.browser.take_screenshot("payroll_wrong_tenant")
                    return {
                        "success": False,
                        "error": f"Wrong tenant: {verification.get('reason')}. Aborting download to prevent data mix-up.",
                        "expected_shortcode": tenant_shortcode,
                        "actual_shortcode": verification.get("current_shortcode"),
                        "screenshot": screenshot
                    }
            
            await self._take_debug_screenshot("payroll_report_page")
            
            # Step 4: Enter date range
            date_entered = await self._enter_payroll_date_range(start_date, end_date)
            if not date_entered:
                logger.warning("Could not enter date range, proceeding with default dates")
            
            await self._take_debug_screenshot("payroll_dates_entered")
            
            # Step 5: Click Update button
            update_clicked = await self._click_update_button()
            if update_clicked:
                try:
                    await self.page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    logger.debug("networkidle timeout after Update click, continuing...")
                await asyncio.sleep(2)
            else:
                logger.warning("Could not click Update button, report may show default data")
            
            await self._take_debug_screenshot("payroll_report_updated")
            
            # Step 6 & 7: Export to Excel
            file_path = await self._export_payroll_to_excel(
                report_type="payroll_activity_summary",
                tenant_name=tenant_name,
                period=period_display
            )
            
            if file_path:
                return {
                    "success": True,
                    "file_path": file_path,
                    "file_name": os.path.basename(file_path),
                    "tenant_name": tenant_name,
                    "report_type": "payroll_activity_summary",
                    "period": period_display,
                    "start_date": start_date,
                    "end_date": end_date
                }
            else:
                screenshot = await self.browser.take_screenshot("payroll_export_failed")
                return {
                    "success": False,
                    "error": "Failed to export report",
                    "screenshot": screenshot
                }
                
        except Exception as e:
            logger.error(f"Error downloading payroll summary: {e}")
            screenshot = await self.browser.take_screenshot("payroll_error")
            return {
                "success": False,
                "error": str(e),
                "screenshot": screenshot
            }
    
    async def download_activity_statement(
        self,
        tenant_name: str,
        find_unfiled: bool = True,
        period: str = None,
        tenant_shortcode: str = None,
        month: Optional[int] = None,
        year: Optional[int] = None
    ) -> dict:
        """
        Download the Activity Statement (BAS Report).
        
        Primary approach: Navigate directly to the statement via URL with date params:
            /bas/statement?startDate=YYYY-MM-01&endDate=YYYY-MM-DD
        Fallback: Navigate to /bas/overview and click the period link.
        
        Args:
            tenant_name: Name of the tenant (for file naming)
            find_unfiled: If True, look for draft/unfiled statements
            period: Period display name (e.g., "February 2026")
            tenant_shortcode: Tenant shortcode for direct URL navigation
            month: Month (1-12) for date-based URL navigation
            year: Year for date-based URL navigation
            
        Returns:
            Dict with success status and file path
        """
        try:
            logger.info(f"Downloading Activity Statement for {tenant_name}, period: {period}, month: {month}, year: {year}, shortcode: {tenant_shortcode}")
            
            # Determine month/year from period string if not provided
            if (month is None or year is None) and period:
                try:
                    parts = period.split()
                    if len(parts) == 2:
                        month_name = parts[0]
                        year = int(parts[1])
                        # Convert month name to number
                        month_names = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
                        month = month_names.get(month_name.lower())
                except (ValueError, KeyError):
                    logger.warning(f"Could not parse month/year from period: {period}")
            
            # Calculate start and end dates for the month
            start_date_str = None
            end_date_str = None
            if month and year:
                start_date_str = f"{year}-{month:02d}-01"
                last_day = calendar.monthrange(year, month)[1]
                end_date_str = f"{year}-{month:02d}-{last_day:02d}"
            
            navigated_to_statement = False
            
            # PRIMARY: Navigate directly to the statement via URL with date params
            # URL format: /bas/statement?startDate=2026-02-01&endDate=2026-02-28
            if tenant_shortcode and start_date_str and end_date_str:
                statement_url = f"https://go.xero.com/app/!{tenant_shortcode}/bas/statement?startDate={start_date_str}&endDate={end_date_str}"
                logger.info(f"Navigating directly to Activity Statement: {statement_url}")
                try:
                    await self.page.goto(statement_url, wait_until="domcontentloaded", timeout=60000)
                    logger.info(f"Navigated to statement URL: {self.page.url}")
                    
                    try:
                        await self.page.wait_for_load_state("networkidle", timeout=20000)
                    except Exception:
                        logger.debug("networkidle timeout, continuing anyway...")
                    await asyncio.sleep(3)
                    
                    # Check if we landed on the "Lodge activity statements" setup page
                    page_content = await self.page.content()
                    if "Lodge activity statements with Xero" in page_content or "Lodge reports" in page_content:
                        logger.warning(f"Activity Statements not configured for tenant: {tenant_name}")
                        screenshot = await self.browser.take_screenshot("activity_not_configured")
                        return {
                            "success": False,
                            "error": f"Activity Statements not configured for {tenant_name}. The tenant needs to set up Activity Statements in Xero first.",
                            "screenshot": screenshot
                        }
                    
                    # Verify we're on the correct tenant
                    if tenant_shortcode:
                        verification = await self._verify_tenant_shortcode(tenant_shortcode)
                        if not verification["valid"]:
                            logger.error(
                                "Wrong tenant detected after direct URL navigation",
                                expected=tenant_shortcode,
                                actual=verification.get("current_shortcode")
                            )
                            screenshot = await self.browser.take_screenshot("activity_wrong_tenant")
                            return {
                                "success": False,
                                "error": f"Wrong tenant: {verification.get('reason')}. Aborting download.",
                                "expected_shortcode": tenant_shortcode,
                                "actual_shortcode": verification.get("current_shortcode"),
                                "screenshot": screenshot
                            }
                    
                    await self._take_debug_screenshot("activity_statement_direct_url")
                    navigated_to_statement = True
                    logger.info("Successfully navigated to statement via direct URL")
                    
                except Exception as e:
                    logger.warning(f"Direct URL navigation to statement failed: {e}, falling back to overview page")
            
            # FALLBACK: Navigate to overview page and click the period
            if not navigated_to_statement:
                logger.info("Using fallback: navigating to BAS overview and clicking period")
                
                if tenant_shortcode:
                    activity_url = TENANT_URL_TEMPLATES["activity_statement"].format(shortcode=tenant_shortcode)
                    logger.info(f"Navigating to Activity Statements overview: {activity_url}")
                    await self.page.goto(activity_url, wait_until="domcontentloaded", timeout=60000)
                else:
                    await self.page.goto(XERO_URLS["activity_statement"], wait_until="domcontentloaded", timeout=60000)
                
                try:
                    await self.page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    logger.debug("networkidle timeout, continuing anyway...")
                await asyncio.sleep(3)
                
                # Verify tenant
                if tenant_shortcode:
                    verification = await self._verify_tenant_shortcode(tenant_shortcode)
                    if not verification["valid"]:
                        logger.error("Wrong tenant on overview page", expected=tenant_shortcode)
                        screenshot = await self.browser.take_screenshot("activity_wrong_tenant")
                        return {
                            "success": False,
                            "error": f"Wrong tenant: {verification.get('reason')}. Aborting download.",
                            "expected_shortcode": tenant_shortcode,
                            "actual_shortcode": verification.get("current_shortcode"),
                            "screenshot": screenshot
                        }
                
                # Check for "Lodge activity statements" setup page
                page_content = await self.page.content()
                if "Lodge activity statements with Xero" in page_content or "Lodge reports" in page_content:
                    logger.warning(f"Activity Statements not configured for tenant: {tenant_name}")
                    screenshot = await self.browser.take_screenshot("activity_not_configured")
                    return {
                        "success": False,
                        "error": f"Activity Statements not configured for {tenant_name}.",
                        "screenshot": screenshot
                    }
                
                await self._take_debug_screenshot("activity_statements_overview")
                
                # Click the matching period on the overview page
                period_clicked = False
                period_variants = []
                if period:
                    period_variants.append(period)
                    parts = period.split()
                    if len(parts) == 2:
                        abbrev = f"{parts[0][:3]} {parts[1]}"
                        if abbrev != period:
                            period_variants.append(abbrev)
                
                for variant in period_variants:
                    if period_clicked:
                        break
                    
                    # Try Prepare button, Review link, period link, text element, JS click
                    for strategy_name, strategy_fn in [
                        ("Prepare button", self._try_click_prepare(variant)),
                        ("Review link", self._try_click_review(variant)),
                        ("Period link", self._try_click_period_link(variant)),
                        ("Text element", self._try_click_text(variant)),
                        ("JavaScript", self._try_click_js(variant)),
                    ]:
                        if period_clicked:
                            break
                        try:
                            result = await strategy_fn
                            if result:
                                period_clicked = True
                                logger.info(f"Clicked via {strategy_name}: {variant}")
                        except Exception as e:
                            logger.debug(f"{strategy_name} failed for {variant}: {e}")
                
                if not period_clicked:
                    logger.warning(f"Could not find period on overview: {period}")
                    screenshot = await self.browser.take_screenshot("activity_period_not_found")
                    return {
                        "success": False,
                        "error": f"Could not find Activity Statement for period: {period}",
                        "screenshot": screenshot
                    }
                
                try:
                    await self.page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    logger.debug("networkidle timeout after period click, continuing...")
                await asyncio.sleep(3)
                await self._take_debug_screenshot("activity_statement_loaded")
            
            # Export to Excel
            file_path = await self._export_to_excel(
                report_type="activity_statement",
                tenant_name=tenant_name
            )
            
            if file_path:
                return {
                    "success": True,
                    "file_path": file_path,
                    "file_name": os.path.basename(file_path),
                    "tenant_name": tenant_name,
                    "report_type": "activity_statement",
                    "period": period
                }
            else:
                screenshot = await self.browser.take_screenshot("activity_export_failed")
                return {
                    "success": False,
                    "error": "Failed to export report",
                    "screenshot": screenshot
                }
                
        except Exception as e:
            logger.error(f"Error downloading activity statement: {e}")
            screenshot = await self.browser.take_screenshot("activity_error")
            return {
                "success": False,
                "error": str(e),
                "screenshot": screenshot
            }
    
    async def _try_click_prepare(self, variant: str) -> bool:
        """Try clicking Prepare button near the period text."""
        row = self.page.locator(f'text="{variant}"').locator('xpath=ancestor::*[contains(@class,"row") or self::tr or self::li or self::div[.//button]]').first
        prepare_btn = row.get_by_role("link", name="Prepare")
        await prepare_btn.wait_for(state="visible", timeout=5000)
        await prepare_btn.click(timeout=5000)
        return True
    
    async def _try_click_review(self, variant: str) -> bool:
        """Try clicking Review link near the period text."""
        row = self.page.locator(f'text="{variant}"').locator('xpath=ancestor::*[contains(@class,"row") or self::tr or self::li or self::div[.//a]]').first
        review_link = row.get_by_role("link", name="Review")
        await review_link.wait_for(state="visible", timeout=5000)
        await review_link.click(timeout=5000)
        return True
    
    async def _try_click_period_link(self, variant: str) -> bool:
        """Try clicking the period text as a link."""
        link = self.page.get_by_role("link", name=variant)
        await link.wait_for(state="visible", timeout=5000)
        await link.click(timeout=5000)
        return True
    
    async def _try_click_text(self, variant: str) -> bool:
        """Try clicking any element containing the period text."""
        element = self.page.get_by_text(variant, exact=False).first
        await element.wait_for(state="visible", timeout=5000)
        await element.click(timeout=5000)
        return True
    
    async def _try_click_js(self, variant: str) -> bool:
        """Try clicking via JavaScript."""
        clicked = await self.page.evaluate('''
            (periodText) => {
                const allElements = document.querySelectorAll('a, button');
                for (const el of allElements) {
                    const parent = el.closest('div, tr, li');
                    if (parent && parent.textContent.includes(periodText)) {
                        const text = el.textContent.trim();
                        if (text === 'Prepare' || text === 'Review') {
                            el.click();
                            return true;
                        }
                    }
                }
                for (const el of allElements) {
                    if (el.textContent && el.textContent.includes(periodText)) {
                        el.click();
                        return true;
                    }
                }
                return false;
            }
        ''', variant)
        return clicked
    
    async def _click_reporting_nav(self) -> bool:
        """
        Click the Reporting button in the navigation bar.
        Uses multiple strategies to handle Xero's dynamic SPA.
        
        Returns:
            True if clicked successfully
        """
        logger.info("Attempting to click Reporting navigation button")
        
        # Strategy 1: Use get_by_role with exact match (from codegen)
        try:
            reporting_btn = self.page.get_by_role("button", name="Reporting")
            # Wait for the button to be visible
            await reporting_btn.wait_for(state="visible", timeout=15000)
            # Scroll into view if needed
            await reporting_btn.scroll_into_view_if_needed()
            await asyncio.sleep(0.5)  # Brief pause after scroll
            await reporting_btn.click(timeout=5000)
            logger.info("Clicked Reporting button using get_by_role")
            return True
        except Exception as e:
            logger.debug(f"Strategy 1 (get_by_role button) failed: {e}")
        
        # Strategy 2: Try as a link instead of button
        try:
            reporting_link = self.page.get_by_role("link", name="Reporting")
            await reporting_link.wait_for(state="visible", timeout=10000)
            await reporting_link.scroll_into_view_if_needed()
            await reporting_link.click(timeout=5000)
            logger.info("Clicked Reporting link using get_by_role")
            return True
        except Exception as e:
            logger.debug(f"Strategy 2 (get_by_role link) failed: {e}")
        
        # Strategy 3: Use locator with text content
        try:
            reporting_locator = self.page.locator('button:has-text("Reporting"), [role="button"]:has-text("Reporting")')
            await reporting_locator.first.wait_for(state="visible", timeout=10000)
            await reporting_locator.first.scroll_into_view_if_needed()
            await reporting_locator.first.click(timeout=5000)
            logger.info("Clicked Reporting using locator with has-text")
            return True
        except Exception as e:
            logger.debug(f"Strategy 3 (locator has-text) failed: {e}")
        
        # Strategy 4: Try clicking by text with force option
        try:
            await self.page.locator('text=Reporting').first.click(force=True, timeout=10000)
            logger.info("Clicked Reporting using text locator with force")
            return True
        except Exception as e:
            logger.debug(f"Strategy 4 (text with force) failed: {e}")
        
        # Strategy 5: Find in navigation area specifically
        try:
            nav_area = self.page.locator('nav, header, [role="navigation"]')
            reporting_in_nav = nav_area.get_by_text("Reporting", exact=True)
            await reporting_in_nav.first.click(timeout=10000)
            logger.info("Clicked Reporting in navigation area")
            return True
        except Exception as e:
            logger.debug(f"Strategy 5 (nav area) failed: {e}")
        
        # Strategy 6: Use JavaScript click as last resort
        try:
            clicked = await self.page.evaluate('''
                () => {
                    const elements = document.querySelectorAll('button, a, [role="button"], [role="menuitem"]');
                    for (const el of elements) {
                        if (el.textContent && el.textContent.trim().includes('Reporting')) {
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }
            ''')
            if clicked:
                logger.info("Clicked Reporting using JavaScript evaluation")
                return True
        except Exception as e:
            logger.debug(f"Strategy 6 (JS click) failed: {e}")
        
        logger.error("All strategies to click Reporting button failed")
        return False
    
    async def _click_activity_statement_link(self) -> bool:
        """
        Click the Activity Statement link in the Reporting dropdown.
        
        Returns:
            True if clicked successfully
        """
        logger.info("Attempting to click Activity Statement link")
        
        # Strategy 1: Use get_by_role with exact match (from codegen)
        try:
            link = self.page.get_by_role("link", name="Activity Statement", exact=True)
            await link.wait_for(state="visible", timeout=10000)
            await link.click(timeout=5000)
            logger.info("Clicked Activity Statement using get_by_role exact")
            return True
        except Exception as e:
            logger.debug(f"Strategy 1 failed: {e}")
        
        # Strategy 2: Use menuitem role (common in dropdowns)
        try:
            menuitem = self.page.get_by_role("menuitem", name="Activity Statement")
            await menuitem.wait_for(state="visible", timeout=5000)
            await menuitem.click(timeout=5000)
            logger.info("Clicked Activity Statement using menuitem role")
            return True
        except Exception as e:
            logger.debug(f"Strategy 2 failed: {e}")
        
        # Strategy 3: Locator with exact text to avoid matching "Activity Statement Summary"
        try:
            # Use filter to exclude links that contain "Summary"
            links = self.page.locator('a:has-text("Activity Statement")').filter(
                has_not_text="Summary"
            )
            await links.first.wait_for(state="visible", timeout=5000)
            await links.first.click(timeout=5000)
            logger.info("Clicked Activity Statement using filtered locator")
            return True
        except Exception as e:
            logger.debug(f"Strategy 3 failed: {e}")
        
        # Strategy 4: Text locator with force
        try:
            await self.page.get_by_text("Activity Statement", exact=True).first.click(
                force=True, timeout=5000
            )
            logger.info("Clicked Activity Statement using text with force")
            return True
        except Exception as e:
            logger.debug(f"Strategy 4 failed: {e}")
        
        # Strategy 5: JavaScript click
        try:
            clicked = await self.page.evaluate('''
                () => {
                    const links = document.querySelectorAll('a, [role="menuitem"], [role="link"]');
                    for (const link of links) {
                        const text = link.textContent?.trim();
                        if (text === 'Activity Statement' || 
                            (text?.includes('Activity Statement') && !text?.includes('Summary'))) {
                            link.click();
                            return true;
                        }
                    }
                    return false;
                }
            ''')
            if clicked:
                logger.info("Clicked Activity Statement using JavaScript")
                return True
        except Exception as e:
            logger.debug(f"Strategy 5 failed: {e}")
        
        logger.error("All strategies to click Activity Statement link failed")
        return False
    
    async def _click_create_new_statement(self) -> bool:
        """
        Click the 'Create new statement' button.
        
        Returns:
            True if clicked successfully
        """
        logger.info("Attempting to click 'Create new statement' button")
        
        # Strategy 1: Use get_by_role (from codegen)
        try:
            btn = self.page.get_by_role("button", name="Create new statement")
            await btn.wait_for(state="visible", timeout=10000)
            await btn.scroll_into_view_if_needed()
            await btn.click(timeout=5000)
            logger.info("Clicked 'Create new statement' using get_by_role")
            return True
        except Exception as e:
            logger.debug(f"Strategy 1 failed: {e}")
        
        # Strategy 2: Locator with has-text
        try:
            btn = self.page.locator('button:has-text("Create new statement")')
            await btn.first.wait_for(state="visible", timeout=5000)
            await btn.first.click(timeout=5000)
            logger.info("Clicked 'Create new statement' using locator")
            return True
        except Exception as e:
            logger.debug(f"Strategy 2 failed: {e}")
        
        # Strategy 3: Text with force click
        try:
            await self.page.get_by_text("Create new statement").click(force=True, timeout=5000)
            logger.info("Clicked 'Create new statement' using text with force")
            return True
        except Exception as e:
            logger.debug(f"Strategy 3 failed: {e}")
        
        logger.warning("Could not find 'Create new statement' button")
        return False
    
    async def _click_period_button(self, period: str) -> bool:
        """
        Click the period button (e.g., "October 2025 PAYG W" or "December 2025 GST, PAYG W, PAYG I").
        
        The period text varies - some have just "PAYG W", others have "GST, PAYG W, PAYG I".
        The constant is that all contain "PAYG" and the month/year.
        
        Args:
            period: The period text to match (e.g., "October 2025", "December 2025")
            
        Returns:
            True if clicked successfully
        """
        logger.info(f"Attempting to click period button for: {period}")
        
        # Strategy 1: Use get_by_role with partial name match
        try:
            # The button text might be "October 2025 PAYG W" or "December 2025 GST, PAYG W, PAYG I"
            btn = self.page.get_by_role("button", name=period)
            await btn.wait_for(state="visible", timeout=10000)
            await btn.scroll_into_view_if_needed()
            await btn.click(timeout=5000)
            logger.info(f"Clicked period button using get_by_role: {period}")
            return True
        except Exception as e:
            logger.debug(f"Strategy 1 failed: {e}")
        
        # Strategy 2: Locator with has-text for partial match
        try:
            btn = self.page.locator(f'button:has-text("{period}")')
            await btn.first.wait_for(state="visible", timeout=5000)
            await btn.first.scroll_into_view_if_needed()
            await btn.first.click(timeout=5000)
            logger.info(f"Clicked period button using locator: {period}")
            return True
        except Exception as e:
            logger.debug(f"Strategy 2 failed: {e}")
        
        # Strategy 3: Look for any clickable element with the period text
        try:
            element = self.page.locator(f'[role="button"]:has-text("{period}"), button:has-text("{period}"), a:has-text("{period}")')
            await element.first.wait_for(state="visible", timeout=5000)
            await element.first.click(timeout=5000)
            logger.info(f"Clicked period element: {period}")
            return True
        except Exception as e:
            logger.debug(f"Strategy 3 failed: {e}")
        
        # Strategy 4: JavaScript click - search for element containing both period AND "PAYG"
        try:
            clicked = await self.page.evaluate('''
                (periodText) => {
                    const elements = document.querySelectorAll('button, [role="button"], a, [role="option"], li');
                    for (const el of elements) {
                        const text = el.textContent;
                        // Match elements that contain the period (e.g., "December 2025") AND "PAYG"
                        if (text && text.includes(periodText) && text.includes('PAYG')) {
                            el.click();
                            return true;
                        }
                    }
                    // Fallback: just match the period text
                    for (const el of elements) {
                        if (el.textContent && el.textContent.includes(periodText)) {
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }
            ''', period)
            if clicked:
                logger.info(f"Clicked period button using JavaScript: {period}")
                return True
        except Exception as e:
            logger.debug(f"Strategy 4 failed: {e}")
        
        # Strategy 5: Try clicking on list items or options in dropdown
        try:
            # The dropdown might use li elements or role="option"
            options = self.page.locator(f'li:has-text("{period}"), [role="option"]:has-text("{period}")')
            count = await options.count()
            if count > 0:
                await options.first.click(timeout=5000)
                logger.info(f"Clicked period option in dropdown: {period}")
                return True
        except Exception as e:
            logger.debug(f"Strategy 5 failed: {e}")
        
        logger.warning(f"Could not find period button for: {period}")
        return False
    
    async def _export_to_excel(self, report_type: str, tenant_name: str) -> Optional[str]:
        """
        Export the current report to Excel.
        
        Workflow from codegen:
        1. Click "Export" button
        2. Check "Excel" radio button
        3. Click "Export" button again (the second one in the modal)
        
        Returns:
            Path to downloaded file, or None if failed
        """
        try:
            await self._take_debug_screenshot("export_start")
            
            # Step 1: Click first Export button to open export modal
            export_clicked = await self._click_export_button()
            
            if not export_clicked:
                logger.error("Could not find Export button")
                return None
            
            await asyncio.sleep(1.5)  # Wait for modal to open
            await self._take_debug_screenshot("export_modal_opened")
            
            # Step 2: Check Excel radio button
            await self._select_excel_format()
            
            await asyncio.sleep(0.5)
            await self._take_debug_screenshot("excel_selected")
            
            # Step 3: Click the BOTTOM Export button (not the top one which may be obscured by popups)
            # The page has an "Adjust G field values" popup that can overlap with the top Export button
            # Using the bottom Export button avoids this conflict
            await self._take_debug_screenshot("before_final_export")
            
            # First, scroll to the bottom of the page to ensure the bottom Export button is visible
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(0.5)
            
            async def click_final_export():
                # Click the LAST (bottom) Export button to avoid popup conflicts
                # Strategy 1: Use get_by_role and click the LAST Export button
                try:
                    export_buttons = self.page.get_by_role("button", name="Export")
                    count = await export_buttons.count()
                    logger.info(f"Found {count} Export buttons - clicking the LAST one (bottom)")
                    if count > 0:
                        # Click the last (bottom) Export button
                        await export_buttons.last.click(timeout=5000)
                        logger.info("Clicked LAST (bottom) Export button using get_by_role")
                        return
                except Exception as e:
                    logger.debug(f"Strategy 1 for final export failed: {e}")
                
                # Strategy 2: Use locator and click the last button
                try:
                    buttons = self.page.locator('button:has-text("Export")')
                    count = await buttons.count()
                    logger.info(f"Found {count} buttons with Export text - clicking the LAST one")
                    if count > 0:
                        await buttons.last.click(timeout=5000)
                        logger.info("Clicked LAST Export button using locator")
                        return
                except Exception as e:
                    logger.debug(f"Strategy 2 for final export failed: {e}")
                
                # Strategy 3: JavaScript click on the LAST Export button (bottom of page)
                try:
                    clicked = await self.page.evaluate('''
                        () => {
                            const buttons = Array.from(document.querySelectorAll('button'));
                            // Filter to only Export buttons
                            const exportButtons = buttons.filter(btn => 
                                btn.textContent && btn.textContent.trim() === 'Export'
                            );
                            if (exportButtons.length > 0) {
                                // Click the LAST one (bottom of page)
                                const lastBtn = exportButtons[exportButtons.length - 1];
                                lastBtn.scrollIntoView();
                                lastBtn.click();
                                return true;
                            }
                            return false;
                        }
                    ''')
                    if clicked:
                        logger.info("Clicked LAST Export button using JavaScript")
                        return
                except Exception as e:
                    logger.debug(f"Strategy 3 for final export failed: {e}")
                
                # Strategy 4: Click with force on the last button
                try:
                    await self.page.get_by_role("button", name="Export").last.click(force=True, timeout=5000)
                    logger.info("Clicked last Export button with force")
                    return
                except Exception as e:
                    logger.debug(f"Strategy 4 for final export failed: {e}")
                    raise e
            
            try:
                file_path = await self.browser.wait_for_download(
                    click_final_export,
                    timeout=60000
                )
            except Exception as e:
                logger.error(f"Download failed: {e}")
                return None
            
            # Rename file with proper naming convention
            new_filename = self.file_manager.generate_filename(
                report_type=report_type,
                tenant_name=tenant_name
            )
            
            final_path = self.file_manager.rename_download(file_path, new_filename)
            
            # Validate the file
            if self.file_manager.validate_excel_file(final_path):
                logger.info(f"Successfully downloaded: {new_filename}")
                return final_path
            else:
                logger.warning(f"Downloaded file may be invalid: {new_filename}")
                return final_path  # Return anyway, let caller decide
                
        except Exception as e:
            logger.error(f"Error exporting to Excel: {e}")
            return None
    
    async def _click_export_button(self) -> bool:
        """
        Click the Export button to open the export modal.
        
        Returns:
            True if clicked successfully
        """
        logger.info("Attempting to click Export button")
        
        # Strategy 1: Use get_by_role
        try:
            btn = self.page.get_by_role("button", name="Export")
            await btn.first.wait_for(state="visible", timeout=10000)
            await btn.first.scroll_into_view_if_needed()
            await btn.first.click(timeout=5000)
            logger.info("Clicked Export button using get_by_role")
            return True
        except Exception as e:
            logger.debug(f"Strategy 1 failed: {e}")
        
        # Strategy 2: Locator with has-text
        try:
            btn = self.page.locator('button:has-text("Export")')
            await btn.first.wait_for(state="visible", timeout=5000)
            await btn.first.click(timeout=5000)
            logger.info("Clicked Export button using locator")
            return True
        except Exception as e:
            logger.debug(f"Strategy 2 failed: {e}")
        
        # Strategy 3: Use fallback selectors
        if await self._click_element("export_button", timeout=5000):
            logger.info("Clicked Export button using fallback selectors")
            return True
        
        logger.error("Could not find Export button")
        return False
    
    async def _select_excel_format(self) -> bool:
        """
        Select Excel format in the export modal.
        
        Returns:
            True if selected successfully
        """
        logger.info("Attempting to select Excel format")
        
        # Strategy 1: Use get_by_role for radio button (from codegen)
        try:
            radio = self.page.get_by_role("radio", name="Excel")
            await radio.wait_for(state="visible", timeout=5000)
            await radio.check(timeout=5000)
            logger.info("Selected Excel using get_by_role radio")
            return True
        except Exception as e:
            logger.debug(f"Strategy 1 failed: {e}")
        
        # Strategy 2: Click the label
        try:
            label = self.page.get_by_label("Excel")
            await label.click(timeout=5000)
            logger.info("Selected Excel by clicking label")
            return True
        except Exception as e:
            logger.debug(f"Strategy 2 failed: {e}")
        
        # Strategy 3: Click text "Excel" near a radio button
        try:
            await self.page.locator('label:has-text("Excel")').click(timeout=5000)
            logger.info("Selected Excel using label locator")
            return True
        except Exception as e:
            logger.debug(f"Strategy 3 failed: {e}")
        
        # Strategy 4: Click any element with Excel text in the modal
        try:
            modal = self.page.locator('[role="dialog"], .modal, [class*="modal"], [class*="export"]')
            excel_option = modal.get_by_text("Excel")
            await excel_option.click(timeout=5000)
            logger.info("Selected Excel in modal")
            return True
        except Exception as e:
            logger.debug(f"Strategy 4 failed: {e}")
        
        # Strategy 5: JavaScript to find and click Excel radio
        try:
            clicked = await self.page.evaluate('''
                () => {
                    // Try to find radio button with Excel label
                    const labels = document.querySelectorAll('label');
                    for (const label of labels) {
                        if (label.textContent?.includes('Excel')) {
                            label.click();
                            return true;
                        }
                    }
                    // Try to find radio input with Excel value
                    const radios = document.querySelectorAll('input[type="radio"]');
                    for (const radio of radios) {
                        if (radio.value?.toLowerCase().includes('excel') || 
                            radio.id?.toLowerCase().includes('excel')) {
                            radio.click();
                            return true;
                        }
                    }
                    return false;
                }
            ''')
            if clicked:
                logger.info("Selected Excel using JavaScript")
                return True
        except Exception as e:
            logger.debug(f"Strategy 5 failed: {e}")
        
        logger.warning("Could not find Excel option, it might already be selected")
        return False
    
    async def _click_all_reports_link(self) -> bool:
        """
        Click the 'All reports' link in the Reporting dropdown.
        
        Returns:
            True if clicked successfully
        """
        logger.info("Attempting to click 'All reports' link")
        
        # Strategy 1: Use get_by_role with exact match (from codegen)
        try:
            link = self.page.get_by_role("link", name="All reports")
            await link.wait_for(state="visible", timeout=10000)
            await link.click(timeout=5000)
            logger.info("Clicked 'All reports' using get_by_role")
            return True
        except Exception as e:
            logger.debug(f"Strategy 1 failed: {e}")
        
        # Strategy 2: Use menuitem role
        try:
            menuitem = self.page.get_by_role("menuitem", name="All reports")
            await menuitem.wait_for(state="visible", timeout=5000)
            await menuitem.click(timeout=5000)
            logger.info("Clicked 'All reports' using menuitem role")
            return True
        except Exception as e:
            logger.debug(f"Strategy 2 failed: {e}")
        
        # Strategy 3: Locator with has-text
        try:
            link = self.page.locator('a:has-text("All reports")')
            await link.first.wait_for(state="visible", timeout=5000)
            await link.first.click(timeout=5000)
            logger.info("Clicked 'All reports' using locator")
            return True
        except Exception as e:
            logger.debug(f"Strategy 3 failed: {e}")
        
        # Strategy 4: Text with force click
        try:
            await self.page.get_by_text("All reports", exact=True).click(force=True, timeout=5000)
            logger.info("Clicked 'All reports' using text with force")
            return True
        except Exception as e:
            logger.debug(f"Strategy 4 failed: {e}")
        
        # Strategy 5: JavaScript click
        try:
            clicked = await self.page.evaluate('''
                () => {
                    const elements = document.querySelectorAll('a, [role="menuitem"], [role="link"]');
                    for (const el of elements) {
                        if (el.textContent?.trim() === 'All reports') {
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }
            ''')
            if clicked:
                logger.info("Clicked 'All reports' using JavaScript")
                return True
        except Exception as e:
            logger.debug(f"Strategy 5 failed: {e}")
        
        logger.error("All strategies to click 'All reports' link failed")
        return False
    
    async def _click_payroll_activity_summary_link(self) -> bool:
        """
        Click the 'Payroll Activity Summary' link on the All Reports page.
        May need to scroll down to find it.
        
        Returns:
            True if clicked successfully
        """
        logger.info("Attempting to click 'Payroll Activity Summary' link")
        
        # First, scroll down the page to ensure the link is visible
        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        await asyncio.sleep(1)
        
        # Strategy 1: Use get_by_role with exact match (from codegen)
        try:
            link = self.page.get_by_role("link", name="Payroll Activity Summary")
            await link.wait_for(state="visible", timeout=10000)
            await link.scroll_into_view_if_needed()
            await link.click(timeout=5000)
            logger.info("Clicked 'Payroll Activity Summary' using get_by_role")
            return True
        except Exception as e:
            logger.debug(f"Strategy 1 failed: {e}")
        
        # Strategy 2: Locator with has-text
        try:
            link = self.page.locator('a:has-text("Payroll Activity Summary")')
            await link.first.wait_for(state="visible", timeout=5000)
            await link.first.scroll_into_view_if_needed()
            await link.first.click(timeout=5000)
            logger.info("Clicked 'Payroll Activity Summary' using locator")
            return True
        except Exception as e:
            logger.debug(f"Strategy 2 failed: {e}")
        
        # Strategy 3: Text locator with scroll
        try:
            element = self.page.get_by_text("Payroll Activity Summary", exact=True)
            await element.scroll_into_view_if_needed()
            await element.click(timeout=5000)
            logger.info("Clicked 'Payroll Activity Summary' using text locator")
            return True
        except Exception as e:
            logger.debug(f"Strategy 3 failed: {e}")
        
        # Strategy 4: JavaScript click with scroll
        try:
            clicked = await self.page.evaluate('''
                () => {
                    const links = document.querySelectorAll('a');
                    for (const link of links) {
                        if (link.textContent?.includes('Payroll Activity Summary')) {
                            link.scrollIntoView({ behavior: 'smooth', block: 'center' });
                            link.click();
                            return true;
                        }
                    }
                    return false;
                }
            ''')
            if clicked:
                logger.info("Clicked 'Payroll Activity Summary' using JavaScript")
                return True
        except Exception as e:
            logger.debug(f"Strategy 4 failed: {e}")
        
        logger.error("All strategies to click 'Payroll Activity Summary' link failed")
        return False
    
    async def _enter_payroll_date_range(self, start_date: str, end_date: str) -> bool:
        """
        Enter the date range for the Payroll Activity Summary report.
        
        From codegen:
        - Click on the date range textbox (name="Date range: This month Select")
        - Fill with start date (e.g., "1 October 2025")
        - Click on end date textbox (name="Select end date")
        - Fill with end date (e.g., "31 October 2025")
        
        Args:
            start_date: Start date in "d MMMM yyyy" format (e.g., "1 October 2025")
            end_date: End date in "d MMMM yyyy" format (e.g., "31 October 2025")
            
        Returns:
            True if dates were entered successfully
        """
        logger.info(f"Entering date range: {start_date} to {end_date}")
        
        start_entered = False
        end_entered = False
        
        # Enter start date
        # Strategy 1: Use get_by_role with textbox (from codegen)
        try:
            # The textbox has a complex name, try partial match
            start_input = self.page.get_by_role("textbox", name="Date range")
            await start_input.wait_for(state="visible", timeout=10000)
            await start_input.click(timeout=5000)
            await start_input.fill(start_date)
            logger.info(f"Entered start date using get_by_role: {start_date}")
            start_entered = True
        except Exception as e:
            logger.debug(f"Strategy 1 for start date failed: {e}")
        
        if not start_entered:
            # Strategy 2: Try locator with placeholder or aria-label
            try:
                start_input = self.page.locator('input[placeholder*="date"], input[aria-label*="start"], input[aria-label*="Date range"]').first
                await start_input.wait_for(state="visible", timeout=5000)
                await start_input.click(timeout=5000)
                await start_input.fill(start_date)
                logger.info(f"Entered start date using locator: {start_date}")
                start_entered = True
            except Exception as e:
                logger.debug(f"Strategy 2 for start date failed: {e}")
        
        if not start_entered:
            # Strategy 3: JavaScript to find and fill start date input
            try:
                filled = await self.page.evaluate('''
                    (startDate) => {
                        const inputs = document.querySelectorAll('input[type="text"], input:not([type])');
                        for (const input of inputs) {
                            const label = input.getAttribute('aria-label') || input.placeholder || '';
                            if (label.toLowerCase().includes('date range') || label.toLowerCase().includes('start')) {
                                input.focus();
                                input.value = startDate;
                                input.dispatchEvent(new Event('input', { bubbles: true }));
                                input.dispatchEvent(new Event('change', { bubbles: true }));
                                return true;
                            }
                        }
                        return false;
                    }
                ''', start_date)
                if filled:
                    logger.info(f"Entered start date using JavaScript: {start_date}")
                    start_entered = True
            except Exception as e:
                logger.debug(f"Strategy 3 for start date failed: {e}")
        
        await asyncio.sleep(0.5)
        
        # Enter end date
        # Strategy 1: Use get_by_role with textbox (from codegen)
        try:
            end_input = self.page.get_by_role("textbox", name="Select end date")
            await end_input.wait_for(state="visible", timeout=5000)
            await end_input.click(timeout=5000)
            await end_input.fill(end_date)
            logger.info(f"Entered end date using get_by_role: {end_date}")
            end_entered = True
        except Exception as e:
            logger.debug(f"Strategy 1 for end date failed: {e}")
        
        if not end_entered:
            # Strategy 2: Try locator with placeholder or aria-label
            try:
                end_input = self.page.locator('input[placeholder*="end"], input[aria-label*="end date"]').first
                await end_input.wait_for(state="visible", timeout=5000)
                await end_input.click(timeout=5000)
                await end_input.fill(end_date)
                logger.info(f"Entered end date using locator: {end_date}")
                end_entered = True
            except Exception as e:
                logger.debug(f"Strategy 2 for end date failed: {e}")
        
        if not end_entered:
            # Strategy 3: JavaScript to find and fill end date input
            try:
                filled = await self.page.evaluate('''
                    (endDate) => {
                        const inputs = document.querySelectorAll('input[type="text"], input:not([type])');
                        for (const input of inputs) {
                            const label = input.getAttribute('aria-label') || input.placeholder || '';
                            if (label.toLowerCase().includes('end date') || label.toLowerCase().includes('end')) {
                                input.focus();
                                input.value = endDate;
                                input.dispatchEvent(new Event('input', { bubbles: true }));
                                input.dispatchEvent(new Event('change', { bubbles: true }));
                                return true;
                            }
                        }
                        return false;
                    }
                ''', end_date)
                if filled:
                    logger.info(f"Entered end date using JavaScript: {end_date}")
                    end_entered = True
            except Exception as e:
                logger.debug(f"Strategy 3 for end date failed: {e}")
        
        # Press Escape to close any date picker that might be open
        await self.page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
        
        if start_entered and end_entered:
            logger.info("Successfully entered both start and end dates")
            return True
        elif start_entered or end_entered:
            logger.warning(f"Only partially entered dates: start={start_entered}, end={end_entered}")
            return True  # Partial success
        else:
            logger.error("Failed to enter date range")
            return False
    
    async def _click_update_button(self) -> bool:
        """
        Click the Update button to refresh the report with new date range.
        
        Returns:
            True if clicked successfully
        """
        logger.info("Attempting to click Update button")
        
        # Strategy 1: Use get_by_role (from codegen)
        try:
            btn = self.page.get_by_role("button", name="Update")
            await btn.wait_for(state="visible", timeout=10000)
            await btn.click(timeout=5000)
            logger.info("Clicked Update button using get_by_role")
            return True
        except Exception as e:
            logger.debug(f"Strategy 1 failed: {e}")
        
        # Strategy 2: Locator with has-text
        try:
            btn = self.page.locator('button:has-text("Update")')
            await btn.first.wait_for(state="visible", timeout=5000)
            await btn.first.click(timeout=5000)
            logger.info("Clicked Update button using locator")
            return True
        except Exception as e:
            logger.debug(f"Strategy 2 failed: {e}")
        
        # Strategy 3: Use fallback selectors
        if await self._click_element("update_button", timeout=5000):
            logger.info("Clicked Update button using fallback selectors")
            return True
        
        # Strategy 4: JavaScript click
        try:
            clicked = await self.page.evaluate('''
                () => {
                    const buttons = document.querySelectorAll('button');
                    for (const btn of buttons) {
                        if (btn.textContent?.trim() === 'Update') {
                            btn.click();
                            return true;
                        }
                    }
                    return false;
                }
            ''')
            if clicked:
                logger.info("Clicked Update button using JavaScript")
                return True
        except Exception as e:
            logger.debug(f"Strategy 4 failed: {e}")
        
        logger.error("Could not find Update button")
        return False
    
    async def _export_payroll_to_excel(
        self,
        report_type: str,
        tenant_name: str,
        period: str
    ) -> Optional[str]:
        """
        Export the Payroll Activity Summary report to Excel.
        
        Workflow from codegen:
        1. Click "Export" button at the bottom
        2. Click "Excel" button in the export dropdown
        
        Args:
            report_type: Type of report for filename
            tenant_name: Tenant name for filename
            period: Period string for filename (e.g., "October 2025")
            
        Returns:
            Path to downloaded file, or None if failed
        """
        try:
            await self._take_debug_screenshot("payroll_export_start")
            
            # Step 1: Click Export button (at the bottom of the report)
            export_clicked = await self._click_export_button()
            
            if not export_clicked:
                logger.error("Could not find Export button for payroll report")
                return None
            
            await asyncio.sleep(1)
            await self._take_debug_screenshot("payroll_export_dropdown_opened")
            
            # Step 2: Click Excel button in the dropdown
            # From codegen: page.get_by_role("button", name="Excel").click()
            async def click_excel_export():
                # Strategy 1: Use get_by_role for Excel button
                try:
                    excel_btn = self.page.get_by_role("button", name="Excel")
                    await excel_btn.wait_for(state="visible", timeout=5000)
                    await excel_btn.click(timeout=5000)
                    logger.info("Clicked Excel button using get_by_role")
                    return
                except Exception as e:
                    logger.debug(f"Strategy 1 for Excel button failed: {e}")
                
                # Strategy 2: Locator with has-text
                try:
                    excel_btn = self.page.locator('button:has-text("Excel")')
                    await excel_btn.first.wait_for(state="visible", timeout=5000)
                    await excel_btn.first.click(timeout=5000)
                    logger.info("Clicked Excel button using locator")
                    return
                except Exception as e:
                    logger.debug(f"Strategy 2 for Excel button failed: {e}")
                
                # Strategy 3: Click text "Excel"
                try:
                    await self.page.get_by_text("Excel", exact=True).click(timeout=5000)
                    logger.info("Clicked Excel using text")
                    return
                except Exception as e:
                    logger.debug(f"Strategy 3 for Excel button failed: {e}")
                
                # Strategy 4: JavaScript click
                try:
                    clicked = await self.page.evaluate('''
                        () => {
                            const elements = document.querySelectorAll('button, a, [role="menuitem"]');
                            for (const el of elements) {
                                if (el.textContent?.trim() === 'Excel') {
                                    el.click();
                                    return true;
                                }
                            }
                            return false;
                        }
                    ''')
                    if clicked:
                        logger.info("Clicked Excel using JavaScript")
                        return
                except Exception as e:
                    logger.debug(f"Strategy 4 for Excel button failed: {e}")
                    raise e
            
            try:
                file_path = await self.browser.wait_for_download(
                    click_excel_export,
                    timeout=60000
                )
            except Exception as e:
                logger.error(f"Download failed: {e}")
                return None
            
            # Rename file with proper naming convention including period
            # Generate filename with period info
            safe_tenant = "".join(c for c in tenant_name if c.isalnum() or c in (' ', '-', '_')).strip()
            safe_period = period.replace(" ", "_")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            new_filename = f"{report_type}_{safe_tenant}_{safe_period}_{timestamp}.xlsx"
            
            final_path = self.file_manager.rename_download(file_path, new_filename)
            
            # Validate the file
            if self.file_manager.validate_excel_file(final_path):
                logger.info(f"Successfully downloaded: {new_filename}")
                return final_path
            else:
                logger.warning(f"Downloaded file may be invalid: {new_filename}")
                return final_path  # Return anyway, let caller decide
                
        except Exception as e:
            logger.error(f"Error exporting payroll report to Excel: {e}")
            return None
    
    async def download_reports_for_tenant(
        self,
        tenant_id: str,
        tenant_name: str,
        reports: list[str] = None
    ) -> dict:
        """
        Download all specified reports for a tenant.
        
        Args:
            tenant_id: Xero tenant ID
            tenant_name: Tenant name for switching and file naming
            reports: List of report types to download
            
        Returns:
            Dict with results for each report
        """
        if reports is None:
            reports = ["activity_statement", "payroll_summary"]
        
        results = {
            "tenant_id": tenant_id,
            "tenant_name": tenant_name,
            "reports": {},
            "success": True
        }
        
        # Switch to tenant first
        switch_result = await self.switch_tenant(tenant_name)
        if not switch_result.get("success"):
            results["success"] = False
            results["error"] = f"Failed to switch tenant: {switch_result.get('error')}"
            return results
        
        # Download each report
        for report_type in reports:
            if report_type == "activity_statement":
                result = await self.download_activity_statement(tenant_name)
            elif report_type in ["payroll_summary", "payroll_activity_summary"]:
                result = await self.download_payroll_activity_summary(tenant_name)
            elif report_type == "balance_sheet":
                result = await self.download_balance_sheet(tenant_name)
            elif report_type == "profit_loss":
                result = await self.download_profit_loss(tenant_name)
            elif report_type == "aged_payables":
                result = await self.download_aged_payables(tenant_name)
            elif report_type == "aged_receivables":
                result = await self.download_aged_receivables(tenant_name)
            else:
                result = {"success": False, "error": f"Unknown report type: {report_type}"}

            results["reports"][report_type] = result

            if not result.get("success"):
                results["success"] = False

        return results

    # ==================================================================
    # BAS-specific report download methods
    # ==================================================================

    async def _download_standard_report(
        self,
        report_key: str,
        tenant_name: str,
        month: Optional[int] = None,
        year: Optional[int] = None,
        tenant_shortcode: str = None,
        enable_outstanding_gst: bool = False,
    ) -> dict:
        """
        Generic download method for standard Xero reports accessed via reporting.xero.com URL.

        These reports (Balance Sheet, P&L, Aged Payables, Aged Receivables) all follow
        the same pattern as Payroll Activity Summary:
        1. Navigate to report URL (reporting.xero.com/!{shortcode}/v1/Run/{code})
        2. Enter date range
        3. Click Update
        4. Optionally enable "Outstanding GST" column (for Aged Payables/Receivables)
        5. Export to Excel

        Args:
            report_key: Key in TENANT_URL_TEMPLATES (e.g. "balance_sheet")
            tenant_name: Name of the tenant
            month: Month (1-12)
            year: Year
            tenant_shortcode: Shortcode for URL navigation
            enable_outstanding_gst: If True, check the Outstanding GST column checkbox

        Returns:
            Dict with success status and file path
        """
        try:
            if month is None or year is None:
                now = datetime.now()
                if now.month == 1:
                    month = 12
                    year = now.year - 1
                else:
                    month = now.month - 1
                    year = now.year

            start_date, end_date = get_month_date_range(month, year)
            period_display = f"{calendar.month_name[month]} {year}"

            logger.info(
                f"Downloading {report_key} for {tenant_name}",
                period=period_display,
                start_date=start_date,
                end_date=end_date,
            )

            # Navigate to the report page
            if not tenant_shortcode:
                return {
                    "success": False,
                    "error": f"tenant_shortcode required for {report_key} download",
                }

            report_url = TENANT_URL_TEMPLATES[report_key].format(shortcode=tenant_shortcode)
            logger.info(f"Navigating to {report_key}: {report_url}")

            await self.page.goto(report_url, wait_until="domcontentloaded", timeout=60000)
            try:
                await self.page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                logger.debug("networkidle timeout, continuing...")
            await asyncio.sleep(3)

            # Verify tenant
            if tenant_shortcode:
                verification = await self._verify_tenant_shortcode(tenant_shortcode)
                if not verification["valid"]:
                    logger.error(
                        f"Wrong tenant detected before {report_key} download",
                        expected=tenant_shortcode,
                        actual=verification.get("current_shortcode"),
                    )
                    screenshot = await self.browser.take_screenshot(f"{report_key}_wrong_tenant")
                    return {
                        "success": False,
                        "error": f"Wrong tenant: {verification.get('reason')}",
                        "screenshot": screenshot,
                    }

            await self._take_debug_screenshot(f"{report_key}_page_loaded")

            # Enter date range
            date_entered = await self._enter_payroll_date_range(start_date, end_date)
            if not date_entered:
                logger.warning("Could not enter date range, proceeding with defaults")

            await self._take_debug_screenshot(f"{report_key}_dates_entered")

            # Enable "Outstanding GST" column if needed (for Aged Payables/Receivables)
            if enable_outstanding_gst:
                await self._enable_outstanding_gst_column()

            # Click Update
            update_clicked = await self._click_update_button()
            if update_clicked:
                try:
                    await self.page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    logger.debug("networkidle timeout after Update, continuing...")
                await asyncio.sleep(2)
            else:
                logger.warning("Could not click Update button")

            await self._take_debug_screenshot(f"{report_key}_updated")

            # Export to Excel (reuse the payroll export method — same UI pattern)
            file_path = await self._export_payroll_to_excel(
                report_type=report_key,
                tenant_name=tenant_name,
                period=period_display,
            )

            if file_path:
                return {
                    "success": True,
                    "file_path": file_path,
                    "file_name": os.path.basename(file_path),
                    "tenant_name": tenant_name,
                    "report_type": report_key,
                    "period": period_display,
                    "start_date": start_date,
                    "end_date": end_date,
                }
            else:
                screenshot = await self.browser.take_screenshot(f"{report_key}_export_failed")
                return {
                    "success": False,
                    "error": f"Failed to export {report_key}",
                    "screenshot": screenshot,
                }

        except Exception as e:
            logger.error(f"{report_key} download failed", error=str(e))
            try:
                screenshot = await self.browser.take_screenshot(f"{report_key}_error")
            except Exception:
                screenshot = None
            return {
                "success": False,
                "error": str(e),
                "screenshot": screenshot,
            }

    async def _enable_outstanding_gst_column(self) -> bool:
        """
        Enable the 'Outstanding GST' column in Aged Payables/Receivables reports.

        This checkbox is not checked by default in Xero but is required for BAS.
        Tries multiple selector strategies.
        """
        logger.info("Enabling Outstanding GST column...")
        selectors = [
            "text=Outstanding GST",
            "label:has-text('Outstanding GST')",
            "input[name*='outstanding'][type='checkbox']",
            "input[name*='gst'][type='checkbox']",
        ]

        for selector in selectors:
            try:
                element = await self.page.wait_for_selector(selector, timeout=5000)
                if element:
                    # Check if it's already checked
                    is_checked = await element.is_checked() if await element.get_attribute("type") == "checkbox" else False
                    if not is_checked:
                        await element.click()
                        logger.info("Outstanding GST column enabled")
                        await asyncio.sleep(1)
                    else:
                        logger.info("Outstanding GST column already enabled")
                    return True
            except Exception:
                continue

        # Try a more general approach: look for columns/settings panel
        try:
            # Try clicking a Columns button first if it exists
            columns_btn = await self.page.query_selector("button:has-text('Columns'), a:has-text('Columns')")
            if columns_btn:
                await columns_btn.click()
                await asyncio.sleep(1)
                # Now look for Outstanding GST checkbox
                gst_checkbox = await self.page.query_selector("text=Outstanding GST")
                if gst_checkbox:
                    await gst_checkbox.click()
                    logger.info("Outstanding GST column enabled via Columns panel")
                    return True
        except Exception as e:
            logger.warning(f"Could not enable Outstanding GST column: {e}")

        logger.warning("Could not find Outstanding GST checkbox — report may be missing this column")
        return False

    async def download_balance_sheet(
        self,
        tenant_name: str,
        month: Optional[int] = None,
        year: Optional[int] = None,
        tenant_shortcode: str = None,
        **kwargs,
    ) -> dict:
        """Download the Balance Sheet report."""
        return await self._download_standard_report(
            report_key="balance_sheet",
            tenant_name=tenant_name,
            month=month,
            year=year,
            tenant_shortcode=tenant_shortcode,
        )

    async def download_profit_loss(
        self,
        tenant_name: str,
        month: Optional[int] = None,
        year: Optional[int] = None,
        tenant_shortcode: str = None,
        **kwargs,
    ) -> dict:
        """Download the Profit & Loss report."""
        return await self._download_standard_report(
            report_key="profit_loss",
            tenant_name=tenant_name,
            month=month,
            year=year,
            tenant_shortcode=tenant_shortcode,
        )

    async def download_aged_payables(
        self,
        tenant_name: str,
        month: Optional[int] = None,
        year: Optional[int] = None,
        tenant_shortcode: str = None,
        **kwargs,
    ) -> dict:
        """Download the Aged Payables Detail report with Outstanding GST column enabled."""
        return await self._download_standard_report(
            report_key="aged_payables",
            tenant_name=tenant_name,
            month=month,
            year=year,
            tenant_shortcode=tenant_shortcode,
            enable_outstanding_gst=True,
        )

    async def download_aged_receivables(
        self,
        tenant_name: str,
        month: Optional[int] = None,
        year: Optional[int] = None,
        tenant_shortcode: str = None,
        **kwargs,
    ) -> dict:
        """Download the Aged Receivables Detail report with Outstanding GST column enabled."""
        return await self._download_standard_report(
            report_key="aged_receivables",
            tenant_name=tenant_name,
            month=month,
            year=year,
            tenant_shortcode=tenant_shortcode,
            enable_outstanding_gst=True,
        )
