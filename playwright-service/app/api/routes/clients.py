"""Client management endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional, Literal
import structlog

from app.db.connection import get_db
from app.db.models import Client
from app.models.requests import ClientCreate, ClientUpdate
from app.api.dependencies import verify_api_key

router = APIRouter()
logger = structlog.get_logger()


def _client_to_dict(c: Client) -> dict:
    """Serialize a Client model to a dict."""
    return {
        "id": c.id,
        "tenant_id": c.tenant_id,
        "tenant_name": c.tenant_name,
        "tenant_shortcode": c.tenant_shortcode,
        "ias_onedrive_folder": c.ias_onedrive_folder,
        "ias_asana_task_id": c.ias_asana_task_id,
        "ias_is_active": c.ias_is_active,
        "bas_onedrive_folder": c.bas_onedrive_folder,
        "bas_asana_task_id": c.bas_asana_task_id,
        "bas_is_active": c.bas_is_active,
        "gst_accounting_method": c.gst_accounting_method,
        "paygi_frequency": c.paygi_frequency,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


@router.get("/")
async def list_clients(
    report_type: Optional[Literal["ias", "bas"]] = Query(None, description="Filter by report type activity"),
    active_only: bool = True,
    api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db)
):
    """List all clients/tenants. Optionally filter by report_type to show only active clients for that type."""
    query = select(Client)
    if active_only and report_type == "ias":
        query = query.where(Client.ias_is_active == True)
    elif active_only and report_type == "bas":
        query = query.where(Client.bas_is_active == True)
    elif active_only:
        # No specific report type — show clients active in either
        from sqlalchemy import or_
        query = query.where(or_(Client.ias_is_active == True, Client.bas_is_active == True))

    result = await db.execute(query)
    clients = result.scalars().all()

    return {
        "success": True,
        "count": len(clients),
        "clients": [_client_to_dict(c) for c in clients]
    }


@router.get("/{client_id}")
async def get_client(
    client_id: int,
    api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db)
):
    """Get a specific client by ID."""
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()

    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    return _client_to_dict(client)


@router.post("/")
async def create_client(
    request: ClientCreate,
    api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db)
):
    """Create a new client."""
    existing = await db.execute(
        select(Client).where(Client.tenant_id == request.tenant_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"Client with tenant_id '{request.tenant_id}' already exists"
        )

    client = Client(
        tenant_id=request.tenant_id,
        tenant_name=request.tenant_name,
        tenant_shortcode=request.tenant_shortcode,
        ias_onedrive_folder=request.ias_onedrive_folder,
        ias_asana_task_id=request.ias_asana_task_id,
        ias_is_active=request.ias_is_active,
        bas_onedrive_folder=request.bas_onedrive_folder,
        bas_asana_task_id=request.bas_asana_task_id,
        bas_is_active=request.bas_is_active,
        gst_accounting_method=request.gst_accounting_method,
        paygi_frequency=request.paygi_frequency,
    )

    db.add(client)
    await db.commit()
    await db.refresh(client)

    logger.info("Client created", tenant_id=request.tenant_id, tenant_name=request.tenant_name)

    return {
        "success": True,
        "message": "Client created",
        "client": _client_to_dict(client)
    }


@router.put("/{client_id}")
async def update_client(
    client_id: int,
    request: ClientUpdate,
    api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db)
):
    """Update an existing client."""
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()

    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    # Update only provided fields
    update_data = request.model_dump(exclude_unset=True)
    for field_name, value in update_data.items():
        setattr(client, field_name, value)

    await db.commit()
    await db.refresh(client)

    logger.info("Client updated", client_id=client_id)

    return {
        "success": True,
        "message": "Client updated",
        "client": _client_to_dict(client)
    }


@router.delete("/{client_id}")
async def delete_client(
    client_id: int,
    api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db)
):
    """Delete a client (soft delete by setting both ias_is_active and bas_is_active to False)."""
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()

    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    client.ias_is_active = False
    client.bas_is_active = False
    await db.commit()

    logger.info("Client deactivated", client_id=client_id, tenant_id=client.tenant_id)

    return {
        "success": True,
        "message": "Client deactivated"
    }
