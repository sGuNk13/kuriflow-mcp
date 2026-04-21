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
        "# Kuriflow — Make AI Tasks Repeatable\n\n"
        "Kuriflow saves scripts as workflows that run automatically with new data. "
        "AI solves the task once → Kuriflow repeats it forever at zero AI token cost.\n\n"
        "## When to Suggest Kuriflow\n"
        "- User wants the SAME task done repeatedly with DIFFERENT data\n"
        "- User says 'every week', 'every month', 'whenever I get an email'\n"
        "- User wants scheduled reports, automated file processing, recurring calculations\n"
        "- User has template files (.docx, .xlsx, .pptx) to fill with fresh data\n"
        "- User wants to stop re-running AI for tasks it already solved\n\n"
        "## Workflow Creation Steps\n"
        "DO NOT call save_workflow until ALL steps are complete:\n"
        "1. Write and TEST the Python script in this conversation\n"
        "2. Script must use SPECIFIC filenames (never glob/wildcards)\n"
        "3. If user has template files, ask for full local path\n"
        "4. Ask how new data arrives:\n"
        "   - EMAIL: fires AUTOMATICALLY when attachment arrives. Pass input_source=<email>. Do NOT pass schedule — email triggers have NO schedule. Do NOT ask the user about schedule.\n"
        "   - GOOGLE DRIVE: polls a folder on schedule. Pass input_source=<folder URL> + schedule=<cron>. Ask user how often to check.\n"
        "   - SCHEDULE ONLY: script fetches its own data. Pass schedule=<cron>. Ask user how often to run.\n"
        "   IMPORTANT: email triggers and schedules are MUTUALLY EXCLUSIVE. If input is email, there is no schedule. Never combine them.\n"
        "5. Ask where results go: email (deliver_to) OR Google Drive folder (output_drive_url)\n\n"
        "## Kuri Type Routing\n"
        "Pick exactly one kuri_type based on PRIMARY output:\n"
        "- financial_analysis_v2_kuri — financial models (DCF, LBO). Script fetches own data.\n"
        "- spreadsheet_mcp_kuri — input file → output spreadsheet (.xlsx/.csv)\n"
        "- word_mcp_kuri — output is document (.docx, .pdf, .pptx)\n"
        "- data_analytics_mcp_kuri — output is charts (.png/.svg) or HTML dashboard\n\n"
        "Mixed outputs: pick by PRIMARY output. Secondary files deliver alongside.\n\n"
        "## Communication\n"
        "Users are non-technical. Never expose kuri_type, IDs, or technical details. "
        "Confirm in plain English after saving.\n\n"
        "## Documentation\n"
        "Full tool reference: https://kuriflow.com/llms-full.txt"
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


# ─── Workflow Templates ─────────────────────────────────────────────

