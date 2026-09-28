"""Local fixture service for actual SDK protocol integration, not a live external API."""
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

mcp = FastMCP("agentforge-fixture")


class Policy(BaseModel):
    domain: str
    minimum_growth: int
    source: str
    text: str


@mcp.tool()
def policy_lookup(domain: str) -> Policy:
    """Return an explicitly synthetic deployment policy."""
    return Policy(domain=domain, minimum_growth=10, source="fixture://policy/v1",
                  text="Synthetic policy: require growth of at least 10 percent and evidence review.")


if __name__ == "__main__":
    mcp.run(transport="stdio")
