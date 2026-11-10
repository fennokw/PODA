"""Read-only scheduling MCP over stdio. Run with python -m poda_app.scheduling.mcp."""
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import evidence, temporal

server = FastMCP("PODA Scheduling")


@server.tool()
def resolve_event_time(sent_at: str, date_text: str, time_text: str, timezone_name: str,
                       end_text: str = "", event_timezone: str = "",
                       sender_timezone_known: bool = False) -> dict[str, str | None]:
    """Resolve an email's quoted date and clock time against its original send time."""
    return temporal.resolve_event_time(sent_at, date_text, time_text, timezone_name, end_text, event_timezone,
                                       sender_timezone_known=sender_timezone_known)


@server.tool()
def preview_email_schedule(messages: list[dict[str, Any]], model: str,
                           timezone_name: str, lookback_hours: int = 24) -> dict[str, Any]:
    """Preview confirmed events from supplied email rows with subject, snippet, date and sent_at."""
    if not 1 <= lookback_hours <= 168:
        raise ValueError("lookback_hours must be between 1 and 168.")
    return evidence.preview_email_schedule(messages, model, datetime.now(timezone.utc), timezone_name, lookback_hours)


if __name__ == "__main__":
    server.run(transport="stdio")
