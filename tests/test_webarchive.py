import inspect

import pytest

from server import get_archived_snapshot, list_available_snapshots, mcp, parse_wayback_timestamp


class TestBacktestToolConfiguration:
    """Tests for backtesting tool configuration."""

    def test_backtest_tool_has_backtesting_supported_tag(self):
        tool = get_archived_snapshot
        tags = getattr(tool, "tags", set()) or set()
        assert "backtesting_supported" in tags, f"Missing 'backtesting_supported' tag. Found: {tags}"

    def test_backtest_tool_has_cutoff_date_parameter(self):
        sig = inspect.signature(get_archived_snapshot.fn)
        params = list(sig.parameters.keys())
        assert "cutoff_date" in params, f"Missing 'cutoff_date' parameter. Found: {params}"

    def test_backtest_tool_cutoff_date_excluded_from_schema(self):
        tool = get_archived_snapshot
        schema_params = tool.parameters.get("properties", {}).keys()
        assert "cutoff_date" not in schema_params, f"'cutoff_date' should be excluded from schema. Found: {list(schema_params)}"

    def test_backtest_tool_cutoff_date_has_default(self):
        sig = inspect.signature(get_archived_snapshot.fn)
        cutoff_param = sig.parameters.get("cutoff_date")
        assert cutoff_param is not None, "cutoff_date parameter not found"
        assert cutoff_param.default != inspect.Parameter.empty, "cutoff_date should have default value"


class TestNonBacktestToolConfiguration:
    """Tests for non-backtesting tool configuration."""

    def test_list_snapshots_does_not_have_backtesting_supported_tag(self):
        tool = list_available_snapshots
        tags = getattr(tool, "tags", set()) or set()
        assert "backtesting_supported" not in tags, f"Should not have 'backtesting_supported' tag. Found: {tags}"

    def test_list_snapshots_does_not_have_cutoff_date_parameter(self):
        sig = inspect.signature(list_available_snapshots.fn)
        params = list(sig.parameters.keys())
        assert "cutoff_date" not in params, f"Should not have 'cutoff_date' parameter. Found: {params}"


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