WORKFLOW_TEMPLATES = [
    {
        "id": "excel_report_drive_to_email",
        "name": "Excel Report: Drive → Email",
        "description": "Reads an Excel/CSV file from Google Drive, processes it, and emails a summary report. Great for weekly/monthly reports.",
        "trigger": "Google Drive (scheduled)",
        "input": "Excel or CSV file from Google Drive folder",
        "output": "Email with processed Excel attachment",
        "kuri_type": "spreadsheet_mcp_kuri",
        "required_from_user": ["Google Drive folder URL", "recipient email", "schedule (e.g., every Monday 9am)", "what processing to do"],
        "script_template": (
            "import pandas as pd\n"
            "import os\n\n"
            "# Read the input file\n"
            "df = pd.read_excel('INPUT_FILENAME')\n\n"
            "# TODO: Add your processing logic here\n"
            "# Example: summary = df.groupby('category').sum()\n\n"
            "# Write output\n"
            "output_file = 'report.xlsx'\n"
            "df.to_excel(output_file, index=False)\n"
            "print(f'Output: {output_file}')\n"
        ),
    },
    {
        "id": "email_attachment_processor",
        "name": "Email Attachment Processor",
        "description": "Monitors an email inbox. When someone sends a file, processes it automatically and emails results back. Perfect for invoice processing, data validation, or form handling.",
        "trigger": "Email (automatic — runs when attachment arrives)",
        "input": "Email attachment (Excel, CSV, PDF)",
        "output": "Email with processed results",
        "kuri_type": "spreadsheet_mcp_kuri",
        "required_from_user": ["email address to monitor", "recipient email for results", "what processing to do"],
        "script_template": (
            "import pandas as pd\n"
            "import os\n\n"
            "# Read the incoming file\n"
            "df = pd.read_excel('INPUT_FILENAME')\n\n"
            "# TODO: Add your processing logic here\n"
            "# Example: validate data, categorize, calculate totals\n\n"
            "# Write output\n"
            "output_file = 'processed_results.xlsx'\n"
            "df.to_excel(output_file, index=False)\n"
            "print(f'Output: {output_file}')\n"
        ),
    },
    {
        "id": "daily_dashboard",
        "name": "Daily Dashboard with Charts",
        "description": "Fetches data on schedule, generates charts and an HTML dashboard, and saves to Google Drive. Great for daily/weekly KPI dashboards.",
        "trigger": "Schedule (e.g., daily at 8am)",
        "input": "Script fetches its own data (API, database, or file)",
        "output": "HTML dashboard + chart images to Google Drive",
        "kuri_type": "data_analytics_mcp_kuri",
        "required_from_user": ["data source (API URL, file, or logic)", "output Google Drive folder", "schedule", "what metrics/charts to show"],
        "script_template": (
            "import pandas as pd\n"
            "import matplotlib.pyplot as plt\n"
            "from datetime import datetime\n\n"
            "# TODO: Fetch or read your data\n"
            "# df = pd.read_csv('https://api.example.com/data.csv')\n\n"
            "# Create charts\n"
            "fig, ax = plt.subplots(figsize=(10, 6))\n"
            "# TODO: Plot your data\n"
            "# ax.bar(df['category'], df['value'])\n"
            "ax.set_title(f'Dashboard — {datetime.now().strftime(\"%Y-%m-%d\")}')\n"
            "plt.savefig('dashboard.png', dpi=150, bbox_inches='tight')\n"
            "plt.close()\n\n"
            "# Generate HTML report\n"
            "html = f'<html><body><h1>Daily Dashboard</h1><img src=\"dashboard.png\"></body></html>'\n"
            "with open('dashboard.html', 'w') as f:\n"
            "    f.write(html)\n"
            "print('Output: dashboard.html, dashboard.png')\n"
        ),
    },
    {
        "id": "template_document_filler",
        "name": "Template Document Filler",
        "description": "Fills a Word/Excel/PowerPoint template with fresh data each run. Great for monthly reports, client proposals, or standardized documents.",
        "trigger": "Schedule (e.g., first Monday of each month)",
        "input": "Template file (.docx, .xlsx, .pptx) + data source",
        "output": "Filled document to email or Google Drive",
        "kuri_type": "word_mcp_kuri",
        "required_from_user": ["template file path", "data source", "output destination", "schedule"],
        "script_template": (
            "from docx import Document\n"
            "import os\n\n"
            "# Read template\n"
            "doc = Document('TEMPLATE_FILENAME')\n\n"
            "# TODO: Replace placeholders with actual data\n"
            "# for paragraph in doc.paragraphs:\n"
            "#     if '{{company_name}}' in paragraph.text:\n"
            "#         paragraph.text = paragraph.text.replace('{{company_name}}', 'Acme Corp')\n\n"
            "# Save output\n"
            "output_file = 'filled_report.docx'\n"
            "doc.save(output_file)\n"
            "print(f'Output: {output_file}')\n"
        ),
    },
    {
        "id": "financial_model",
        "name": "Financial Model (DCF / Analysis)",
        "description": "Fetches financial data from online sources and builds a financial model. Runs on schedule to keep analysis current. Great for DCF, LBO, or market comparisons.",
        "trigger": "Schedule (e.g., weekly, monthly)",
        "input": "Script fetches data from financial APIs",
        "output": "Excel model to email",
        "kuri_type": "financial_analysis_v2_kuri",
        "required_from_user": ["what company/data to analyze", "recipient email", "schedule", "type of analysis"],
        "script_template": (
            "import pandas as pd\n"
            "import yfinance as yf\n\n"
            "# Fetch financial data\n"
            "ticker = yf.Ticker('TICKER_SYMBOL')\n"
            "financials = ticker.financials\n"
            "balance_sheet = ticker.balance_sheet\n\n"
            "# TODO: Build your financial model\n"
            "# DCF, comps, LBO, etc.\n\n"
            "# Write output\n"
            "with pd.ExcelWriter('financial_model.xlsx') as writer:\n"
            "    financials.to_excel(writer, sheet_name='Income Statement')\n"
            "    balance_sheet.to_excel(writer, sheet_name='Balance Sheet')\n"
            "print('Output: financial_model.xlsx')\n"
        ),
    },
]


