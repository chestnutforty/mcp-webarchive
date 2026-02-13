"""MCP server for Internet Archive Wayback Machine.

Access historical webpage snapshots from the Internet Archive Wayback Machine.
Useful for retrieving historical content, verifying past information, and tracking
changes to websites over time.
"""

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

from rate_limiter import RateLimiter, rate_limited

# Slack webhook for error notifications
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
MCP_NAME = "webarchive"

WAYBACK_CDX_API = "https://web.archive.org/cdx/search/cdx"
WAYBACK_BASE_URL = "https://web.archive.org/web"


def send_slack_error(
    tool_name: str, error: Exception, args: tuple, kwargs: dict
) -> None:
    """Send error notification to Slack webhook."""
    try:
        error_message = {
            "text": f"MCP Tool Error in `{MCP_NAME}`",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "MCP Tool Error",
                        "emoji": True,
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*MCP Server:*\n{MCP_NAME}"},
                        {"type": "mrkdwn", "text": f"*Tool:*\n{tool_name}"},
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Args:*\n```{str(args)[:200]}```",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Kwargs:*\n```{str(kwargs)[:300]}```",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Error:*\n```{str(error)[:500]}```",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Traceback:*\n```{traceback.format_exc()[:1000]}```",
                    },
                },
            ],
        }
        httpx.post(SLACK_WEBHOOK_URL, json=error_message, timeout=5)
    except Exception:
        pass  # Don't let Slack errors affect the tool response


def notify_on_error(func):
    """Decorator to send Slack notification on tool errors."""

    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            send_slack_error(func.__name__, e, args, kwargs)
            raise
    return wrapper


# Proxy support - ONLY for APIs with NO authentication
# APIs with API keys track usage per key, so proxy is unnecessary
# APIs without auth rate-limit by IP, so proxy helps avoid limits
def _build_proxy_url() -> str | None:
    """Build Oxylabs proxy URL from credentials."""
    username = os.getenv("OXYLABS_USERNAME")
    password = os.getenv("OXYLABS_PASSWORD")
    if not username or not password:
        return None
    return f"http://{username}:{password}@pr.oxylabs.io:7777"


PROXY_URL = _build_proxy_url()

# Rate limiter instance
_limiter = RateLimiter()

mcp = FastMCP(
    name="webarchive",
    instructions="""Access historical webpage snapshots from the Internet Archive Wayback Machine.
This datasource provides archived versions of websites captured over time, allowing you to see how
webpages looked at specific points in the past. Useful for retrieving historical content, verifying
past information, and tracking changes to websites over time.""".strip(),
)


async def find_snapshot_before_date(client: httpx.AsyncClient, url: str, target_date: str) -> dict | None:
    """Query the CDX API to find the most recent snapshot at or before target_date."""
    timestamp = target_date.replace("-", "")
    params = {
        "url": url,
        "output": "json",
        "fl": "timestamp,original,statuscode",
        "filter": "statuscode:200",
        "to": timestamp,  # Only snapshots up to this date
        "limit": 1,
        "sort": "reverse",  # Most recent first
    }
    resp = await client.get(WAYBACK_CDX_API, params=params)
    resp.raise_for_status()
    try:
        data = resp.json()
        if len(data) > 1:  # First row is header, second is data
            headers = data[0]
            row = data[1]
            row_dict = dict(zip(headers, row))
            timestamp = row_dict.get("timestamp", "")
            original_url = row_dict.get("original", url)
            return {
                "timestamp": timestamp,
                "url": f"{WAYBACK_BASE_URL}/{timestamp}/{original_url}",
            }
    except json.JSONDecodeError:
        pass
    return None


async def fetch_archived_page(client: httpx.AsyncClient, archive_url: str) -> str:
    """Fetch the archived page content and convert to text."""
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


