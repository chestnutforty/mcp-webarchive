import inspect

import pytest

from server import (
    get_archived_snapshot,
    list_available_snapshots,
    search_site_archives,
    mcp,
    parse_wayback_timestamp,
    get_url_variations,
    apply_pick_filter,
)


class TestBacktestToolConfiguration:
    """Tests for backtesting tool configuration - ALL tools should support backtesting."""

    @pytest.mark.parametrize("tool", [get_archived_snapshot, list_available_snapshots, search_site_archives])
    def test_backtest_tool_has_backtesting_supported_tag(self, tool):
        tags = getattr(tool, "tags", set()) or set()
        assert "backtesting_supported" in tags, f"Missing 'backtesting_supported' tag for {tool.name}. Found: {tags}"

    @pytest.mark.parametrize("tool", [get_archived_snapshot, list_available_snapshots, search_site_archives])
    def test_backtest_tool_has_cutoff_date_parameter(self, tool):
        sig = inspect.signature(tool.fn)
        params = list(sig.parameters.keys())
        assert "cutoff_date" in params, f"Missing 'cutoff_date' parameter for {tool.name}. Found: {params}"

    @pytest.mark.parametrize("tool", [get_archived_snapshot, list_available_snapshots, search_site_archives])
    def test_backtest_tool_cutoff_date_excluded_from_schema(self, tool):
        schema_params = tool.parameters.get("properties", {}).keys()
        assert "cutoff_date" not in schema_params, f"'cutoff_date' should be excluded from schema for {tool.name}. Found: {list(schema_params)}"

    @pytest.mark.parametrize("tool", [get_archived_snapshot, list_available_snapshots, search_site_archives])
    def test_backtest_tool_cutoff_date_has_default(self, tool):
        sig = inspect.signature(tool.fn)
        cutoff_param = sig.parameters.get("cutoff_date")
        assert cutoff_param is not None, f"cutoff_date parameter not found for {tool.name}"
        assert cutoff_param.default != inspect.Parameter.empty, f"cutoff_date should have default value for {tool.name}"


