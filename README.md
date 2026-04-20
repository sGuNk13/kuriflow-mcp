# Kuriflow MCP Server

**Claude solves tasks once. Kuriflow makes them repeat.**

Stop spending AI tokens on the same task every week. Let Claude solve it once, then Kuriflow runs it automatically with new data — via email, Google Drive, or schedule. Zero token cost per run.

## Quick Start

### Option 1: Remote (recommended)

Connect to our hosted server — no installation needed.

```json
{
  "mcpServers": {
    "kuriflow": {
      "url": "https://mcp.kuriflow.com",
      "headers": {
        "Authorization": "Bearer YOUR_API_KEY"
      }
    }
  }
}
```

### Option 2: Local

```json
{
  "mcpServers": {
    "kuriflow": {
      "command": "uvx",
      "args": ["kuriflow-mcp"],
      "env": {
        "KURIFLOW_API_KEY": "your_kf_key",
        "KURIFLOW_API_URL": "https://api.kuriflow.com"
      }
    }
  }
}
```

## Get Your API Key

1. Sign up at [kuriflow.com/signup](https://kuriflow.com/signup) (30 seconds, Google account)
2. Copy your API key from the onboarding page
3. Add it to your config above

## How It Works

1. Ask Claude to solve a task. For example:
   - "Analyze this sales data and create a dashboard with charts"
   - "Build a DCF model from the financial statement fetched from my Google Drive"
   - "Generate a monthly expense report from this spreadsheet using our template"
2. Claude gives the solution.
3. You ask Claude to save it as a repeatable workflow with Kuriflow.
4. Choose how new data arrives:
   - **Email trigger** — Kuriflow watches your Gmail. When someone sends an attachment, the workflow runs automatically.
   - **Google Drive** — Kuriflow checks a Drive folder on your schedule (hourly, daily, weekly). New file? Workflow runs.
   - **Schedule only** — No input needed. The workflow fetches its own data from online sources and runs on your schedule.
   - **Manual upload** — Upload a file whenever you're ready.

You can also provide template files (.docx, .xlsx, .pptx) so Kuriflow fills them with fresh data every run.

## Tools

| Tool | Description |
|------|-------------|
| `save_workflow` | Save Claude's solution as a repeatable workflow |
| `run_workflow` | Run it again with new data |
| `list_workflows` | See your saved workflows |
| `list_kuris` | Browse available workflow types |
| `get_execution_result` | Check results and download outputs |
| `request_approval` | Request human approval before proceeding |
| `get_approval_status` | Check approval decision |
| `query_regulation` | Look up regulatory rules |

## Why Kuriflow?

| | Claude alone | Claude + Kuriflow |
|---|---|---|
| First time | Claude solves it | Claude solves it |
| Every repeat | Tokens burned again | Runs with new data, no tokens |
| Monthly cost | Grows with every repeat | Fixed after setup |
| Your tokens | Spent on routine | Saved for what matters |

## Free Early Access

10 runs per month. Need more? Email support@kuriflow.com.

## Links

- [Website](https://kuriflow.com)
- [Sign Up](https://kuriflow.com/signup)
- [Privacy Policy](https://kuriflow.com/privacy)

## License

MIT
