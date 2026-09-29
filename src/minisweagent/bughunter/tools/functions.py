"""Local Python functions exposed as tools.

Any function decorated with ``@server.tool`` becomes callable by the model. Add your own
functions here, or build another in-memory ``FastMCP`` server and pass it to
``ToolRegistry.add_server``. Tools are exposed as ``local__<name>``.
"""

from fastmcp import FastMCP


def build_local_server() -> FastMCP:
    server = FastMCP("local")

    @server.tool
    def record_note(text: str) -> str:
        """Record a short note or finding for later reference during this run."""
        return f"Recorded note: {text}"

    return server