def extract_domain(url: str) -> str:
    """Extract domain from URL."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.netloc or parsed.path.split("/")[0]


async def get_domain_diagnostics(
    client: httpx.AsyncClient,
    url: str,
    cutoff_date: str | None = None
) -> dict:
    """Check if domain has any captures and suggest alternatives."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    domain = parsed.netloc
    path = parsed.path

    # Build date filter
    date_params = {}
    if cutoff_date:
        date_params["to"] = cutoff_date.replace("-", "")

    diagnostics = {
        "domain": domain,
        "path": path,
        "domain_has_captures": False,
        "sample_archived_paths": [],
        "hints": [],
        "reason": "unknown",
    }

    # Check domain-wide captures (limit to paths within cutoff)
    params = {
        "url": f"{domain}/*",
        "output": "json",
        "fl": "original,timestamp",
        "filter": "statuscode:200",
        "collapse": "urlkey",
        "limit": 50,
        **date_params,
    }

    try:
        resp = await client.get(WAYBACK_CDX_API, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if len(data) > 1:
                diagnostics["domain_has_captures"] = True
                headers = data[0]

                # Extract unique paths
                seen_paths = set()
                for row in data[1:]:
                    row_dict = dict(zip(headers, row))
                    original = row_dict.get("original", "")
                    timestamp = row_dict.get("timestamp", "")

                    # Filter by cutoff date
                    if cutoff_date:
                        snapshot_date = parse_wayback_timestamp(timestamp)
                        if snapshot_date > cutoff_date:
                            continue

                    # Extract path from URL
                    try:
                        orig_parsed = urlparse(original)
                        orig_path = orig_parsed.path or "/"
                        if orig_path not in seen_paths and len(seen_paths) < 10:
                            seen_paths.add(orig_path)
                            diagnostics["sample_archived_paths"].append(orig_path)
                    except Exception:
                        continue
    except Exception:
        pass

    # Determine reason and hints
    if not diagnostics["domain_has_captures"]:
        diagnostics["reason"] = "domain_not_archived"
        diagnostics["hints"].append(f"The domain '{domain}' has no captures in the Wayback Machine (or none before the cutoff date).")
        # Check www variant
        if domain.startswith("www."):
            alt = domain[4:]
        else:
            alt = "www." + domain
        diagnostics["hints"].append(f"Try the alternate host: {alt}")
    else:
        diagnostics["reason"] = "path_not_archived"
        diagnostics["hints"].append(f"The specific path '{path}' is not archived, but the domain has captures.")

        # Suggest similar paths
        if path and path != "/":
            path_keywords = [p for p in path.strip("/").split("/") if p]
            matching_paths = [
                p for p in diagnostics["sample_archived_paths"]
                if any(kw.lower() in p.lower() for kw in path_keywords)
            ]
            if matching_paths:
                diagnostics["hints"].append(f"Similar archived paths found: {', '.join(matching_paths[:5])}")
            else:
                diagnostics["hints"].append(f"Try one of these archived paths: {', '.join(diagnostics['sample_archived_paths'][:5])}")

    return diagnostics


def get_url_variations(url: str, include_host_variants: bool = True) -> list[str]:
    """Generate URL variations to try (e.g., with .html, trailing slash, www/non-www)."""
    from urllib.parse import urlparse, urlunparse

    variations = [url]

    # Add www/non-www variant
    if include_host_variants:
        parsed = urlparse(url)
        host = parsed.netloc
        if host.startswith("www."):
            alt_host = host[4:]
        else:
            alt_host = "www." + host
        alt_parsed = parsed._replace(netloc=alt_host)
        alt_url = urlunparse(alt_parsed)
        if alt_url != url:
            variations.append(alt_url)

    # Don't add path variations if URL already has a file extension or query string
    if "?" in url or url.endswith("/"):
        return variations

    # Check if URL already has a common extension
    path = url.split("?")[0]
    if path.endswith((".html", ".htm", ".php", ".asp", ".aspx", ".jsp")):
        return variations

    # Add common path variations for each host variant
    base_variations = list(variations)
    for base_url in base_variations:
        for suffix in [".html", ".htm", "/"]:
            variant = base_url + suffix
            if variant not in variations:
                variations.append(variant)

    return variations


# =============================================================================
# TOOLS
# =============================================================================


@mcp.tool(
    name="webarchive_get_snapshot",
    title="Get Archived Snapshot",
    description="""Fetch the content of a webpage from the Internet Archive Wayback Machine at or before a specified date.

Finds the closest available snapshot before or on the target date and returns its content as readable text.
Automatically tries www/non-www variants and common extensions (.html, .htm, /).

Tip: If unsure whether a page is archived, first use webarchive_list_snapshots to see available dates,
then call this tool with a date from those results.

Note: Not all pages are archived, and archive frequency varies. The tool returns the closest available snapshot.""",
    tags={"backtesting_supported", "output:high", "format:text"},
    exclude_args=["cutoff_date"],
    meta={
        "when_to_use": """Use when forecasting questions require verifying historical webpage content.

Forecast: "Will company X change its leadership by Q2 2025?"
-> webarchive_get_snapshot(url="company.com/team", target_date="2024-12-01") to check current team composition

Forecast: "Will organization Y update its policy on Z?"
-> webarchive_get_snapshot(url="org.com/policy", target_date="2024-06-01") to see past policy text

Forecast: "Has product pricing changed over the past year?"
-> webarchive_get_snapshot(url="store.com/pricing", target_date="2023-01-15") to verify historical pricing
"""
    },
)
@rate_limited(_limiter)
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

    # Use single client for all operations to avoid connection pool exhaustion
    async with httpx.AsyncClient(timeout=60, follow_redirects=True, proxy=PROXY_URL) as client:
        for url_variant in url_variations:
            snapshot = await find_snapshot_before_date(client, url_variant, target_date)
            if snapshot:
                matched_url = url_variant
                break

        if not snapshot:
            # Get diagnostics to provide helpful hints
            diagnostics = await get_domain_diagnostics(client, url, cutoff_date=target_date)
            tried = ", ".join(url_variations)

            result = f"No archived snapshot found for '{url}' at or before {target_date}.\n\n"
            result += f"**Tried URL variations:** {tried}\n\n"
            result += f"**Reason:** {diagnostics['reason'].replace('_', ' ').title()}\n\n"

            if diagnostics["hints"]:
                result += "**Hints:**\n"
                for hint in diagnostics["hints"]:
                    result += f"- {hint}\n"

            if diagnostics["sample_archived_paths"]:
                result += f"\n**Archived paths on this domain (before {target_date}):**\n"
                for path in diagnostics["sample_archived_paths"][:8]:
                    result += f"- {path}\n"

            return result

        snapshot_timestamp = snapshot.get("timestamp", "")
        snapshot_date = parse_wayback_timestamp(snapshot_timestamp)

        archive_url = snapshot.get("url", "")
        if not archive_url:
            return f"Error: Could not retrieve archive URL for '{matched_url}'."

        content = await fetch_archived_page(client, archive_url)

    result = f"""## Archived Snapshot
**Original URL:** {url}
**Matched URL:** {matched_url}
**Snapshot Date:** {snapshot_date}
**Archive URL:** {archive_url}

---

{content}"""

    return result


def apply_pick_filter(snapshots: list[dict], pick: str, target_date: str | None = None) -> list[dict]:
    """Apply snapshot selection filter based on pick parameter."""
    if not snapshots or not pick:
        return snapshots

    if pick == "closest_to_end":
        # Return single snapshot closest to end of range (most recent)
        return snapshots[:1] if snapshots else []

    elif pick == "closest_to_start":
        # Return single snapshot closest to start of range (oldest)
        return snapshots[-1:] if snapshots else []

    elif pick == "closest_to_date" and target_date:
        # Return single snapshot closest to specified date
        try:
            target_dt = datetime.strptime(target_date, "%Y-%m-%d")
            closest = min(
                snapshots,
                key=lambda s: abs((datetime.strptime(s["date"], "%Y-%m-%d") - target_dt).days)
            )
            return [closest]
        except (ValueError, KeyError):
            return snapshots[:1]

    elif pick == "monthly":
        # Return one snapshot per month
        seen_months = set()
        monthly = []
        for s in snapshots:
            month_key = s["date"][:7]  # YYYY-MM
            if month_key not in seen_months:
                seen_months.add(month_key)
                monthly.append(s)
        return monthly

    elif pick == "yearly":
        # Return one snapshot per year
        seen_years = set()
        yearly = []
        for s in snapshots:
            year_key = s["date"][:4]  # YYYY
            if year_key not in seen_years:
                seen_years.add(year_key)
                yearly.append(s)
        return yearly

    return snapshots


@mcp.tool(
    name="webarchive_list_snapshots",
    title="List Available Snapshots",
    description="""List available snapshots for a URL within a date range from the Internet Archive Wayback Machine.

Discover what archived versions of a webpage exist before fetching specific content.

Workflow:
1. Call webarchive_list_snapshots to see what dates have archived versions
2. Pick a date from the results
3. Call webarchive_get_snapshot with that date as target_date to fetch the content

Supports multi-year queries via `years` parameter and snapshot selection via `pick`:
- `closest_to_end`: Most recent snapshot in range
- `closest_to_start`: Oldest snapshot in range
- `closest_to_date`: Closest to a specific target_date
- `monthly`: One snapshot per month
- `yearly`: One snapshot per year

Returns a list of available snapshot dates with their archive URLs.""",
    tags={"backtesting_supported", "output:medium", "format:json"},
    exclude_args=["cutoff_date"],
    meta={
        "when_to_use": """Use to discover available archive dates before fetching specific snapshots.

Forecast: "Has a website changed over the past 3 years?"
-> webarchive_list_snapshots(url="example.com", years=[2022, 2023, 2024], pick="yearly")

Forecast: "When did an organization last update its team page?"
-> webarchive_list_snapshots(url="org.com/team", start_date="2024-01-01")

Forecast: "Is a website still active / being maintained?"
-> webarchive_list_snapshots(url="startup.io", limit=10) to check recent capture frequency
"""
    },
)
@rate_limited(_limiter)
@notify_on_error
async def list_available_snapshots(
    url: Annotated[str, "The URL to check for snapshots (e.g., 'example.com' or 'https://example.com/page')"],
    start_date: Annotated[str | None, "Start of date range in YYYY-MM-DD format (optional)"] = None,
    end_date: Annotated[str | None, "End of date range in YYYY-MM-DD format (optional)"] = None,
    years: Annotated[list[int] | None, "List of years to query (e.g., [2022, 2023, 2024]). Overrides start_date/end_date."] = None,
    pick: Annotated[str | None, "Snapshot selection: 'closest_to_end', 'closest_to_start', 'closest_to_date', 'monthly', 'yearly'"] = None,
    target_date: Annotated[str | None, "Target date for 'closest_to_date' pick option (YYYY-MM-DD)"] = None,
    limit: Annotated[int, "Maximum number of snapshots to return (default 20, max 50)"] = 20,
    cutoff_date: Annotated[str, "Cutoff date for backtesting (hidden from LLM)"] = datetime.now().strftime("%Y-%m-%d"),
) -> str:
    # Validate and cap limit
    limit = min(max(1, limit), 50)

    # Parse cutoff date
    cutoff_dt = None
    if cutoff_date:
        try:
            cutoff_dt = datetime.strptime(cutoff_date, "%Y-%m-%d")
        except ValueError:
            pass

    # If years provided, query each year separately
    if years:
        all_snapshots = []
        years_queried = []

        # Filter years to only include those before cutoff
        for year in sorted(set(years)):
            if cutoff_dt and year > cutoff_dt.year:
                continue
            years_queried.append(year)

        # Normalize URL
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        url_variations = get_url_variations(url)
        matched_url = url

        async with httpx.AsyncClient(timeout=30, proxy=PROXY_URL) as client:
            for year in years_queried:
                year_start = f"{year}-01-01"
                year_end = f"{year}-12-31"

                # Cap year_end at cutoff_date
                if cutoff_dt and year == cutoff_dt.year:
                    year_end = cutoff_date

                for url_variant in url_variations:
                    params = {
                        "url": url_variant,
                        "output": "json",
                        "fl": "timestamp,original,statuscode",
                        "filter": "statuscode:200",
                        "collapse": "timestamp:8",
                        "limit": 20,
                        "sort": "reverse",
                        "from": year_start.replace("-", ""),
                        "to": year_end.replace("-", ""),
                    }

                    try:
                        resp = await client.get(WAYBACK_CDX_API, params=params)
                        resp.raise_for_status()
                        data = resp.json()
                        if len(data) > 1:
                            matched_url = url_variant
                            headers = data[0]
                            for row in data[1:]:
                                row_dict = dict(zip(headers, row))
                                timestamp = row_dict.get("timestamp", "")
                                snapshot_date = parse_wayback_timestamp(timestamp)

                                # Extra safety: filter by cutoff
                                if cutoff_dt:
                                    try:
                                        snap_dt = datetime.strptime(snapshot_date, "%Y-%m-%d")
                                        if snap_dt > cutoff_dt:
                                            continue
                                    except ValueError:
                                        pass

                                archive_url = f"{WAYBACK_BASE_URL}/{timestamp}/{row_dict.get('original', matched_url)}"
                                all_snapshots.append({
                                    "date": snapshot_date,
                                    "timestamp": timestamp,
                                    "archive_url": archive_url,
                                    "year": year,
                                })
                            break
                    except Exception:
                        continue

            if not all_snapshots:
                diagnostics = await get_domain_diagnostics(client, url, cutoff_date=cutoff_date)
                return json.dumps({
                    "url": url,
                    "years_queried": years_queried,
                    "snapshots": [],
                    "diagnostics": diagnostics,
                }, indent=2)

        # Apply pick filter
        all_snapshots = apply_pick_filter(all_snapshots, pick, target_date)

        # Group by year for output
        by_year = {}
        for s in all_snapshots:
            yr = s.get("year", s["date"][:4])
            if yr not in by_year:
                by_year[yr] = []
            by_year[yr].append(s)

        return json.dumps({
            "url": url,
            "matched_url": matched_url,
            "years_queried": years_queried,
            "total_found": len(all_snapshots),
            "snapshots_by_year": by_year,
            "snapshots": all_snapshots[:limit],
        }, indent=2)

    # Standard single-range query
    # For backtesting: cap end_date at cutoff_date to prevent leakage
    effective_end_date = end_date
    if cutoff_date:
        if end_date is None:
            effective_end_date = cutoff_date
        else:
            effective_end_date = min(end_date, cutoff_date)

    # Cap target_date at cutoff_date for closest_to_date
    effective_target_date = target_date
    if target_date and cutoff_date and target_date > cutoff_date:
        effective_target_date = cutoff_date

    # Normalize URL
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Try URL variations to find snapshots
    url_variations = get_url_variations(url)
    data = None
    matched_url = url

    async with httpx.AsyncClient(timeout=30, proxy=PROXY_URL) as client:
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
            if effective_end_date:
                params["to"] = effective_end_date.replace("-", "")

            try:
                resp = await client.get(WAYBACK_CDX_API, params=params)
                resp.raise_for_status()

                variant_data = resp.json()
                if len(variant_data) > 1:  # Has results (first row is header)
                    data = variant_data
                    matched_url = url_variant
                    break
            except (httpx.HTTPError, json.JSONDecodeError):
                continue

        if not data or len(data) <= 1:
            # Get diagnostics for helpful error message
            diagnostics = await get_domain_diagnostics(client, url, cutoff_date=effective_end_date)
            return json.dumps({
                "url": url,
                "tried_variations": url_variations,
                "snapshots": [],
                "diagnostics": diagnostics,
            }, indent=2)

    headers = data[0]
    snapshots = []

    for row in data[1:limit * 2]:
        row_dict = dict(zip(headers, row))
        timestamp = row_dict.get("timestamp", "")
        snapshot_date = parse_wayback_timestamp(timestamp)

        # Filter out snapshots after cutoff_date (extra safety)
        if cutoff_dt:
            try:
                snapshot_dt = datetime.strptime(snapshot_date, "%Y-%m-%d")
                if snapshot_dt > cutoff_dt:
                    continue
            except ValueError:
                pass

        archive_url = f"{WAYBACK_BASE_URL}/{timestamp}/{row_dict.get('original', matched_url)}"

        snapshots.append({
            "date": snapshot_date,
            "timestamp": timestamp,
            "archive_url": archive_url,
        })

    # Apply pick filter
    snapshots = apply_pick_filter(snapshots, pick, effective_target_date)

    # Limit final output
    snapshots = snapshots[:limit]

    result = {
        "url": url,
        "matched_url": matched_url,
        "total_found": len(snapshots),
        "date_range": {
            "start": start_date,
            "end": effective_end_date,
        },
        "snapshots": snapshots,
    }

    return json.dumps(result, indent=2)


@mcp.tool(
    name="webarchive_search_site",
    title="Search Site Archives",
    description="""Search for archived pages on a domain that match a path pattern.

Use this tool when:
- The target URL has no captures but you want to find related pages on the same domain
- You're looking for a page but don't know the exact path (e.g., "team" page could be /team, /about-us, /people)
- You want to discover what pages exist on a domain in the archive

Supports wildcard patterns (e.g., '*team*', '*about*', '/blog/*').
Searches both www and non-www variants automatically.

Returns a list of unique archived paths on the domain with their most recent snapshot dates.""",
    tags={"backtesting_supported", "output:medium", "format:json"},
    exclude_args=["cutoff_date"],
    meta={
        "when_to_use": """Use when a specific URL has no captures or you need to discover pages on a domain.

Forecast: "Will nonprofit X expand its programs?"
-> webarchive_search_site(domain="nonprofit.org", path_pattern="*program*") to find program pages

Forecast: "Does company Y have a public research page?"
-> webarchive_search_site(domain="company.com", path_pattern="*research*")

Forecast: "What sections does this organization's website have?"
-> webarchive_search_site(domain="org.com") to list all archived pages
"""
    },
)
@rate_limited(_limiter)
@notify_on_error
async def search_site_archives(
    domain: Annotated[str, "Domain to search (e.g., 'example.com' or 'www.example.com')"],
    path_pattern: Annotated[str | None, "Path pattern to match using wildcards (e.g., '*team*', '*about*', '/blog/*')"] = None,
    limit: Annotated[int, "Maximum number of unique paths to return (default 30, max 100)"] = 30,
    cutoff_date: Annotated[str, "Cutoff date for backtesting (hidden from LLM)"] = datetime.now().strftime("%Y-%m-%d"),
) -> str:
    from urllib.parse import urlparse

    # Validate and cap limit
    limit = min(max(1, limit), 100)

    # Parse cutoff date
    cutoff_dt = None
    if cutoff_date:
        try:
            cutoff_dt = datetime.strptime(cutoff_date, "%Y-%m-%d")
        except ValueError:
            pass

    # Clean domain (remove protocol if present)
    if domain.startswith(("http://", "https://")):
        parsed = urlparse(domain)
        domain = parsed.netloc

    # Build search URL pattern
    if path_pattern:
        # Ensure pattern has wildcard if not already
        if not path_pattern.startswith(("*", "/")):
            path_pattern = "*" + path_pattern
        if not path_pattern.endswith("*"):
            path_pattern = path_pattern + "*"
        search_url = f"{domain}{path_pattern}"
    else:
        search_url = f"{domain}/*"

    # Try both www and non-www variants
    domains_to_try = [domain]
    if domain.startswith("www."):
        domains_to_try.append(domain[4:])
    else:
        domains_to_try.append("www." + domain)

    all_results = []
    seen_paths = set()

    async with httpx.AsyncClient(timeout=30, proxy=PROXY_URL) as client:
        for d in domains_to_try:
            if path_pattern:
                pattern = path_pattern if path_pattern.startswith("/") else path_pattern
                query_url = f"{d}{pattern}" if pattern.startswith("/") else f"{d}/*{pattern}*"
            else:
                query_url = f"{d}/*"

            params = {
                "url": query_url,
                "output": "json",
                "fl": "original,timestamp,statuscode",
                "filter": "statuscode:200",
                "collapse": "urlkey",  # One result per unique URL
                "limit": limit * 3,  # Get extra to account for filtering
            }

            # Add date filter for cutoff
            if cutoff_date:
                params["to"] = cutoff_date.replace("-", "")

            try:
                resp = await client.get(WAYBACK_CDX_API, params=params, timeout=20)
                if resp.status_code != 200:
                    continue

                data = resp.json()
                if len(data) <= 1:
                    continue

                headers = data[0]
                for row in data[1:]:
                    row_dict = dict(zip(headers, row))
                    original = row_dict.get("original", "")
                    timestamp = row_dict.get("timestamp", "")
                    snapshot_date = parse_wayback_timestamp(timestamp)

                    # Extra safety: filter by cutoff date
                    if cutoff_dt:
                        try:
                            snap_dt = datetime.strptime(snapshot_date, "%Y-%m-%d")
                            if snap_dt > cutoff_dt:
                                continue
                        except ValueError:
                            pass

                    # Extract path
                    try:
                        orig_parsed = urlparse(original)
                        path = orig_parsed.path or "/"
                        host = orig_parsed.netloc

                        # Deduplicate by path (ignore host variations)
                        path_key = path.lower().rstrip("/") or "/"
                        if path_key in seen_paths:
                            continue
                        seen_paths.add(path_key)

                        archive_url = f"{WAYBACK_BASE_URL}/{timestamp}/{original}"
                        all_results.append({
                            "path": path,
                            "full_url": original,
                            "host": host,
                            "last_captured": snapshot_date,
                            "archive_url": archive_url,
                        })

                        if len(all_results) >= limit:
                            break
                    except Exception:
                        continue

                if len(all_results) >= limit:
                    break

            except Exception:
                continue

    # Sort by path for easier reading
    all_results.sort(key=lambda x: x["path"])

    result = {
        "domain": domain,
        "domains_searched": domains_to_try,
        "path_pattern": path_pattern,
        "cutoff_date": cutoff_date,
        "total_found": len(all_results),
        "paths": all_results[:limit],
    }

    if not all_results:
        result["message"] = f"No archived pages found for domain '{domain}'" + (
            f" matching pattern '{path_pattern}'" if path_pattern else ""
        ) + f" before {cutoff_date}."
        result["hints"] = [
            "Try a different domain variant (www vs non-www)",
            "Try a broader path pattern",
            "The site may not be well-archived in the Wayback Machine",
        ]

    return json.dumps(result, indent=2)


if __name__ == "__main__":
    mcp.run()
