"""
HTTP client for Kuriflow Backend API.

Thin wrapper around httpx that handles auth and base URL.
The MCP server calls this to interact with the Kuriflow backend.
"""

import logging
import os
from typing import Any, Dict, List, Optional
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)

# Default timeout for API calls (seconds)
DEFAULT_TIMEOUT = 60.0
# Longer timeout for workflow execution
EXECUTION_TIMEOUT = 300.0


class KuriflowClient:
    """HTTP client for Kuriflow backend API."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.base_url = (base_url or os.environ.get("KURIFLOW_API_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("KURIFLOW_API_KEY", "")

        if not self.base_url:
            raise ValueError(
                "KURIFLOW_API_URL must be set. "
                "Set via environment variable or pass base_url parameter."
            )
        if not self.api_key:
            raise ValueError(
                "KURIFLOW_API_KEY must be set. "
                "Set via environment variable or pass api_key parameter."
            )

        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _client(self, timeout: float = DEFAULT_TIMEOUT) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers,
            timeout=timeout,
        )

    # ─── Regulations ──────────────────────────────────────────────────

    async def query_regulation(
        self,
        country_code: str,
        query: str,
        domain: Optional[str] = None,
        category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Query regulatory knowledge base."""
        params: Dict[str, str] = {
            "country_code": country_code,
            "query": query,
        }
        if domain:
            params["domain"] = domain
        if category:
            params["category"] = category

        async with self._client() as client:
            resp = await client.get("/api/v1/regulations/query", params=params)
            resp.raise_for_status()
            return resp.json()

    async def list_regulation_packs(
        self,
        country_code: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List available regulation packs."""
        params = {}
        if country_code:
            params["country_code"] = country_code

        async with self._client() as client:
            resp = await client.get("/api/v1/regulations/packs", params=params)
            resp.raise_for_status()
            return resp.json()

    # ─── Workflows ────────────────────────────────────────────────────

    async def list_workflows(self) -> List[Dict[str, Any]]:
        """List all workflows for the authenticated org."""
        async with self._client() as client:
            resp = await client.get("/api/v1/workflows")
            resp.raise_for_status()
            return resp.json()

    async def list_kuris(self) -> List[Dict[str, Any]]:
        """List available kuris from the kuri catalog."""
        async with self._client() as client:
            resp = await client.get("/api/v1/kuri/catalog")
            resp.raise_for_status()
            return resp.json()

    async def get_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Get a workflow by ID."""
        async with self._client() as client:
            resp = await client.get(f"/api/v1/workflows/{workflow_id}")
            resp.raise_for_status()
            return resp.json()

    async def run_workflow(
        self,
        workflow_id: str,
        session_id: Optional[str] = None,
        initial_context: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute a saved workflow."""
        sid = session_id or str(uuid4())

        payload: Dict[str, Any] = {
            "workflow_id": workflow_id,
            "session_id": sid,
            "initial_context": initial_context or {},
        }
        if model:
            payload["model"] = model

        async with self._client(timeout=EXECUTION_TIMEOUT) as client:
            resp = await client.post(
                "/api/v1/execute/run",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    async def upload_file(
        self,
        session_id: str,
        file_key: str,
        file_content: bytes,
        filename: str,
    ) -> Dict[str, Any]:
        """Upload a file for workflow execution."""
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=DEFAULT_TIMEOUT,
        ) as client:
            resp = await client.post(
                "/api/v1/execute/upload",
                files={"file": (filename, file_content)},
                data={"session_id": session_id, "file_key": file_key},
            )
            resp.raise_for_status()
            return resp.json()

    # ─── Compose & Save ──────────────────────────────────────────────

    async def save_workflow(
        self,
        name: str,
        steps: List[Dict[str, Any]],
        description: Optional[str] = None,
        trigger_type: str = "manual",
        schedule: Optional[str] = None,
        timezone: str = "UTC",
        plugins: Optional[List[Dict[str, Any]]] = None,
        input_method: Optional[str] = None,
        input_config: Optional[Dict[str, Any]] = None,
        output_method: Optional[str] = None,
        output_config: Optional[Dict[str, Any]] = None,
        kuri_type: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a workflow from simplified steps via compose-and-save endpoint."""
        payload: Dict[str, Any] = {
            "name": name,
            "steps": steps,
            "trigger_type": trigger_type,
        }
        if description:
            payload["description"] = description
        if schedule:
            payload["schedule"] = schedule
        if timezone != "UTC":
            payload["timezone"] = timezone
        if plugins:
            payload["plugins"] = plugins
        if input_method:
            payload["input_method"] = input_method
        if input_config:
            payload["input_config"] = input_config
        if output_method:
            payload["output_method"] = output_method
        if output_config:
            payload["output_config"] = output_config
        if kuri_type:
            payload["kuri_type"] = kuri_type
        if model:
            payload["model"] = model

        async with self._client() as client:
            resp = await client.post("/api/v1/workflows/compose-and-save", json=payload)
            resp.raise_for_status()
            return resp.json()

    # ─── Approval Requests ──────────────────────────────────────────

    async def request_approval(
        self,
        title: str,
        approver_email: str,
        description: Optional[str] = None,
        category: str = "general",
        requested_by: str = "AI Agent",
        context_data: Optional[Dict[str, Any]] = None,
        urgency: str = "normal",
        expires_in_hours: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create a standalone approval request."""
        payload: Dict[str, Any] = {
            "title": title,
            "approver_email": approver_email,
            "category": category,
            "requested_by": requested_by,
            "urgency": urgency,
        }
        if description:
            payload["description"] = description
        if context_data:
            payload["context_data"] = context_data
        if expires_in_hours:
            payload["expires_in_hours"] = expires_in_hours

        async with self._client() as client:
            resp = await client.post("/api/v1/approval-requests", json=payload)
            resp.raise_for_status()
            return resp.json()

    async def get_approval_status(
        self,
        request_id: str,
    ) -> Dict[str, Any]:
        """Get the status of an approval request."""
        async with self._client() as client:
            resp = await client.get(f"/api/v1/approval-requests/{request_id}")
            resp.raise_for_status()
            return resp.json()

    # ─── Execution Results ────────────────────────────────────────────

    async def get_execution_status(self, execution_id: str) -> Dict[str, Any]:
        """Get execution status and results."""
        async with self._client() as client:
            resp = await client.get(f"/api/v1/execute/{execution_id}/status")
            resp.raise_for_status()
            return resp.json()

    async def get_workflow_executions(
        self,
        workflow_id: str,
    ) -> List[Dict[str, Any]]:
        """Get execution history for a workflow."""
        async with self._client() as client:
            resp = await client.get(f"/api/v1/workflows/{workflow_id}/executions")
            resp.raise_for_status()
            return resp.json()
