"""
Kuriflow MCP Server — Back-office execution engine for AI agents.

Exposes Kuriflow's domain-specific capabilities as MCP tools that
Claude Cowork, Gemini CLI, or any MCP-compatible agent can call.

Tools (v3):
    1. query_regulation    — Query regulatory knowledge base (multi-country)
    2. list_workflows      — List saved workflows
    3. list_kuris          — List available kuris (pre-built specialist agents)
    4. run_workflow        — Execute a saved workflow with optional file uploads
    5. save_workflow       — Create a repeatable workflow from simplified steps
    6. request_approval    — Request human approval (governance layer for AI agents)
    7. get_approval_status — Check approval decision status
    8. get_execution_result — Get execution results and audit trail
"""

import base64
import json
import logging
import os
import sys
from typing import Optional, Union

import httpx
from mcp.server.fastmcp import FastMCP

from kuriflow_mcp.client import KuriflowClient

logger = logging.getLogger(__name__)

# ─── Auth ────────────────────────────────────────────────────────────

SIGNUP_MESSAGE = (
    "To use Kuriflow, sign up at https://kuriflow.com/signup with your "
    "Google account (takes 30 seconds). Your API key will be shown after "
    "signup — add it to your Claude MCP settings as KURIFLOW_API_KEY."
)

API_URL = os.environ.get("KURIFLOW_API_URL", "https://api.kuriflow.com").rstrip("/")


def _check_auth() -> Optional[str]:
    """Return a signup-link message if auth is missing, else None."""
    api_url = os.environ.get("KURIFLOW_API_URL", "")
    api_key = os.environ.get("KURIFLOW_API_KEY", "")
    if not api_key or not api_url:
        return json.dumps({"message": SIGNUP_MESSAGE})
    return None


def _get_client_for_key(api_key: str) -> KuriflowClient:
    """Create a KuriflowClient for a specific user's API key."""
    return KuriflowClient(base_url=API_URL, api_key=api_key)


# ─── Token Verifier (remote MCP auth) ───────────────────────────────

class KuriflowTokenVerifier:
    """Validates kf_ API keys against the Kuriflow backend.

    Used when running as a remote MCP server (streamable-http).
    Claude sends the user's API key as a Bearer token.
    """

    async def verify_token(self, token: str):
        """Verify a kf_ API key by calling the backend /auth/me endpoint."""
        from mcp.server.auth.provider import AccessToken

        if not token.startswith("kf_"):
            return None

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{API_URL}/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code == 200:
                    user_data = resp.json()
                    return AccessToken(
                        token=token,
                        client_id=user_data.get("id", "unknown"),
                        scopes=["kuriflow:all"],
                    )
        except Exception as e:
            logger.warning("Token verification failed: %s", e)

        return None


# ─── Server Setup ─────────────────────────────────────────────────────

_transport = os.environ.get("KURIFLOW_MCP_TRANSPORT", "stdio")
_server_kwargs: dict = {}
if _transport != "stdio":
    from mcp.server.auth.settings import AuthSettings
    _server_kwargs["token_verifier"] = KuriflowTokenVerifier()
    _server_kwargs["auth"] = AuthSettings(
        issuer_url=API_URL,
        resource_server_url=API_URL,
    )

