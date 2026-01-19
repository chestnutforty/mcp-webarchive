import json
import os
import traceback
from datetime import datetime
from functools import wraps
from typing import Annotated

from dotenv import load_dotenv

load_dotenv()

import html2text
import httpx
from fastmcp import FastMCP

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
MCP_NAME = "webarchive"

WAYBACK_AVAILABILITY_API = "https://archive.org/wayback/available"
WAYBACK_CDX_API = "https://web.archive.org/cdx/search/cdx"
WAYBACK_BASE_URL = "https://web.archive.org/web"


def send_slack_error(tool_name: str, error: Exception, args: tuple, kwargs: dict) -> None:
    try:
        error_message = {
            "text": f"MCP Tool Error in `{MCP_NAME}`",
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": "MCP Tool Error", "emoji": True}},
                {"type": "section", "fields": [
                    {"type": "mrkdwn", "text": f"*MCP Server:*\n{MCP_NAME}"},
                    {"type": "mrkdwn", "text": f"*Tool:*\n{tool_name}"},
                ]},
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*Error:*\n```{str(error)[:500]}```"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*Traceback:*\n```{traceback.format_exc()[:1000]}```"}},
            ],
        }
        httpx.post(SLACK_WEBHOOK_URL, json=error_message, timeout=5)
    except Exception:
        pass


def notify_on_error(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            send_slack_error(func.__name__, e, args, kwargs)
            raise
    return wrapper


mcp = FastMCP(
    name="webarchive",
    instructions="""Access historical webpage snapshots from the Internet Archive Wayback Machine.
This datasource provides archived versions of websites captured over time, allowing you to see how
webpages looked at specific points in the past. Useful for retrieving historical content, verifying
past information, and tracking changes to websites over time.""".strip(),
)


async def find_closest_snapshot(url: str, target_date: str) -> dict | None:
    """Query the Wayback Machine availability API to find the closest snapshot."""
    timestamp = target_date.replace("-", "")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(WAYBACK_AVAILABILITY_API, params={"url": url, "timestamp": timestamp})
        resp.raise_for_status()
        data = resp.json()
        if "archived_snapshots" in data and "closest" in data["archived_snapshots"]:
            return data["archived_snapshots"]["closest"]
    return None


async def fetch_archived_page(archive_url: str) -> str:
    """Fetch the archived page content and convert to text."""
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(archive_url)
        resp.raise_for_status()
        html_content = resp.text

    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.ignore_emphasis = False
    h.body_width = 0  # Don't wrap text
    text_content = h.handle(html_content)

    # Limit output size to avoid overwhelming context
    max_chars = 50000
    if len(text_content) > max_chars:
        text_content = text_content[:max_chars] + "\n\n[Content truncated due to length...]"

    return text_content


def parse_wayback_timestamp(timestamp: str) -> str:
    """Convert Wayback timestamp (YYYYMMDDHHMMSS) to YYYY-MM-DD format."""
    if len(timestamp) >= 8:
        return f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]}"
    return timestamp


def get_url_variations(url: str) -> list[str]:
    """Generate URL variations to try (e.g., with .html, trailing slash)."""
    variations = [url]

    # Don't add variations if URL already has a file extension or query string
    if "?" in url or url.endswith("/"):
        return variations

    # Check if URL already has a common extension
    path = url.split("?")[0]
    if path.endswith((".html", ".htm", ".php", ".asp", ".aspx", ".jsp")):
        return variations

    # Add common variations
    variations.extend([
        url + ".html",
        url + ".htm",
        url + "/",
    ])

    return variations


