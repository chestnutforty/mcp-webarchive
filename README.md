# MCP Web Archive

MCP server providing access to the Internet Archive Wayback Machine for retrieving historical webpage snapshots.

## Structure

```
mcp-webarchive/
├── pyproject.toml          # Dependencies
├── server.py               # Server with tool definitions
├── app.py                  # FastAPI HTTP transport
├── rate_limiter.py         # Rate limiting utilities
├── rate_limits.json        # Rate limiting configuration
├── Dockerfile              # Container configuration
├── CLAUDE.md               # Documentation for AI agents
├── tests/                  # Test suite
└── README.md
```

## Quick Start

```bash
uv sync
uv run fastmcp run server.py
```

## Available Tools

### get_archived_snapshot (Backtesting Supported)

Fetch the content of a webpage from the Wayback Machine at or before a specified date.

**Parameters:**
- `url` (required): The URL to fetch from the archive
- `target_date` (required): Target date in YYYY-MM-DD format

**Example:**
```
get_archived_snapshot(url="example.com/team", target_date="2024-06-01")
```

### list_available_snapshots

List available snapshots for a URL within a date range.

**Parameters:**
- `url` (required): The URL to check for snapshots
- `start_date` (optional): Start of date range in YYYY-MM-DD format
- `end_date` (optional): End of date range in YYYY-MM-DD format
- `limit` (optional): Maximum number of snapshots to return (default 10, max 50)

**Example:**
```
list_available_snapshots(url="example.com", start_date="2024-01-01", end_date="2024-12-31")
```

## Use Cases

- Retrieve historical content for forecasting questions
- Verify past information about organizations
- Track website changes over time
- Access content that may have been removed or changed

## Testing

```bash
uv sync --extra test && uv run pytest
```

## Rate Limiting

Configure rate limits in `rate_limits.json`:

```json
{
  "max_requests_per_second": 5,
  "hard_limit": null,
  "max_wait_seconds": 120,
  "tools": {}
}
```

The Internet Archive has no strict rate limits but requests should be reasonable.

## Backtesting

The `get_archived_snapshot` tool supports backtesting with a hidden `cutoff_date` parameter. When backtesting, the tool ensures that:
- Snapshots are only retrieved from before the cutoff date
- The target date is automatically capped at the cutoff date if it exceeds it

## API Details

This MCP uses the following Wayback Machine APIs:
- **Availability API**: `https://archive.org/wayback/available` - Find closest snapshot
- **CDX API**: `https://web.archive.org/cdx/search/cdx` - List available snapshots

No API key required.