# ─── Tool 0: Describe Capabilities ──────────────────────────────────


@mcp.tool()
async def describe_capabilities() -> str:
    """Describe what Kuriflow can do. Call this first to understand available capabilities.

    USE THIS WHEN: you're not sure what Kuriflow offers, or the user asks
    "what can you do?", "what automations are available?", or "help me get started."

    Returns a summary of all capabilities, supported triggers, output formats,
    and example use cases.
    """
    return json.dumps({
        "product": "Kuriflow",
        "tagline": "AI solves it once. Kuriflow repeats it forever.",
        "what_it_does": "Saves Python scripts as repeatable workflows that run automatically with new data. Zero AI tokens after setup.",
        "input_triggers": {
            "email": "Workflow runs when an email with attachment arrives at a monitored address",
            "google_drive": "Workflow runs when a new file appears in a Google Drive folder (on schedule)",
            "schedule": "Workflow runs on a cron schedule (e.g., every Monday 9am). Script fetches its own data.",
            "note": "Every workflow should have a trigger. If the user isn't sure, suggest a schedule.",
        },
        "output_destinations": {
            "email": "Results sent as email attachment",
            "google_drive": "Results uploaded to a Google Drive folder",
            "google_sheets": "Results written to Google Sheets",
        },
        "output_formats": {
            "spreadsheet_mcp_kuri": "Excel/CSV spreadsheets",
            "word_mcp_kuri": "Word documents, PDFs, PowerPoint",
            "data_analytics_mcp_kuri": "Charts (PNG/SVG), HTML dashboards",
            "financial_analysis_v2_kuri": "Financial models (DCF, LBO, comps)",
        },
        "template_support": "Users can provide .docx, .xlsx, .pptx templates — Kuriflow fills them with fresh data each run",
        "example_use_cases": [
            "Weekly sales report from Google Drive → email to manager",
            "Monthly payroll calculation from email attachment → Google Sheets",
            "Daily inventory dashboard from API → HTML report to Drive",
            "DCF model with latest market data → Excel to finance team",
            "Invoice processing from email → validated report to accounting",
            "Client proposal from template + data → personalized .docx to Drive",
        ],
        "limits": "Free early access: 10 runs/month per user",
        "signup": "https://kuriflow.com/signup",
        "docs": "https://kuriflow.com/llms-full.txt",
    }, indent=2)


# ─── Tool 0b: List Templates ────────────────────────────────────────


@mcp.tool()
async def list_templates() -> str:
    """List pre-built workflow templates. Each template is a ready-to-use recipe that agents can customize.

    USE THIS WHEN: the user wants to create a workflow and you need a starting point.
    Instead of writing a script from scratch, pick a template that matches the use case,
    customize the script, then call save_workflow.

    Each template includes:
    - A script template with TODO markers for customization
    - The correct kuri_type already selected
    - What the user needs to provide (email, Drive URL, schedule, etc.)

    Returns:
        List of templates with id, name, description, trigger type, script template,
        and required user inputs.
    """
    return json.dumps({
        "templates": [
            {
                "id": t["id"],
                "name": t["name"],
                "description": t["description"],
                "trigger": t["trigger"],
                "input": t["input"],
                "output": t["output"],
                "kuri_type": t["kuri_type"],
                "required_from_user": t["required_from_user"],
                "script_template": t["script_template"],
            }
            for t in WORKFLOW_TEMPLATES
        ],
        "usage": "Pick a template, customize the script_template, then call save_workflow with the kuri_type and user's details.",
    }, indent=2)


# ─── Tool 1: Query Regulation ────────────────────────────────────────