@mcp.tool(
    name="get_archived_snapshot",
    title="Get Archived Snapshot",
    description="""Fetch the content of a webpage from the Internet Archive Wayback Machine at or before a specified date.

Use this tool when you need to see how a webpage looked at a specific point in the past. The tool will find
the closest available snapshot before or on the target date and return its content as readable text.

Examples:
- To check a company's team page from 6 months ago: get_archived_snapshot(url="example.com/team", target_date="2024-06-01")
- To verify historical product pricing: get_archived_snapshot(url="store.com/pricing", target_date="2023-01-15")
- To see an organization's past mission statement: get_archived_snapshot(url="nonprofit.org/about", target_date="2022-12-01")

Note: Not all pages are archived, and archive frequency varies. The tool returns the closest available snapshot.""",
    tags={"backtesting_supported", "output:high", "format:text"},
    exclude_args=["cutoff_date"],
    meta={"when_to_use": "Use when you need historical webpage content for forecasting questions about organization changes, website history, or verifying past information."},
)
@notify_on_error
async def get_archived_snapshot(
    url: Annotated[str, "The URL to fetch from the archive (e.g., 'example.com/page' or 'https://example.com/page')"],
    target_date: Annotated[str, "Target date to find snapshot for, in YYYY-MM-DD format"],
    cutoff_date: Annotated[str, "Cutoff date for backtesting (hidden from LLM)"] = datetime.now().strftime("%Y-%m-%d"),
) -> str:
    # Validate date format
    try:
        target_dt = datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        return f"Error: Invalid target_date format '{target_date}'. Please use YYYY-MM-DD format."

    # For backtesting: ensure we don't access snapshots after cutoff_date
    try:
        cutoff_dt = datetime.strptime(cutoff_date, "%Y-%m-%d")
        if target_dt > cutoff_dt:
            target_date = cutoff_date
            target_dt = cutoff_dt
    except ValueError:
        pass  # If cutoff_date is invalid, proceed with original target_date

    # Normalize URL
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Try URL variations (original, .html, .htm, /) to find snapshots
    url_variations = get_url_variations(url)
    snapshot = None
    matched_url = url

    for url_variant in url_variations:
        snapshot = await find_closest_snapshot(url_variant, target_date)
        if snapshot:
            # Verify snapshot is before target date
            snapshot_timestamp = snapshot.get("timestamp", "")
            snapshot_date = parse_wayback_timestamp(snapshot_timestamp)
            try:
                snapshot_dt = datetime.strptime(snapshot_date, "%Y-%m-%d")
                if snapshot_dt <= target_dt:
                    matched_url = url_variant
                    break
            except ValueError:
                matched_url = url_variant
                break
        snapshot = None

    if not snapshot:
        tried = ", ".join(url_variations)
        return f"No archived snapshot found for '{url}' at or before {target_date}. Tried URL variations: {tried}. The page may not be archived in the Wayback Machine."

    snapshot_timestamp = snapshot.get("timestamp", "")
    snapshot_date = parse_wayback_timestamp(snapshot_timestamp)

    archive_url = snapshot.get("url", "")
    if not archive_url:
        return f"Error: Could not retrieve archive URL for '{matched_url}'."

    content = await fetch_archived_page(archive_url)

    result = f"""## Archived Snapshot
**Original URL:** {url}
**Matched URL:** {matched_url}
**Snapshot Date:** {snapshot_date}
**Archive URL:** {archive_url}

---

{content}"""

    return result


@mcp.tool(
    name="list_available_snapshots",
    title="List Available Snapshots",
    description="""List available snapshots for a URL within a date range from the Internet Archive Wayback Machine.

Use this tool to discover what archived versions of a webpage exist before fetching specific content.
This helps you identify which dates have captures available.

Examples:
- To find all snapshots of a page in 2024: list_available_snapshots(url="example.com", start_date="2024-01-01", end_date="2024-12-31")
- To check recent archive coverage: list_available_snapshots(url="company.com/team", limit=5)

Returns a list of available snapshot dates with their archive URLs.""",
    tags={"output:medium", "format:json"},
    meta={"when_to_use": "Use to discover available archive dates before fetching specific snapshots, especially when unsure about archive coverage."},
)
@notify_on_error
async def list_available_snapshots(
    url: Annotated[str, "The URL to check for snapshots (e.g., 'example.com' or 'https://example.com/page')"],
    start_date: Annotated[str | None, "Start of date range in YYYY-MM-DD format (optional)"] = None,
    end_date: Annotated[str | None, "End of date range in YYYY-MM-DD format (optional)"] = None,
    limit: Annotated[int, "Maximum number of snapshots to return (default 20, max 50)"] = 20,
) -> str:
    # Validate and cap limit
    limit = min(max(1, limit), 50)

    # Normalize URL
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Try URL variations to find snapshots
    url_variations = get_url_variations(url)
    data = None
    matched_url = url

    async with httpx.AsyncClient(timeout=30) as client:
        for url_variant in url_variations:
            params = {
                "url": url_variant,
                "output": "json",
                "fl": "timestamp,original,statuscode",
                "filter": "statuscode:200",
                "collapse": "timestamp:8",
                "limit": limit * 2,
                "sort": "reverse",
            }
            if start_date:
                params["from"] = start_date.replace("-", "")
            if end_date:
                params["to"] = end_date.replace("-", "")

            resp = await client.get(WAYBACK_CDX_API, params=params)
            resp.raise_for_status()

            try:
                variant_data = resp.json()
                if len(variant_data) > 1:  # Has results (first row is header)
                    data = variant_data
                    matched_url = url_variant
                    break
            except json.JSONDecodeError:
                continue

    if not data or len(data) <= 1:
        tried = ", ".join(url_variations)
        return json.dumps({
            "url": url,
            "tried_variations": url_variations,
            "snapshots": [],
            "message": f"No archived snapshots found. Tried: {tried}" +
                      (f" between {start_date} and {end_date}" if start_date or end_date else "")
        }, indent=2)

    headers = data[0]
    snapshots = []

    for row in data[1:limit + 1]:
        row_dict = dict(zip(headers, row))
        timestamp = row_dict.get("timestamp", "")
        snapshot_date = parse_wayback_timestamp(timestamp)
        archive_url = f"{WAYBACK_BASE_URL}/{timestamp}/{row_dict.get('original', matched_url)}"

        snapshots.append({
            "date": snapshot_date,
            "timestamp": timestamp,
            "archive_url": archive_url,
        })

    result = {
        "url": url,
        "matched_url": matched_url,
        "total_found": len(snapshots),
        "date_range": {
            "start": start_date,
            "end": end_date,
        },
        "snapshots": snapshots,
    }

    return json.dumps(result, indent=2)


if __name__ == "__main__":
    mcp.run()
