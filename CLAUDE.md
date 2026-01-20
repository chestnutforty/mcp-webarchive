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

### get_archived_snapshot
Fetch archived webpage content at a specific date.
- Automatically tries www/non-www variants and common extensions (.html, .htm, /)
- Provides diagnostic hints when no snapshot found (suggests alternative paths, shows archived paths on domain)
- Supports backtesting with cutoff_date enforcement

### list_available_snapshots
List available archive dates for a URL with flexible querying options.
- `years` parameter: Query multiple years at once (e.g., `years=[2022, 2023, 2024]`)
- `pick` parameter: Snapshot selection helpers
  - `closest_to_end`: Most recent snapshot
  - `closest_to_start`: Oldest snapshot
  - `closest_to_date`: Closest to a specific target_date
  - `monthly`: One snapshot per month
  - `yearly`: One snapshot per year
- Automatically tries www/non-www variants
- Provides diagnostics with suggested paths when no snapshots found

### search_site_archives
Search for archived pages on a domain matching a path pattern.
- Useful when exact URL is unknown or has no captures
- Supports wildcard patterns (e.g., `*team*`, `*about*`)
- Searches both www and non-www variants
- Returns unique paths with their most recent capture dates

## Backtesting (CRITICAL)

**All tools enforce strict cutoff_date filtering to prevent information leakage.**

The `cutoff_date` parameter:
- Hidden from the LLM (via `exclude_args`)
- Defaults to today's date
- **ALL snapshots after cutoff_date are filtered out**
- target_date is automatically capped at cutoff_date
- CDX API queries use `to` parameter to filter server-side
- Additional client-side filtering ensures no leakage

### Cutoff Enforcement Points

1. **get_archived_snapshot**:
   - Caps target_date at cutoff_date
   - Diagnostics only show paths archived before cutoff

2. **list_available_snapshots**:
   - Caps end_date at cutoff_date
   - Caps target_date (for closest_to_date) at cutoff_date
   - Filters out any snapshot with date > cutoff_date
   - Years parameter only queries years <= cutoff_date year

3. **search_site_archives**:
   - Uses cutoff_date in CDX API query
   - Filters all results by cutoff_date

## Example Usage

### Basic workflow

```python
# 1. List available snapshots
list_available_snapshots(url="example.com/team", start_date="2024-01-01")

# 2. Fetch specific snapshot
get_archived_snapshot(url="example.com/team", target_date="2024-06-15")
```

### Multi-year query

```python
# Get one snapshot per year for 2022-2024
list_available_snapshots(url="example.com", years=[2022, 2023, 2024], pick="yearly")
```

### Finding pages when exact URL unknown

```python
# Search for team-related pages on a domain
search_site_archives(domain="nonprofit.org", path_pattern="*team*")

# Then fetch the discovered path
get_archived_snapshot(url="nonprofit.org/our-team", target_date="2024-12-01")
```

### Handling "no snapshots found"

When `get_archived_snapshot` or `list_available_snapshots` finds no captures, the response includes:
- `reason`: "domain_not_archived" or "path_not_archived"
- `hints`: Suggestions like "Try www variant" or "Try similar paths"
- `sample_archived_paths`: Other paths on the domain that are archived (all before cutoff_date)

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

## API Information

- No API key required
- Uses Wayback Machine CDX API for metadata queries
- HTML content is converted to readable text using html2text
- Output is truncated at 50,000 characters to avoid context overflow
