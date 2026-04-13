"""
Report Orchestrator - Generic job runner for IAS and BAS report workflows.

Driven by ReportProfile definitions, this module:
1. Iterates report specs, checking conditions against the client
2. Calls the appropriate XeroAutomation download method for each
3. Consolidates downloaded files into a single Excel
4. Copies to OneDrive
5. Updates the Asana task
6. Logs results and cleans up temp files
"""

import os
import re
import calendar
from datetime import datetime
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

import structlog

from app.config import get_settings
from app.db.models import Client, DownloadLog
from app.services.report_profiles import ReportProfile

logger = structlog.get_logger()
settings = get_settings()


def _get_australian_fy_year() -> int:
    """Return the current Australian fiscal year (e.g. 2026 for July 2025 - June 2026)."""
    now = datetime.now()
    return now.year + 1 if now.month >= 7 else now.year


def _safe_name(tenant_name: str) -> str:
    """Sanitize tenant name for use in filenames."""
    return re.sub(r'[<>:"/\\|?*]', '', tenant_name)


def _format_period(report_type: str, month: int, year: int) -> str:
    """Format the period string based on report type."""
    if report_type == "bas":
        # Quarter mapping: Mar=Q3, Jun=Q4, Sep=Q1, Dec=Q2 (Australian FY quarters)
        quarter_map = {3: "Q3", 6: "Q4", 9: "Q1", 12: "Q2"}
        quarter = quarter_map.get(month, f"{calendar.month_name[month]}")
        return f"{quarter} {year}"
    else:
        return f"{calendar.month_name[month]} {year}"


