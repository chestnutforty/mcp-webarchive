# MCP Server: mcp-webarchive

## Overview

This MCP provides access to the Internet Archive Wayback Machine, allowing retrieval of historical webpage snapshots. It's useful for forecasting tasks that need to verify past website content, track organizational changes, or access historical data that may no longer be available on the live web.

## Key Files

- `server.py` - Main MCP server with tool definitions
- `rate_limits.json` - Rate limiting configuration
- `pyproject.toml` - Dependencies

## Running the Server

```bash
uv sync
uv run fastmcp run server.py
```

## Available Tools

- **get_archived_snapshot** - Fetch archived webpage content at a specific date (supports backtesting)
- **list_available_snapshots** - List available archive dates for a URL

## Rate Limiting

Rate limits are configured in `rate_limits.json`:

```json
{
  "max_requests_per_second": 5,
  "hard_limit": null,
  "max_wait_seconds": 120,
  "tools": {}
}
```

## Backtesting

The `get_archived_snapshot` tool supports backtesting with the `cutoff_date` parameter:
- Hidden from the LLM (via `exclude_args`)
- Defaults to today's date
- Ensures snapshots are from before the cutoff date
- Automatically caps target_date at cutoff_date

## Example Usage

For a forecasting question like "Will the Nonlinear Fund have more than eight FTE employees on January 1, 2026?":

1. First, list available snapshots to see archive coverage:
   ```
   list_available_snapshots(url="nonlinearfund.org/team", start_date="2025-01-01", end_date="2025-12-31")
   ```

2. Then fetch the content from a relevant date:
   ```
   get_archived_snapshot(url="nonlinearfund.org/team", target_date="2025-12-01")
   ```

## API Information

- No API key required
- Uses Wayback Machine Availability API and CDX API
- HTML content is converted to readable text using html2text
- Output is truncated at 50,000 characters to avoid context overflow
