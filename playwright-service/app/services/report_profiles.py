from dataclasses import dataclass, field
from typing import Literal, Optional, Callable, Any


@dataclass
class ReportSpec:
    """Defines a single report to download."""
    report_key: str                        # e.g. "activity_statement", "balance_sheet"
    sheet_names: list[str]                 # Target sheet names in consolidated xlsx
    download_method: str                   # Method name on XeroAutomation
    xero_report_code: str | None = None    # Xero report code (e.g. "1017" for Balance Sheet)
    condition: Callable | None = None      # Optional: (client) -> bool, skips if False


@dataclass
class ReportProfile:
    """Defines a complete report workflow for a report type."""
    report_type: Literal["ias", "bas"]
    reports: list[ReportSpec]
    filename_template: str                 # e.g. "{tenant} - {period} IAS.xlsx"
    onedrive_folder_attr: str              # Client model attribute for OneDrive path
    asana_task_attr: str                   # Client model attribute for Asana task ID
    asana_section_setting: str             # Settings attribute name for section GID
    asana_reassignee_setting: str          # Settings attribute name for reassignee GID
    asana_team_gids: list[str] = field(default_factory=list)  # Team GIDs to tag in comments


# ---------------------------------------------------------------------------
# IAS Profile
# ---------------------------------------------------------------------------
IAS_PROFILE = ReportProfile(
    report_type="ias",
    reports=[
        ReportSpec(
            report_key="activity_statement",
            sheet_names=["Activity_Statement"],
            download_method="download_activity_statement",
        ),
        ReportSpec(
            report_key="payroll_activity_summary",
            sheet_names=["Payroll_Activity_Summary"],
            download_method="download_payroll_activity_summary",
            xero_report_code="2035",
        ),
    ],
    filename_template="{tenant} - {period} IAS.xlsx",
    onedrive_folder_attr="ias_onedrive_folder",
    asana_task_attr="ias_asana_task_id",
    asana_section_setting="ias_asana_section_gid",
    asana_reassignee_setting="ias_asana_reassignee_gid",
)

# ---------------------------------------------------------------------------
# BAS Profile
# ---------------------------------------------------------------------------
BAS_PROFILE = ReportProfile(
    report_type="bas",
    reports=[
        ReportSpec(
            report_key="activity_statement_summary",
            sheet_names=["GST Summary", "GST Detail", "BAS field"],
            download_method="download_activity_statement",
        ),
        ReportSpec(
            report_key="balance_sheet",
            sheet_names=["BS"],
            download_method="download_balance_sheet",
            xero_report_code="1017",
        ),
        ReportSpec(
            report_key="profit_loss",
            sheet_names=["P&L"],
            download_method="download_profit_loss",
            xero_report_code="1016",
        ),
        ReportSpec(
            report_key="payroll_activity_summary",
            sheet_names=["Payroll Activity Smry"],
            download_method="download_payroll_activity_summary",
            xero_report_code="2035",
            condition=lambda client: (
                client.paygi_frequency is not None
                and client.paygi_frequency != "No Payroll"
            ),
        ),
        ReportSpec(
            report_key="aged_payables",
            sheet_names=["AP"],
            download_method="download_aged_payables",
            xero_report_code="1003",
            condition=lambda client: client.gst_accounting_method == "Cash Basis",
        ),
        ReportSpec(
            report_key="aged_receivables",
            sheet_names=["AR"],
            download_method="download_aged_receivables",
            xero_report_code="1002",
            condition=lambda client: client.gst_accounting_method == "Cash Basis",
        ),
    ],
    filename_template="{tenant} - {period} BAS.xlsx",
    onedrive_folder_attr="bas_onedrive_folder",
    asana_task_attr="bas_asana_task_id",
    asana_section_setting="bas_asana_section_gid",
    asana_reassignee_setting="bas_asana_reassignee_gid",
    # asana_team_gids populated from settings at runtime
)


def get_profile(report_type: str) -> ReportProfile:
    """Return the ReportProfile for the given report type."""
    profiles = {"ias": IAS_PROFILE, "bas": BAS_PROFILE}
    if report_type not in profiles:
        raise ValueError(f"Unknown report type: {report_type}")
    return profiles[report_type]