mcp = FastMCP(
    "Kuriflow",
    **_server_kwargs,
    instructions=(
        "Kuriflow automates repeatable tasks. Users are non-technical — "
        "never expose kuri_type, step types, or technical details.\n\n"
        "DO NOT call save_workflow until ALL steps below are complete.\n\n"
        "BEFORE CALLING save_workflow:\n"
        "1. Write and execute the Python script in this conversation.\n"
        "2. The script must use a SPECIFIC input filename. NEVER use glob or wildcards.\n"
        "3. If the user has a template file (.docx, .xlsx, .pptx, etc.), ask for its "
        "full local path and pass it as: template_file_paths=[\"/full/path/to/file.docx\"].\n"
        "4. Ask how new data will ARRIVE. Two modes:\n"
        "   - EMAIL (event-driven): workflow fires automatically the moment an email with an attachment arrives. Do NOT ask for a schedule — there is none. Just pass input_source=<email address>.\n"
        "   - GOOGLE DRIVE (polling): workflow checks the folder on a schedule. Ask the user for check frequency (hourly, every 15 min, daily at 9am, etc.) and pass both input_source=<folder URL> and schedule=<cron>.\n"
        "   - NO INPUT (manual/scheduled only): for scripts that fetch their own data. Ask if the user wants a schedule; pass schedule=<cron> or omit for manual.\n"
        "5. Ask where results go: recipient email OR a DIFFERENT Drive folder.\n\n"
        "ROUTING — pick exactly one kuri_type:\n"
        "1. financial_analysis_v2_kuri — financial models (DCF, LBO, comps). Script fetches its own data. No input files.\n"
        "2. spreadsheet_mcp_kuri — input file (Excel/CSV) → output spreadsheet.\n"
        "3. word_mcp_kuri — output is a document (.docx, .pdf, .pptx). Any data source.\n"
        "4. data_analytics_mcp_kuri — output is charts (.png/.jpg/.svg) or HTML dashboard (.html) from tabular data. Uses matplotlib/seaborn/plotly.\n\n"
        "MIXED OUTPUTS: scripts can write multiple output file types in one run (e.g., a .docx report with an embedded chart, plus a .xlsx data appendix). Pick kuri_type by the PRIMARY output — secondary files are delivered alongside.\n\n"
        "After saving: confirm in plain English. Never expose kuri_type or IDs to the user."
    ),
    host=os.environ.get("FASTMCP_HOST", os.environ.get("HOST", "0.0.0.0")),
    port=int(os.environ.get("FASTMCP_PORT", os.environ.get("PORT", "8080"))),
)


def _get_client() -> KuriflowClient:
    """Get or create the Kuriflow API client.

    In remote mode (streamable-http), uses the per-user API key from
    the authenticated request. In local mode (stdio), uses env var.
    """
    if _transport != "stdio":
        # Remote mode — get user's API key from auth context
        try:
            from mcp.server.auth.middleware.auth_context import get_access_token
            token = get_access_token()
            if token and token.token:
                return _get_client_for_key(token.token)
        except Exception:
            pass
    return KuriflowClient()


# ─── Tool 1: Query Regulation ────────────────────────────────────────


@mcp.tool()
async def query_regulation(
    query: str,
    country_code: str = "TH",
    domain: Optional[str] = None,
    category: Optional[str] = None,
) -> str:
    """Query the regulatory knowledge base for labor law, tax, and social security rules.

    Returns authoritative regulatory information with legal references
    and effective dates. Multi-country support — pass the country_code
    for the jurisdiction you need. Available domains per country:
    - labor_law: OT rates, severance tiers, leave entitlements, working hours
    - social_security: contribution rates, caps, employer matching
    - tax: progressive income tax brackets, withholding rates, filing deadlines

    Args:
        query: Natural language query about regulations.
            Examples: "OT rate for holidays", "severance after 3 years",
            "social security cap", "income tax brackets"
        country_code: ISO country code (e.g., "TH", "SG", "ID", "MY").
            Default "TH". Use list_regulation_packs to see available countries.
        domain: Optional domain filter: "labor_law", "social_security", "tax".
        category: Optional category within domain for narrower results.

    Returns:
        Regulatory text with legal references, formatted for LLM consumption.
    """
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    client = _get_client()

    try:
        result = await client.query_regulation(
            country_code=country_code,
            query=query,
            domain=domain,
            category=category,
        )
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.error(f"query_regulation failed: {e}")
        return json.dumps({"error": str(e)})


# ─── Tool 3: List Workflows ──────────────────────────────────────────