class TestAllToolsBacktestingConsistency:
    """Tests to ensure all tools in the server have consistent backtesting configuration."""

    def get_all_tools(self):
        return mcp._tool_manager._tools

    def test_all_backtest_tools_have_correct_configuration(self):
        tools = self.get_all_tools()

        for tool_name, tool in tools.items():
            tags = getattr(tool, "tags", set()) or set()

            if "backtesting_supported" in tags:
                sig = inspect.signature(tool.fn)
                params = list(sig.parameters.keys())
                assert "cutoff_date" in params, f"Tool '{tool_name}' missing 'cutoff_date' parameter"

                schema_params = tool.parameters.get("properties", {}).keys()
                assert "cutoff_date" not in schema_params, f"Tool '{tool_name}' should exclude 'cutoff_date' from schema"

    def test_all_non_backtest_tools_have_correct_configuration(self):
        tools = self.get_all_tools()

        for tool_name, tool in tools.items():
            tags = getattr(tool, "tags", set()) or set()

            if "backtesting_supported" not in tags:
                sig = inspect.signature(tool.fn)
                params = list(sig.parameters.keys())
                assert "cutoff_date" not in params, f"Tool '{tool_name}' should not have 'cutoff_date' without backtesting tag"


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_parse_wayback_timestamp_full(self):
        result = parse_wayback_timestamp("20240115123456")
        assert result == "2024-01-15"

    def test_parse_wayback_timestamp_date_only(self):
        result = parse_wayback_timestamp("20240115")
        assert result == "2024-01-15"

    def test_parse_wayback_timestamp_short(self):
        result = parse_wayback_timestamp("2024")
        assert result == "2024"  # Returns as-is if too short

    def test_get_url_variations_includes_www(self):
        variations = get_url_variations("https://example.com/page")
        assert "https://www.example.com/page" in variations

    def test_get_url_variations_removes_www(self):
        variations = get_url_variations("https://www.example.com/page")
        assert "https://example.com/page" in variations

    def test_get_url_variations_adds_extensions(self):
        variations = get_url_variations("https://example.com/page")
        assert "https://example.com/page.html" in variations
        assert "https://example.com/page.htm" in variations
        assert "https://example.com/page/" in variations

    def test_get_url_variations_no_extension_for_existing(self):
        variations = get_url_variations("https://example.com/page.html")
        # Should not add .html again
        assert "https://example.com/page.html.html" not in variations

    def test_apply_pick_filter_closest_to_end(self):
        snapshots = [
            {"date": "2024-03-01"},
            {"date": "2024-02-01"},
            {"date": "2024-01-01"},
        ]
        result = apply_pick_filter(snapshots, "closest_to_end")
        assert len(result) == 1
        assert result[0]["date"] == "2024-03-01"

    def test_apply_pick_filter_closest_to_start(self):
        snapshots = [
            {"date": "2024-03-01"},
            {"date": "2024-02-01"},
            {"date": "2024-01-01"},
        ]
        result = apply_pick_filter(snapshots, "closest_to_start")
        assert len(result) == 1
        assert result[0]["date"] == "2024-01-01"

    def test_apply_pick_filter_monthly(self):
        snapshots = [
            {"date": "2024-03-15"},
            {"date": "2024-03-01"},
            {"date": "2024-02-15"},
            {"date": "2024-02-01"},
            {"date": "2024-01-15"},
        ]
        result = apply_pick_filter(snapshots, "monthly")
        assert len(result) == 3
        months = [s["date"][:7] for s in result]
        assert "2024-03" in months
        assert "2024-02" in months
        assert "2024-01" in months

    def test_apply_pick_filter_yearly(self):
        snapshots = [
            {"date": "2024-06-15"},
            {"date": "2024-01-15"},
            {"date": "2023-06-15"},
            {"date": "2022-06-15"},
        ]
        result = apply_pick_filter(snapshots, "yearly")
        assert len(result) == 3
        years = [s["date"][:4] for s in result]
        assert "2024" in years
        assert "2023" in years
        assert "2022" in years

    def test_apply_pick_filter_closest_to_date(self):
        snapshots = [
            {"date": "2024-03-01"},
            {"date": "2024-02-01"},
            {"date": "2024-01-01"},
        ]
        result = apply_pick_filter(snapshots, "closest_to_date", "2024-02-15")
        assert len(result) == 1
        assert result[0]["date"] == "2024-02-01"


class TestToolSchemas:
    """Tests for tool schemas."""

    def test_get_archived_snapshot_has_required_params(self):
        tool = get_archived_snapshot
        props = tool.parameters.get("properties", {})
        required = tool.parameters.get("required", [])

        assert "url" in props, "Missing 'url' parameter"
        assert "target_date" in props, "Missing 'target_date' parameter"
        assert "url" in required, "'url' should be required"
        assert "target_date" in required, "'target_date' should be required"

    def test_list_available_snapshots_has_required_params(self):
        tool = list_available_snapshots
        props = tool.parameters.get("properties", {})
        required = tool.parameters.get("required", [])

        assert "url" in props, "Missing 'url' parameter"
        assert "url" in required, "'url' should be required"
        assert "start_date" in props, "Missing 'start_date' parameter"
        assert "end_date" in props, "Missing 'end_date' parameter"
        assert "limit" in props, "Missing 'limit' parameter"
        assert "years" in props, "Missing 'years' parameter"
        assert "pick" in props, "Missing 'pick' parameter"

    def test_search_site_archives_has_required_params(self):
        tool = search_site_archives
        props = tool.parameters.get("properties", {})
        required = tool.parameters.get("required", [])

        assert "domain" in props, "Missing 'domain' parameter"
        assert "domain" in required, "'domain' should be required"
        assert "path_pattern" in props, "Missing 'path_pattern' parameter"
        assert "limit" in props, "Missing 'limit' parameter"
