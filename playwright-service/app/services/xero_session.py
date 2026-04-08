"""
Xero Session Service - Manages Xero authentication sessions.

Handles:
- Cookie storage and retrieval (encrypted)
- Session validation
- Session expiry detection
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
import structlog

from app.db.models import XeroSession
from app.services.encryption import get_encryption_service

logger = structlog.get_logger()


class XeroSessionService:
    """
    Manages Xero session cookies and authentication state.
    
    Sessions are stored encrypted in PostgreSQL and restored
    to the browser for automated operations.
    """
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.encryption = get_encryption_service()
    
    async def get_session(self) -> Optional[dict]:
        """
        Retrieve the stored Xero session.
        
        Returns:
            Dict with cookies and metadata, or None if no session exists
        """
        result = await self.db.execute(
            select(XeroSession).where(XeroSession.id == 1)
        )
        session = result.scalar_one_or_none()
        
        if not session:
            logger.debug("No session found in database")
            return None
        
        try:
            cookies = self.encryption.decrypt_json(session.cookies)
            return {
                "cookies": cookies,
                "expires_at": session.expires_at,
                "updated_at": session.updated_at,
            }
        except Exception as e:
            logger.error("Failed to decrypt session", error=str(e))
            return None
    
    async def save_session(
        self,
        cookies: list[dict],
        expires_at: Optional[datetime] = None
    ) -> bool:
        """
        Save Xero session cookies.
        
        Args:
            cookies: List of cookie dicts from Playwright
            expires_at: Optional expiry timestamp
            
        Returns:
            True if saved successfully
        """
        try:
            # Encrypt cookies
            encrypted_cookies = self.encryption.encrypt_json(cookies)
            
            # Default expiry: 2 weeks from now
            # Use naive UTC datetimes (no tzinfo) to match TIMESTAMP WITHOUT TIME ZONE columns
            if expires_at is None:
                expires_at = datetime.utcnow() + timedelta(weeks=2)
            elif expires_at.tzinfo is not None:
                expires_at = expires_at.replace(tzinfo=None)
            
            # Check if session exists
            result = await self.db.execute(
                select(XeroSession).where(XeroSession.id == 1)
            )
            existing = result.scalar_one_or_none()
            
            if existing:
                # Update existing session
                existing.cookies = encrypted_cookies
                existing.expires_at = expires_at
                existing.updated_at = datetime.utcnow()
            else:
                # Create new session
                session = XeroSession(
                    id=1,
                    cookies=encrypted_cookies,
                    expires_at=expires_at,
                )
                self.db.add(session)
            
            await self.db.commit()
            logger.info("Session saved", cookie_count=len(cookies), expires_at=expires_at.isoformat())
            return True
            
        except Exception as e:
            logger.error("Failed to save session", error=str(e))
            await self.db.rollback()
            return False
    
    async def delete_session(self) -> bool:
        """Delete the stored session."""
        try:
            result = await self.db.execute(
                select(XeroSession).where(XeroSession.id == 1)
            )
            session = result.scalar_one_or_none()
            
            if session:
                await self.db.delete(session)
                await self.db.commit()
                logger.info("Session deleted")
            
            return True
        except Exception as e:
            logger.error("Failed to delete session", error=str(e))
            await self.db.rollback()
            return False
    
    async def is_session_valid(self) -> bool:
        """
        Check if the stored session is still valid (not expired).
        
        Returns:
            True if session exists and hasn't expired
        """
        session_data = await self.get_session()
        
        if not session_data:
            return False
        
        expires_at = session_data.get("expires_at")
        if expires_at:
            # Handle both timezone-aware and naive datetimes
            now = datetime.now(timezone.utc)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < now:
                logger.warning("Session has expired", expires_at=expires_at.isoformat())
                return False
        
        return True
    
    async def get_session_status(self) -> dict:
        """
        Get detailed session status.
        
        Returns:
            Dict with session status information
        """
        session_data = await self.get_session()
        
        if not session_data:
            return {
                "has_session": False,
                "is_valid": False,
                "expires_at": None,
                "updated_at": None,
                "cookie_count": 0,
            }
        
        expires_at = session_data.get("expires_at")
        is_expired = False
        if expires_at:
            now = datetime.now(timezone.utc)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            is_expired = expires_at < now
        
        return {
            "has_session": True,
            "is_valid": not is_expired,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "updated_at": session_data.get("updated_at").isoformat() if session_data.get("updated_at") else None,
            "cookie_count": len(session_data.get("cookies", [])),
        }
