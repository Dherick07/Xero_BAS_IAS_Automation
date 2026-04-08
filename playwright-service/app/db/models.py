from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.db.connection import Base


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(255), unique=True, nullable=False)
    tenant_name = Column(String(255), nullable=False)
    tenant_shortcode = Column(String(50), unique=True)
    ias_onedrive_folder = Column(String(500))
    ias_asana_task_id = Column(String(255))
    ias_is_active = Column(Boolean, default=False)
    bas_onedrive_folder = Column(String(500))
    bas_asana_task_id = Column(String(255))
    bas_is_active = Column(Boolean, default=False)
    gst_accounting_method = Column(String(20))
    paygi_frequency = Column(String(20))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class XeroSession(Base):
    __tablename__ = "xero_sessions"

    id = Column(Integer, primary_key=True, default=1)
    cookies = Column(Text, nullable=False)
    oauth_tokens = Column(Text)
    expires_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DownloadLog(Base):
    __tablename__ = "download_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.id"))
    report_mode = Column(String(10), nullable=False)
    report_type = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False)
    file_path = Column(String(500))
    file_name = Column(String(255))
    file_size = Column(Integer)
    error_message = Column(Text)
    screenshot_path = Column(String(500))
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))
    uploaded_to_onedrive = Column(Boolean, default=False)
    onedrive_path = Column(String(500))
