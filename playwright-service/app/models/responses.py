"""Pydantic response models for API endpoints."""

from pydantic import BaseModel, Field
from typing import Optional, Any
from datetime import datetime


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    database: str
    browser: dict


class AuthStatusResponse(BaseModel):
    """Authentication status response."""
    logged_in: bool
    current_tenant: Optional[str] = None
    needs_reauth: bool
    expires_at: Optional[datetime] = None
    message: Optional[str] = None


class TenantListResponse(BaseModel):
    """Tenant list response."""
    success: bool
    tenants: list[dict] = []
    error: Optional[str] = None


class ReportDownloadResponse(BaseModel):
    """Report download response."""
    success: bool
    file_path: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    tenant_id: Optional[str] = None
    tenant_name: Optional[str] = None
    report_type: Optional[str] = None
    report_period: Optional[str] = None
    downloaded_at: Optional[datetime] = None
    error: Optional[str] = None
    screenshot: Optional[str] = None


class BatchDownloadResponse(BaseModel):
    """Batch download response."""
    success: bool
    total: int
    completed: int
    failed: int
    results: list[dict] = []
    errors: list[dict] = []


class ClientResponse(BaseModel):
    """Client response."""
    id: int
    tenant_id: str
    tenant_name: str
    is_active: bool
    onedrive_folder: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class DownloadLogResponse(BaseModel):
    """Download log response."""
    id: int
    client_id: Optional[int]
    report_type: str
    status: str
    file_path: Optional[str]
    file_name: Optional[str]
    file_size: Optional[int]
    error_message: Optional[str]
    screenshot_path: Optional[str]
    started_at: datetime
    completed_at: Optional[datetime]
    uploaded_to_onedrive: bool
    onedrive_path: Optional[str]


class BrowserStatusResponse(BaseModel):
    """Browser status response."""
    initialized: bool
    headless: bool
    browser_connected: bool
    context_active: bool
    page_active: bool


class GenericResponse(BaseModel):
    """Generic API response."""
    success: bool
    message: Optional[str] = None
    data: Optional[Any] = None
    error: Optional[str] = None
