"""Report download endpoints — supports both IAS and BAS via report_type parameter."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from datetime import datetime, timedelta
import asyncio as _asyncio
import uuid
import structlog
import os

from app.db.connection import get_db, async_session_maker as AsyncSessionLocal
from app.db.models import DownloadLog, Client
from app.config import get_settings
from app.services.browser_manager import BrowserManager
from app.services.xero_automation import XeroAutomation
from app.services.xero_session import XeroSessionService
from app.services.xero_auth import XeroAuthService
from app.services.report_profiles import get_profile
from app.services.report_orchestrator import run_report_job
from app.api.dependencies import verify_api_key
from app.models import (
    ConsolidatedReportRequest,
    BatchDownloadRequest,
)

router = APIRouter()
logger = structlog.get_logger()
settings = get_settings()


# --- In-memory background job registry ---
_jobs: dict[str, dict] = {}
_JOB_TTL_HOURS = 1


def _create_job() -> str:
    """Create a new job entry and return its job_id."""
    cutoff = datetime.utcnow() - timedelta(hours=_JOB_TTL_HOURS)
    expired = [jid for jid, j in _jobs.items() if j.get("created_at", datetime.utcnow()) < cutoff]
    for jid in expired:
        del _jobs[jid]

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "status": "running",
        "message": "Starting...",
        "steps": [],
        "result": None,
        "created_at": datetime.utcnow(),
    }
    return job_id


def _update_job(job_id: str, message: str) -> None:
    """Append a step message to the job."""
    if job_id in _jobs:
        _jobs[job_id]["message"] = message
        _jobs[job_id]["steps"].append(message)


def _finish_job(job_id: str, success: bool, result: dict) -> None:
    """Mark a job as complete."""
    if job_id in _jobs:
        _jobs[job_id]["status"] = "success" if success else "failed"
        _jobs[job_id]["message"] = "Complete" if success else result.get("error", "Failed")
        _jobs[job_id]["result"] = result


async def _ensure_authenticated(db: AsyncSession) -> tuple[bool, dict]:
    """Ensure browser is authenticated with Xero."""
    browser_manager = await BrowserManager.get_instance()
    session_service = XeroSessionService(db)

    if not browser_manager.is_initialized:
        session_data = await session_service.get_session()
        if not session_data:
            return False, {"error": "No session found. Please run /api/auth/setup first."}

        auth_service = XeroAuthService(browser_manager)
        restore_result = await auth_service.restore_session(session_data.get("cookies", []))

        if not restore_result.get("success"):
            return False, {"error": "Failed to restore session. Please re-authenticate."}

    return True, {}


# =============================================================================
# Main Endpoints
# =============================================================================

@router.post("/run")
async def run_report(
    request: ConsolidatedReportRequest,
    api_key: str = Depends(verify_api_key),
):
    """
    Start a report job in the background.
    Supports both IAS (default) and BAS via report_type parameter.
    Returns immediately with a job_id. Poll GET /api/reports/job/{job_id} for status.
    """
    import calendar

    job_id = _create_job()
    profile = get_profile(request.report_type)
    period_label = f"{calendar.month_name[request.month]} {request.year}"

    _update_job(job_id, f"Queued: {request.tenant_name} — {period_label} ({request.report_type.upper()})")

    _asyncio.create_task(_run_job(job_id, request))

    return {
        "job_id": job_id,
        "tenant_name": request.tenant_name,
        "period": period_label,
        "report_type": request.report_type,
    }


async def _run_job(job_id: str, request: ConsolidatedReportRequest) -> None:
    """Background coroutine that runs the full report workflow via the orchestrator."""
    async with AsyncSessionLocal() as db:
        try:
            browser_manager = await BrowserManager.get_instance()

            async with browser_manager.request_lock:
                # Auth
                _update_job(job_id, "Ensuring authenticated with Xero...")
                is_auth, auth_error = await _ensure_authenticated(db)
                if not is_auth:
                    _finish_job(job_id, False, auth_error)
                    return

                automation = XeroAutomation(browser_manager)

                # Switch tenant
                if request.tenant_shortcode:
                    _update_job(job_id, f"Switching to {request.tenant_name}...")
                    switch_result = await automation.switch_tenant(
                        request.tenant_name, request.tenant_shortcode
                    )
                    if not switch_result.get("success"):
                        _finish_job(job_id, False, {
                            "error": f"Failed to switch tenant: {switch_result.get('error')}"
                        })
                        return

                # Look up client
                client_result = await db.execute(
                    select(Client).where(Client.tenant_id == request.tenant_id)
                )
                client = client_result.scalar_one_or_none()

                if not client:
                    _finish_job(job_id, False, {"error": f"Client not found: {request.tenant_id}"})
                    return

                # Run the profile-driven workflow
                profile = get_profile(request.report_type)
                result = await run_report_job(
                    profile=profile,
                    client=client,
                    month=request.month,
                    year=request.year,
                    job_id=job_id,
                    automation=automation,
                    update_job_fn=_update_job,
                    db=db,
                )

            _finish_job(job_id, result.get("success", False), result)

        except Exception as e:
            logger.error("Background job failed", job_id=job_id, error=str(e))
            _finish_job(job_id, False, {"error": str(e)})


@router.post("/batch")
async def batch_download(
    request: BatchDownloadRequest,
    api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Download reports for multiple tenants.
    If tenant_ids is not specified, processes all active clients for the given report_type.
    """
    profile = get_profile(request.report_type)
    logger.info("Batch download requested", report_type=request.report_type)

    # Get clients to process
    if request.tenant_ids:
        query = select(Client).where(Client.tenant_id.in_(request.tenant_ids))
    else:
        # Filter by the correct is_active flag based on report type
        if request.report_type == "bas":
            query = select(Client).where(Client.bas_is_active == True)
        else:
            query = select(Client).where(Client.ias_is_active == True)

    result = await db.execute(query)
    clients = result.scalars().all()

    if not clients:
        return {"success": False, "error": "No clients found to process"}

    browser_manager = await BrowserManager.get_instance()
    results = {
        "total": len(clients),
        "completed": 0,
        "failed": 0,
        "report_type": request.report_type,
        "results": []
    }

    async with browser_manager.request_lock:
        is_auth, auth_error = await _ensure_authenticated(db)
        if not is_auth:
            return {"success": False, **auth_error}

        automation = XeroAutomation(browser_manager)

        for client in clients:
            job_id = f"batch-{client.tenant_id}"
            try:
                # Switch tenant
                if client.tenant_shortcode:
                    switch_result = await automation.switch_tenant(
                        client.tenant_name, client.tenant_shortcode
                    )
                    if not switch_result.get("success"):
                        results["failed"] += 1
                        results["results"].append({
                            "tenant_name": client.tenant_name,
                            "success": False,
                            "error": f"Switch failed: {switch_result.get('error')}"
                        })
                        continue

                # Run the orchestrator for this client
                job_result = await run_report_job(
                    profile=profile,
                    client=client,
                    month=request.month,
                    year=request.year,
                    job_id=job_id,
                    automation=automation,
                    update_job_fn=lambda jid, msg: None,  # No job tracking for batch
                    db=db,
                )

                if job_result.get("success"):
                    results["completed"] += 1
                else:
                    results["failed"] += 1

                results["results"].append({
                    "tenant_name": client.tenant_name,
                    **job_result,
                })

            except Exception as e:
                results["failed"] += 1
                results["results"].append({
                    "tenant_name": client.tenant_name,
                    "success": False,
                    "error": str(e)
                })

    results["success"] = results["failed"] == 0
    return results