async def run_report_job(
    profile: ReportProfile,
    client: Client,
    month: int,
    year: int,
    job_id: str,
    automation,  # XeroAutomation instance
    update_job_fn,  # Callback: (job_id, message) -> None
    db: AsyncSession,
) -> dict:
    """
    Run a full report job for a single client using the given profile.

    Returns a result dict with success status, file info, and any errors.
    """
    from app.services.file_manager import get_file_manager
    from app.services.asana_service import get_asana_service

    period = _format_period(profile.report_type, month, year)
    safe_tenant = _safe_name(client.tenant_name)

    log = logger.bind(
        client=client.tenant_name,
        report_type=profile.report_type,
        period=period,
        job_id=job_id,
    )

    downloaded_files = []
    sheet_name_map = {}  # {file_path: [target_sheet_names]}
    errors = []

    # --- Download each report in the profile ---
    for spec in profile.reports:
        # Check condition
        if spec.condition and not spec.condition(client):
            log.info(f"Skipping {spec.report_key} (condition not met)")
            continue

        update_job_fn(job_id, f"Downloading {spec.report_key}...")
        log.info(f"Downloading {spec.report_key}")

        try:
            # Build kwargs for the download method
            kwargs = {
                "tenant_name": client.tenant_name,
                "tenant_shortcode": client.tenant_shortcode,
            }

            # Activity statement uses period-based params
            if spec.download_method == "download_activity_statement":
                kwargs["period"] = period
                kwargs["find_unfiled"] = False
                kwargs["month"] = month
                kwargs["year"] = year
                kwargs["is_quarterly"] = profile.report_type == "bas"
            else:
                # Standard reporting URL-based reports use month/year
                kwargs["month"] = month
                kwargs["year"] = year

            result = await getattr(automation, spec.download_method)(**kwargs)

            if result.get("success"):
                downloaded_files.append(result["file_path"])
                sheet_name_map[result["file_path"]] = spec.sheet_names
                log.info(f"{spec.report_key} downloaded successfully")
            else:
                error_msg = f"{spec.report_key}: {result.get('error', 'Unknown error')}"
                errors.append(error_msg)
                log.error(f"{spec.report_key} failed", error=result.get("error"))

            # Log to DB
            await _log_download(
                db, client.id, profile.report_type, spec.report_key, result
            )

        except Exception as e:
            error_msg = f"{spec.report_key}: {str(e)}"
            errors.append(error_msg)
            log.error(f"{spec.report_key} exception", error=str(e))
            await _log_download(
                db, client.id, profile.report_type, spec.report_key,
                {"success": False, "error": str(e)}
            )

    # --- Consolidate ---
    consolidated_file = None
    file_manager = get_file_manager()

    if downloaded_files:
        update_job_fn(job_id, "Consolidating reports...")
        try:
            filename = profile.filename_template.format(
                tenant=safe_tenant, period=period
            )
            consolidated_path = file_manager.consolidate_excel_files(
                file_paths=downloaded_files,
                output_filename=filename,
                sheet_name_map=sheet_name_map,
            )
            consolidated_file = {
                "file_path": consolidated_path,
                "file_name": filename,
            }
            log.info("Consolidation complete", file=filename)
        except Exception as e:
            errors.append(f"Consolidation failed: {str(e)}")
            log.error("Consolidation failed", error=str(e))

    # --- Copy to OneDrive ---
    onedrive_path = None
    onedrive_folder = getattr(client, profile.onedrive_folder_attr, None)

    if consolidated_file and settings.one_drive_folder_origin and onedrive_folder:
        try:
            update_job_fn(job_id, "Copying to OneDrive...")
            fy_year = _get_australian_fy_year()
            onedrive_folder_with_fy = os.path.join(onedrive_folder, f"FY {fy_year}")
            onedrive_path = file_manager.copy_to_onedrive(
                source_path=consolidated_file["file_path"],
                onedrive_origin=settings.one_drive_folder_origin,
                client_onedrive_folder=onedrive_folder_with_fy,
            )
            consolidated_file["onedrive_path"] = onedrive_path
            update_job_fn(job_id, f"Saved to OneDrive: {os.path.basename(onedrive_path)}")
        except Exception as e:
            log.warning("OneDrive copy failed (non-fatal)", error=str(e))
            errors.append(f"OneDrive copy failed: {str(e)}")

    # --- Cleanup temp files (only if OneDrive copy succeeded) ---
    if onedrive_path:
        update_job_fn(job_id, "Cleaning up temporary files...")
        files_to_delete = downloaded_files.copy()
        if consolidated_file and consolidated_file.get("file_path"):
            files_to_delete.append(consolidated_file["file_path"])
        if files_to_delete:
            cleanup_result = file_manager.cleanup_job_files(files_to_delete)
            if cleanup_result["errors"]:
                errors.extend([f"Cleanup: {e}" for e in cleanup_result["errors"]])

    # --- Update Asana ---
    asana_updated = False
    asana_error = None
    asana_task_id = getattr(client, profile.asana_task_attr, None)

    if onedrive_path and asana_task_id and settings.asana_api_key:
        update_job_fn(job_id, "Updating Asana task...")
        asana_service = get_asana_service()

        # Build the section and reassignee GIDs from profile settings
        section_gid = getattr(settings, profile.asana_section_setting, "")
        reassignee_gid = getattr(settings, profile.asana_reassignee_setting, "")

        # Build team GIDs (for BAS, include the Income Tax team from settings)
        team_gids = []
        if profile.report_type == "bas" and settings.bas_asana_team_gid:
            team_gids.append(settings.bas_asana_team_gid)

        fy_year = _get_australian_fy_year()
        consolidated_filename = consolidated_file["file_name"] if consolidated_file else None
        asana_link = (
            file_manager.build_sharepoint_url(
                onedrive_folder=onedrive_folder,
                fy_year=fy_year,
                local_prefix=settings.onedrive_local_prefix,
                sharepoint_base_url=settings.sharepoint_base_url,
                filename=consolidated_filename,
            )
            or onedrive_path
        )

        asana_result = await asana_service.update_task_after_export(
            task_id_or_url=asana_task_id,
            onedrive_link=asana_link,
            filename=consolidated_filename,
            section_gid=section_gid,
            reassignee_gid=reassignee_gid,
            team_gids=team_gids,
        )
        asana_updated = asana_result["success"]
        asana_error = asana_result.get("error")
        if not asana_updated:
            log.warning("Asana update failed, sending fallback email", error=asana_error)
            await asana_service.send_fallback_email(onedrive_path, asana_error or "Unknown error")
            errors.append(f"Asana update failed: {asana_error}")
        else:
            update_job_fn(job_id, "Asana task updated")

    # --- Build final result ---
    success = len(downloaded_files) > 0
    result = {
        "consolidated_file": consolidated_file,
        "errors": errors,
        "asana_updated": asana_updated,
        "asana_error": asana_error,
    }
    if errors:
        result["error"] = "; ".join(errors)

    done_msg = f"Done — {consolidated_file['file_name']}" if consolidated_file else "Done with errors"
    update_job_fn(job_id, done_msg)

    return {"success": success, **result}


async def _log_download(
    db: AsyncSession,
    client_id: Optional[int],
    report_mode: str,
    report_type: str,
    result: dict,
) -> None:
    """Log a download attempt to the database."""
    log = DownloadLog(
        client_id=client_id,
        report_mode=report_mode,
        report_type=report_type,
        status="success" if result.get("success") else "failed",
        file_path=result.get("file_path"),
        file_name=result.get("file_name"),
        error_message=result.get("error"),
        screenshot_path=result.get("screenshot"),
        completed_at=datetime.utcnow() if result.get("success") else None,
    )
    db.add(log)
    await db.commit()
