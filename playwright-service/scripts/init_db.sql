-- ============================================================
-- Xero BAS/IAS Automation - Database Schema
-- ============================================================

-- -----------------------------------------------
-- Trigger function: auto-update updated_at column
-- -----------------------------------------------
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- -----------------------------------------------
-- Table: clients
-- -----------------------------------------------
CREATE TABLE IF NOT EXISTS clients (
    id                    SERIAL PRIMARY KEY,
    tenant_id             VARCHAR(255) UNIQUE NOT NULL,
    tenant_name           VARCHAR(255) NOT NULL,
    tenant_shortcode      VARCHAR(50) UNIQUE,
    ias_onedrive_folder   VARCHAR(500),
    ias_asana_task_id     VARCHAR(255),
    ias_is_active         BOOLEAN DEFAULT false,
    bas_onedrive_folder   VARCHAR(500),
    bas_asana_task_id     VARCHAR(255),
    bas_is_active         BOOLEAN DEFAULT false,
    gst_accounting_method VARCHAR(20),
    paygi_frequency       VARCHAR(20),
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    updated_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_clients_tenant_id ON clients(tenant_id);
CREATE INDEX IF NOT EXISTS idx_clients_tenant_shortcode ON clients(tenant_shortcode);
CREATE INDEX IF NOT EXISTS idx_clients_ias_is_active ON clients(ias_is_active);
CREATE INDEX IF NOT EXISTS idx_clients_bas_is_active ON clients(bas_is_active);

CREATE TRIGGER trg_clients_updated_at
    BEFORE UPDATE ON clients
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------
-- Table: xero_sessions (single-row, id=1 enforced)
-- -----------------------------------------------
CREATE TABLE IF NOT EXISTS xero_sessions (
    id          INTEGER PRIMARY KEY DEFAULT 1,
    cookies     TEXT NOT NULL,
    oauth_tokens TEXT,
    expires_at  TIMESTAMPTZ,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT single_session CHECK (id = 1)
);

CREATE TRIGGER trg_xero_sessions_updated_at
    BEFORE UPDATE ON xero_sessions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------
-- Table: download_logs
-- -----------------------------------------------
CREATE TABLE IF NOT EXISTS download_logs (
    id                   SERIAL PRIMARY KEY,
    client_id            INTEGER REFERENCES clients(id),
    report_mode          VARCHAR(10) NOT NULL,
    report_type          VARCHAR(50) NOT NULL,
    status               VARCHAR(20) NOT NULL,
    file_path            VARCHAR(500),
    file_name            VARCHAR(255),
    file_size            INTEGER,
    error_message        TEXT,
    screenshot_path      VARCHAR(500),
    started_at           TIMESTAMPTZ DEFAULT NOW(),
    completed_at         TIMESTAMPTZ,
    uploaded_to_onedrive BOOLEAN DEFAULT false,
    onedrive_path        VARCHAR(500)
);

CREATE INDEX IF NOT EXISTS idx_download_logs_client_id ON download_logs(client_id);
CREATE INDEX IF NOT EXISTS idx_download_logs_report_mode ON download_logs(report_mode);
CREATE INDEX IF NOT EXISTS idx_download_logs_status ON download_logs(status);
CREATE INDEX IF NOT EXISTS idx_download_logs_started_at ON download_logs(started_at);