# =============================================================================
# Job Polling & File Access
# =============================================================================

@router.get("/job/{job_id}")
async def get_job_status(job_id: str, api_key: str = Depends(verify_api_key)):
    """Poll the status of a background report job."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    return {
        "job_id": job_id,
        "status": job["status"],
        "message": job["message"],
        "steps": job["steps"],
        "result": job["result"],
    }


@router.get("/download/{filename}")
async def download_file(filename: str, api_key: str = Depends(verify_api_key)):
    """Download a previously generated report file."""
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(settings.download_dir, safe_filename)

    if not os.path.abspath(file_path).startswith(os.path.abspath(settings.download_dir)):
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@router.get("/files")
async def list_downloaded_files(api_key: str = Depends(verify_api_key)):
    """List all downloaded report files."""
    from app.services.file_manager import get_file_manager

    file_manager = get_file_manager()
    files = file_manager.list_downloads()

    return {
        "success": True,
        "count": len(files),
        "files": files
    }


@router.get("/logs")
async def get_download_logs(
    limit: int = 50,
    status: Optional[str] = None,
    report_mode: Optional[str] = None,
    api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db)
):
    """Get download logs, optionally filtered by status or report_mode (ias/bas)."""
    query = select(DownloadLog).order_by(DownloadLog.started_at.desc()).limit(limit)

    if status:
        query = query.where(DownloadLog.status == status)
    if report_mode:
        query = query.where(DownloadLog.report_mode == report_mode)

    result = await db.execute(query)
    logs = result.scalars().all()

    return {
        "success": True,
        "count": len(logs),
        "logs": [
            {
                "id": log.id,
                "client_id": log.client_id,
                "report_mode": log.report_mode,
                "report_type": log.report_type,
                "status": log.status,
                "file_name": log.file_name,
                "error_message": log.error_message,
                "started_at": log.started_at.isoformat() if log.started_at else None,
                "completed_at": log.completed_at.isoformat() if log.completed_at else None,
            }
            for log in logs
        ]
    }
