"""Pydantic request models for API endpoints."""

from pydantic import BaseModel, Field
from typing import Optional, List, Literal


# Auth Models
class SwitchTenantRequest(BaseModel):
    tenant_name: str = Field(..., description="Name of the Xero tenant/organisation to switch to")
    tenant_shortcode: Optional[str] = Field(None, description="Tenant shortcode for URL-based switching")


# Report Models
class ReportRequest(BaseModel):
    tenant_id: str = Field(..., description="Xero tenant ID")
    tenant_name: str = Field(..., description="Xero tenant/organisation name")
    tenant_shortcode: Optional[str] = Field(None, description="Tenant shortcode")
    period: str = Field(..., description="Report period (e.g., 'October 2025')")
    find_unfiled: bool = Field(True, description="Find unfiled/draft statements")
    report_type: Literal["ias", "bas"] = Field("ias", description="Report type: 'ias' or 'bas'")


class PayrollReportRequest(BaseModel):
    tenant_id: str = Field(..., description="Xero tenant ID")
    tenant_name: str = Field(..., description="Xero tenant/organisation name")
    tenant_shortcode: Optional[str] = Field(None, description="Tenant shortcode")
    month: Optional[int] = Field(None, ge=1, le=12, description="Month (1-12)")
    year: Optional[int] = Field(None, ge=2020, le=2100, description="Year")


class ConsolidatedReportRequest(BaseModel):
    """Request for consolidated report download. Used by /run endpoint."""
    tenant_id: str = Field(..., description="Xero tenant ID")
    tenant_name: str = Field(..., description="Xero tenant/organisation name")
    tenant_shortcode: Optional[str] = Field(None, description="Tenant shortcode")
    month: int = Field(..., ge=1, le=12, description="Month (1-12)")
    year: int = Field(..., ge=2020, le=2100, description="Year")
    period: Optional[str] = Field(None, description="Activity Statement period. Derived from month/year if not provided")
    find_unfiled: bool = Field(False, description="Find unfiled/draft activity statements")
    report_type: Literal["ias", "bas"] = Field("ias", description="Report type: 'ias' or 'bas'")


class BatchDownloadRequest(BaseModel):
    """Request for batch report downloads."""
    tenant_ids: Optional[List[str]] = Field(None, description="List of tenant IDs (if None, all active)")
    month: Optional[int] = Field(None, ge=1, le=12, description="Month (1-12)")
    year: Optional[int] = Field(None, ge=2020, le=2100, description="Year")
    period: Optional[str] = Field(None, description="Period string for activity statements")
    report_type: Literal["ias", "bas"] = Field("ias", description="Report type: 'ias' or 'bas'")


# Client Models
class ClientCreate(BaseModel):
    tenant_id: str = Field(..., description="Xero tenant ID")
    tenant_name: str = Field(..., description="Xero tenant/organisation name")
    tenant_shortcode: Optional[str] = Field(None, description="Tenant shortcode")
    ias_onedrive_folder: Optional[str] = Field(None, description="OneDrive folder for IAS reports")
    ias_asana_task_id: Optional[str] = Field(None, description="Asana task ID for IAS")
    ias_is_active: bool = Field(False, description="Active for IAS automation")
    bas_onedrive_folder: Optional[str] = Field(None, description="OneDrive folder for BAS reports")
    bas_asana_task_id: Optional[str] = Field(None, description="Asana task ID for BAS")
    bas_is_active: bool = Field(False, description="Active for BAS automation")
    gst_accounting_method: Optional[str] = Field(None, description="'Cash Basis' or 'Accrual Basis'")
    paygi_frequency: Optional[str] = Field(None, description="'Monthly', 'Quarterly', or 'No Payroll'")


class ClientUpdate(BaseModel):
    tenant_name: Optional[str] = None
    tenant_shortcode: Optional[str] = None
    ias_onedrive_folder: Optional[str] = None
    ias_asana_task_id: Optional[str] = None
    ias_is_active: Optional[bool] = None
    bas_onedrive_folder: Optional[str] = None
    bas_asana_task_id: Optional[str] = None
    bas_is_active: Optional[bool] = None
    gst_accounting_method: Optional[str] = None
    paygi_frequency: Optional[str] = None