@mcp.tool()
async def list_workflows() -> str:
    """List all saved workflows for the current organization.

    Returns workflow names, IDs, descriptions, types (kuri_type),
    and whether they are active. Use the workflow ID with run_workflow
    to execute a saved workflow.

    Returns:
        JSON array of workflow objects with id, name, description,
        kuri_type, is_active, created_at, updated_at.
    """
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    client = _get_client()

    try:
        workflows = await client.list_workflows()
        # Return a clean summary for each workflow
        summary = []
        for w in workflows:
            summary.append({
                "id": w.get("id"),
                "name": w.get("name"),
                "description": w.get("description"),
                "kuri_type": w.get("kuri_type"),
                "is_active": w.get("is_active"),
                "created_at": w.get("created_at"),
            })
        return json.dumps(summary, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.error(f"list_workflows failed: {e}")
        return json.dumps({"error": str(e)})


# ─── Tool 4: Run Workflow ─────────────────────────────────────────────


@mcp.tool()
async def run_workflow(
    workflow_id: str,
    model: str,
    file_paths: Optional[list[str]] = None,
    file_keys: Optional[list[str]] = None,
    context_vars: Optional[dict] = None,
) -> str:
    """Execute a saved workflow, optionally with file uploads and context variables.

    If the workflow requires input files (e.g., attendance data, invoices),
    provide file_paths and corresponding file_keys. The file_keys should
    match what the workflow expects (e.g., "attendance", "employees", "invoice").

    Args:
        workflow_id: UUID of the workflow to execute. Get this from list_workflows.
        model: Your AI model ID (e.g., "claude-opus-4-6", "claude-sonnet-4-6").
            Required so Kuriflow uses the same model for AI processing steps.
        file_paths: Optional list of local file paths to upload before execution.
        file_keys: Optional list of keys for each file (must match file_paths length).
            Common keys: "attendance", "employees", "calendar", "invoice", "rates".
        context_vars: Optional dictionary of context variables to pass to the workflow.

    Returns:
        Execution result with status, extracted data, execution log,
        and any output files generated.
    """
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    client = _get_client()
    from uuid import uuid4
    session_id = str(uuid4())

    try:
        # Upload files if provided
        if file_paths:
            keys = file_keys or [f"file_{i}" for i in range(len(file_paths))]
            if len(keys) != len(file_paths):
                return json.dumps({
                    "error": "file_paths and file_keys must have the same length"
                })

            for path, key in zip(file_paths, keys):
                try:
                    with open(path, "rb") as f:
                        content = f.read()
                except FileNotFoundError:
                    return json.dumps({"error": f"File not found: {path}"})

                await client.upload_file(
                    session_id=session_id,
                    file_key=key,
                    file_content=content,
                    filename=os.path.basename(path),
                )

        # Run the workflow
        result = await client.run_workflow(
            workflow_id=workflow_id,
            session_id=session_id,
            initial_context=context_vars,
            model=model,
        )
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.error(f"run_workflow failed: {e}")
        return json.dumps({"error": str(e)})


# ─── Tool 5: Save Workflow ───────────────────────────────────────


@mcp.tool()
async def save_workflow(
    name: str,
    script: str,
    kuri_type: str,
    model: str,
    deliver_to: Optional[str] = None,
    output_drive_url: Optional[str] = None,
    email_subject: Optional[str] = None,
    schedule: Optional[str] = None,
    timezone: str = "UTC",
    input_source: Optional[str] = None,
    subject_filter: Optional[str] = None,
    file_pattern: Optional[str] = None,
    input_file_map: Optional[dict] = None,
    expected_columns: Optional[list] = None,
    template_file_paths: Optional[list] = None,
    description: Optional[str] = None,
) -> str:
    """Save a Python script as a repeatable workflow. Zero AI cost per run.

    Pass the script you already wrote in this conversation. Kuriflow
    re-runs it on schedule or when new data arrives.

    Args:
        name: Workflow name (e.g., "Weekly Sales Summary").
        script: Full Python script source code. Must use specific filenames
            (e.g., pd.read_excel('data.xlsx')), NOT glob or wildcards.
        kuri_type: REQUIRED. One of: "financial_analysis_v2_kuri",
            "spreadsheet_mcp_kuri", "word_mcp_kuri", "data_analytics_mcp_kuri".
            See routing in system instructions.
        model: Your AI model ID (e.g., "claude-opus-4-6").
        deliver_to: Email address to send results to.
            Provide either deliver_to OR output_drive_url (not both).
        output_drive_url: Google Drive folder URL to upload results to.
            Provide either deliver_to OR output_drive_url (not both).
            Must be a DIFFERENT folder from input_source.
        email_subject: Subject line for result emails. Defaults to workflow name.
            Ignored if output_drive_url is set.
        schedule: Cron expression. Format: "minute hour day month weekday".
            Examples: "0 9 * * 1" (Monday 9am), "0 12 * * *" (daily noon).
            Omit for manual-only.
        timezone: Timezone for schedule. Default "UTC". Example: "Asia/Bangkok".
        input_source: Where new data comes from. Pass one of:
            - Google Drive folder URL (e.g., "https://drive.google.com/drive/folders/...")
            - Email address to monitor (e.g., "invoices@company.com")
            Omit if script fetches its own data (financial_analysis_v2_kuri).
        subject_filter: For email input — keyword in subject line to match.
        file_pattern: For Google Drive input — filename pattern to match
            (e.g., "sales_data*"). If omitted, downloads the newest file.
        input_file_map: Maps arriving files to filenames the script reads.
            Format: {"filename.xlsx": "filename.xlsx"}.
            Required for spreadsheet_mcp_kuri.
        expected_columns: Column headers the script reads from the input file.
            Required for spreadsheet_mcp_kuri with email input.
        template_file_paths: Local paths to template files to bundle with the workflow.
            Example: ["/Users/name/offer_letter.docx"]. The MCP server reads and
            encodes them — do NOT pass base64 strings here, just file paths.
        description: Optional description.

    Returns:
        JSON with workflow id and schedule info.
    """
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    client = _get_client()

    try:
        # Validate output — exactly one delivery method
        if not deliver_to and not output_drive_url:
            return json.dumps({
                "error": "Provide either deliver_to (email) or output_drive_url (Google Drive folder)."
            })
        if deliver_to and output_drive_url:
            return json.dumps({
                "error": "Provide only one of deliver_to or output_drive_url, not both."
            })

        # --- Encode template files (MCP server reads local paths) ---
        assets: dict = {}
        if template_file_paths:
            for path in template_file_paths:
                try:
                    with open(path, "rb") as f:
                        data = f.read()
                    assets[os.path.basename(path)] = base64.b64encode(data).decode()
                    logger.info(f"save_workflow: encoded template '{os.path.basename(path)}' ({len(data):,} bytes)")
                except FileNotFoundError:
                    return json.dumps({"error": f"Template file not found: {path}"})

        # --- Build step config ---
        step_config = {"script": script}
        if input_file_map:
            step_config["input_file_map"] = input_file_map
        if assets:
            step_config["assets"] = assets
        steps = [{"type": "python_script", "config": step_config}]

        # --- Build input config from explicit params ---
        input_method = None
        input_config = None
        if input_source:
            if "drive.google.com" in input_source:
                input_method = "google_drive"
                input_config = {"folder_url": input_source}
                if file_pattern:
                    input_config["file_pattern"] = file_pattern
                # Set file_key to match input_file_map key
                if input_file_map:
                    input_config["file_key"] = next(iter(input_file_map.keys()))
            elif "@" in input_source:
                input_method = "email_attachment"
                input_config = {"monitor_email": input_source}
                if subject_filter:
                    input_config["subject_filter"] = subject_filter
                if expected_columns:
                    input_config["expected_columns"] = expected_columns

        # --- Build output config from explicit params ---
        if output_drive_url:
            output_method = "google_drive"
            output_config = {"folder_url": output_drive_url}
        else:
            output_method = "email"
            output_config = {
                "recipient_email": deliver_to,
                "subject": email_subject or name,
            }

        # --- Trigger ---
        trigger_type = "scheduled" if schedule else "manual"

        result = await client.save_workflow(
            name=name,
            steps=steps,
            description=description,
            trigger_type=trigger_type,
            schedule=schedule,
            timezone=timezone,
            plugins=None,
            input_method=input_method,
            input_config=input_config,
            output_method=output_method,
            output_config=output_config,
            kuri_type=kuri_type,
            model=model,
        )
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.error(f"save_workflow failed: {e}")
        return json.dumps({"error": str(e)})


# ─── Tool 6: Request Approval ───────────────────────────────────


@mcp.tool()
async def request_approval(
    title: str,
    approver_email: str,
    description: Optional[str] = None,
    category: str = "general",
    context_data: Optional[dict] = None,
    urgency: str = "normal",
    expires_in_hours: Optional[int] = None,
) -> str:
    """Request human approval before proceeding with an action.

    Use this when you need a human to sign off on something before
    continuing. The approver receives an email notification and can
    approve or reject via Kuriflow.

    Common use cases:
    - Financial: "Approve payment of $50,000 to vendor X"
    - HR: "Approve salary adjustment for employee Y"
    - Operations: "Approve production batch release"
    - AI output: "Review and approve AI-generated report before sending"

    After calling this, use get_approval_status with the returned ID
    to check if the approver has made a decision.

    Args:
        title: Short description of what needs approval.
            Example: "Approve Q1 expense report — $12,500"
        approver_email: Email of the person who should approve.
        description: Optional detailed description for the approver.
        category: Type of approval: "financial", "hr", "operations", "general".
        context_data: Optional dict of data the approver should see.
            Example: {"vendor": "Acme", "amount": 50000, "invoice_id": "INV-001"}
        urgency: Priority level: "low", "normal", "high", "critical".
            Critical sends an urgent email notification.
        expires_in_hours: Optional auto-expiry. After this many hours,
            the request is automatically marked as expired.

    Returns:
        JSON with the approval request ID and status ("pending").
        Use the ID with get_approval_status to poll for the decision.
    """
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    client = _get_client()

    try:
        result = await client.request_approval(
            title=title,
            approver_email=approver_email,
            description=description,
            category=category,
            requested_by="Claude",
            context_data=context_data or {},
            urgency=urgency,
            expires_in_hours=expires_in_hours,
        )
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.error(f"request_approval failed: {e}")
        return json.dumps({"error": str(e)})


# ─── Tool 7: Get Approval Status ────────────────────────────────


@mcp.tool()
async def get_approval_status(
    request_id: str,
) -> str:
    """Check the status of a previously submitted approval request.

    Returns the current status: "pending", "approved", "rejected", or "expired".
    If decided, includes who decided and their comments.

    Args:
        request_id: The approval request ID returned by request_approval.

    Returns:
        JSON with status, decision details (if decided), and timestamps.
    """
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    client = _get_client()

    try:
        result = await client.get_approval_status(request_id)
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.error(f"get_approval_status failed: {e}")
        return json.dumps({"error": str(e)})


# ─── Tool 8: Get Execution Result ───────────────────────────────────


@mcp.tool()
async def get_execution_result(
    execution_id: str,
) -> str:
    """Get the result and audit trail of a workflow execution.

    Returns detailed execution results including status, extracted data,
    step-by-step execution log, any errors, and output file references.

    Args:
        execution_id: The execution ID returned by run_workflow.

    Returns:
        Execution result with success status, context variables,
        execution log entries, timing, and any output files.
    """
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    client = _get_client()

    try:
        result = await client.get_execution_status(execution_id)
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.error(f"get_execution_result failed: {e}")
        return json.dumps({"error": str(e)})


# ─── Tool 9: List Kuris ───────────────────────────────────────────────


@mcp.tool()
async def list_kuris() -> str:
    """List available Kuriflow kuris — pre-built specialist agents.

    Kuris are ready-to-use specialists for specific domains. Each kuri
    has a kuri_type you can pass to save_workflow to associate the workflow
    with that kuri.

    Use this to discover which kuri best fits the user's task before
    creating a workflow with save_workflow.

    For save_workflow, use one of these kuri_types:
    - financial_analysis_v2_kuri: script fetches data from APIs (zero input files)
    - spreadsheet_mcp_kuri: script transforms input files into spreadsheets
    - word_mcp_kuri: script generates Word docs (.docx) or PDFs from input data
    - data_analytics_mcp_kuri: script generates charts (.png/.jpg/.svg) or HTML dashboards from tabular data

    Returns:
        JSON list of kuris with kuri_type, name, description, and department.
    """
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    client = _get_client()

    try:
        result = await client.list_kuris()
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.error(f"list_kuris failed: {e}")
        return json.dumps({"error": str(e)})


# ─── Entry Point ──────────────────────────────────────────────────────


def main():
    """Run the Kuriflow MCP server."""
    transport = os.environ.get("KURIFLOW_MCP_TRANSPORT", "stdio")

    # Railway/Render/Fly assign PORT dynamically — bridge to FastMCP's env var
    if transport != "stdio":
        port = os.environ.get("PORT")
        if port and not os.environ.get("FASTMCP_PORT"):
            os.environ["FASTMCP_PORT"] = port
        if not os.environ.get("FASTMCP_HOST"):
            os.environ["FASTMCP_HOST"] = "0.0.0.0"

    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
