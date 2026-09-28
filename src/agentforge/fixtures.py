"""Versioned synthetic workloads: 10 distinct DAG templates x 6 business domains."""
import json
import sqlite3
from pathlib import Path

from .schema import Plan

DOMAINS = ["retail", "logistics", "education", "energy", "manufacturing", "support"]


def step(sid, capability, args, deps=(), objective=""):
    tools = {"web": "web.search", "rag": "rag.search", "sql": "sql.query", "code": "code.calculate",
             "mcp": "mcp.policy_lookup"}
    return {"id": sid, "objective": objective or f"{capability}: {sid}", "capability": capability,
            "dependencies": list(deps), "tool": tools[capability], "arguments": args}


def ref(sid, field="value"):
    return {"$ref": f"{sid}.rows.0.{field}"}


def build_cases():
    corpus, cases = [], []
    for i, domain in enumerate(DOMAINS):
        previous, current, cost = 100 + 20 * i, 130 + 25 * i, 52 + 10 * i
        corpus.extend([
            {"id": f"{domain}-market", "url": f"fixture://{domain}/market", "text":
             f"{domain} market growth: demand is seasonal. Validate trends using both periods; do not extrapolate blindly."},
            {"id": f"{domain}-policy", "url": f"fixture://{domain}/policy", "text":
             f"{domain} policy risk: synthetic deployment requires growth >= 10 percent, cost review, and pilot monitoring."},
            {"id": f"{domain}-operations", "url": f"fixture://{domain}/operations", "text":
             f"{domain} operations forecast: capacity is constrained; compare unit cost and margin before rollout."}
        ])
        # Each template has different calculations, SQL projections, and dependency topology.
        recipes = [
            ("growth", "Compute percentage growth between previous/current periods and review rollout policy.",
             "(current - previous) / previous * 100", {"current": ref("current"), "previous": ref("previous")},
             (current - previous) / previous * 100),
            ("unit_cost", "Compute current cost per unit and compare it with the previous period; explain constraints.",
             "cost / current", {"cost": ref("current", "cost"), "current": ref("current")}, cost / current),
            ("margin", "Compute current absolute margin and margin percent; assess expansion evidence.",
             "(current - cost) / current * 100", {"cost": ref("current", "cost"), "current": ref("current")},
             (current - cost) / current * 100),
            ("forecast", "Forecast next-period value by extending the observed absolute change; discuss seasonality.",
             "current + (current - previous)", {"current": ref("current"), "previous": ref("previous")},
             current + current - previous),
            ("capacity", "Estimate extra capacity for a 20 percent current-volume increase and compare baseline.",
             "current * 1.2 - previous", {"current": ref("current"), "previous": ref("previous")},
             current * 1.2 - previous),
            ("cost_shock", "Stress-test unit cost if current cost rises 25 percent and current volume falls 10 percent.",
             "cost * 1.25 / (current * 0.9)", {"cost": ref("current", "cost"), "current": ref("current")},
             cost * 1.25 / (current * .9)),
            ("weighted", "Compute a 30/70 weighted previous/current forecast and compare with policy evidence.",
             "previous * 0.3 + current * 0.7", {"previous": ref("previous"), "current": ref("current")},
             previous * .3 + current * .7),
            ("payback", "Compute cost divided by incremental period value as a simple payback proxy; state limitations.",
             "cost / (current - previous)", {"cost": ref("current", "cost"), "current": ref("current"),
                                               "previous": ref("previous")}, cost / (current - previous)),
            ("break_even", "Compute added volume required to cover current cost at a unit contribution of 2.5.",
             "cost / 2.5", {"cost": ref("current", "cost")}, cost / 2.5),
            ("efficiency", "Compute change in value per unit cost between periods; assess evidence for efficiency gains.",
             "current / cost - previous / previous_cost", {"current": ref("current"), "cost": ref("current", "cost"),
               "previous": ref("previous"), "previous_cost": ref("previous", "cost")},
             current / cost - previous / (cost - 8))
        ]
        for j, (name, goal, expression, variables, expected) in enumerate(recipes):
            steps = [
                step("market", "web", {"query": f"{domain} market growth"}, objective="Find market caveats"),
                step("previous", "sql", {"query": "SELECT value,cost FROM metrics WHERE domain=:domain AND period='previous'",
                     "params": {"domain": domain}}, objective="Read previous period metrics"),
                step("current", "sql", {"query": "SELECT value,cost FROM metrics WHERE domain=:domain AND period='current'",
                     "params": {"domain": domain}}, objective="Read current period metrics"),
                step("calculation", "code", {"expression": expression, "variables": variables},
                     ["previous", "current"], objective=goal),
                step("policy", "rag", {"query": f"{domain} policy operations"}, ["market"],
                     "Retrieve independent policy and operational caveats")
            ]
            expected_values = [expected]
            if name in ("margin", "forecast", "capacity", "cost_shock", "efficiency"):
                steps.append(step("sensitivity", "code",
                    {"expression": "result * 0.9", "variables": {"result": {"$ref": "calculation.value"}}},
                    ["calculation", "policy"], "Compute a minus-10-percent sensitivity on the derived result"))
                expected_values.append(expected * .9)
                goal += " Also compute minus-10-percent sensitivity on the derived result."
            Plan(steps=steps)
            cases.append({"id": f"{domain}-{name}", "category": name, "dataset_kind": "synthetic",
                "goal": f"For the synthetic {domain} business: {goal} Read both periods and cite market and policy evidence.",
                "fixture_plan": steps, "gold": {"expected_values": expected_values,
                    "min_sources": 2, "relevant_tools": ["web.search", "rag.search", "sql.query", "code.calculate"]}})
    return corpus, cases


def initialize(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    corpus, cases = build_cases()
    analytics = data_dir / "analytics.sqlite"
    with sqlite3.connect(analytics) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS metrics(domain TEXT,period TEXT,value REAL,cost REAL, PRIMARY KEY(domain,period))")
        for i, domain in enumerate(DOMAINS):
            for period, value, cost in [("previous", 100 + 20 * i, 44 + 10 * i),
                                        ("current", 130 + 25 * i, 52 + 10 * i)]:
                conn.execute("INSERT OR IGNORE INTO metrics VALUES(?,?,?,?)", (domain, period, value, cost))
    return corpus, cases, analytics


def write_fixtures(directory: Path):
    import copy
    directory.mkdir(parents=True, exist_ok=True)
    corpus, cases = build_cases()
    mcp_demo = copy.deepcopy(cases[0])
    mcp_demo["fixture_plan"].append(step("external_policy", "mcp", {"domain": "retail"},
        ["calculation", "policy"], "Verify synthetic deployment policy via MCP protocol"))
    for name, payload in [("corpus.json", corpus), ("benchmark.json", cases), ("demo.json", cases[0]),
                          ("mcp-demo.json", mcp_demo)]:
        (directory / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