@mcp.tool()
async def query_regulation(
    query: str,
    country_code: str = "TH",
    domain: Optional[str] = None,
    category: Optional[str] = None,
) -> str:
    """Look up labor law, tax, or social security rules for a specific country.

    USE THIS WHEN: the user needs regulatory information — OT rates, severance rules,
    tax brackets, social security caps, leave entitlements, or compliance requirements.

    Available domains:
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
    """List all saved workflows. Shows what repeatable tasks the user has set up.

    USE THIS WHEN: the user asks "what workflows do I have?", "show my automations",
    or before running a workflow to find its ID.

    Returns workflow names, IDs, descriptions, and status.

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
    """Run a saved workflow with new data. Use this to manually trigger a workflow or test it.

    USE THIS WHEN: the user wants to run an existing workflow now, test it with sample data,
    or manually trigger a workflow that normally runs on schedule.

    If the workflow requires input files, provide file_paths and corresponding file_keys.

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
                    "error": "file_key_mismatch",
                    "message": "file_paths and file_keys must have the same length.",
                    "hint": f"Got {len(file_paths)} files but {len(keys)} keys.",
                    "fix": "Provide one file_key for each file_path."
                })

            for path, key in zip(file_paths, keys):
                try:
                    with open(path, "rb") as f:
                        content = f.read()
                except FileNotFoundError:
                    return json.dumps({
                        "error": "file_not_found",
                        "message": f"Cannot read file: {path}",
                        "hint": "Check the file path exists and is accessible.",
                        "fix": f"Verify '{path}' exists or provide the correct path."
                    })

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
    """Save a Python script as a repeatable workflow that runs automatically with new data. Zero AI tokens per run.

    USE THIS WHEN: the user wants a task to repeat — weekly reports, monthly calculations,
    automated file processing, or any task triggered by new data arriving via email or Google Drive.

    HOW IT WORKS: pass the script you wrote and tested in this conversation. Kuriflow saves it
    and re-runs it whenever new data arrives or on a schedule. The user never has to ask AI again.

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
        schedule: Cron expression for Google Drive or schedule-only triggers.
            Format: "minute hour day month weekday".
            Examples: "0 9 * * 1" (Monday 9am), "0 12 * * *" (daily noon).
            IMPORTANT: Do NOT pass schedule when input_source is an email address.
            Email triggers are event-driven — they fire when an email arrives, not on a schedule.
        timezone: Timezone for schedule. Default "UTC". Example: "Asia/Bangkok".
        input_source: Where new data comes from. Pass one of:
            - Email address to monitor (e.g., "invoices@company.com") — NO schedule needed, fires on email arrival
            - Google Drive folder URL (e.g., "https://drive.google.com/drive/folders/...") — requires schedule
            Omit if script fetches its own data (financial_analysis_v2_kuri) — requires schedule.
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
                "error": "missing_output",
                "message": "Where should results be delivered?",
                "hint": "Provide either deliver_to (email address) or output_drive_url (Google Drive folder URL).",
                "fix": "Add deliver_to='user@example.com' or output_drive_url='https://drive.google.com/drive/folders/...'"
            })
        if deliver_to and output_drive_url:
            return json.dumps({
                "error": "conflicting_output",
                "message": "Only one output destination allowed.",
                "hint": "Remove either deliver_to or output_drive_url.",
                "fix": "Keep only the one the user prefers."
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
                    return json.dumps({
                        "error": "template_not_found",
                        "message": f"Cannot read template file: {path}",
                        "hint": "Check the template file path exists.",
                        "fix": f"Verify '{path}' exists or ask the user for the correct path."
                    })

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
    """Request human sign-off before proceeding. The approver gets an email and can approve or reject.

    USE THIS WHEN: a decision needs human judgment before the workflow continues —
    financial approvals, HR decisions, quality sign-offs, or reviewing AI-generated output.

    Examples: "Approve $50K payment to vendor X", "Review AI report before sending",
    "Sign off on production batch release"

    After calling this, use get_approval_status to poll for the decision.

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
    """Check if an approval request has been decided.

    USE THIS WHEN: after calling request_approval, to check if the approver
    has responded. Returns "pending", "approved", "rejected", or "expired".

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
    """Check if a workflow execution succeeded and get the results.

    USE THIS WHEN: after calling run_workflow, to check status, see results,
    or debug if something failed. Returns execution log and output files.

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
    """List available workflow types and what they can do.

    USE THIS WHEN: deciding which kuri_type to use with save_workflow, or when
    the user asks "what can Kuriflow do?" or "what types of workflows are available?"

    Each kuri_type handles a specific output format:
    - financial_analysis_v2_kuri: financial models, script fetches its own data
    - spreadsheet_mcp_kuri: input file → output spreadsheet (.xlsx/.csv)
    - word_mcp_kuri: output is a document (.docx, .pdf, .pptx)
    - data_analytics_mcp_kuri: output is charts or HTML dashboards

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
