"""
Aether — AI Runtime Control Plane
Connected demo narrative.

One story across three use cases -- the same uncertain balance figure escalating from
a chat reply to a CRM write to a payment -- then a real human review of what the
pipeline stopped.

It reaches ALLOW and ESCALATE, not all five states, because that is what these three
requests actually produce. Wording the scenarios until the ladder lit up end to end
would be staging the demo rather than running it; `python -m evals.run` exercises all
five against labelled cases.

    python -m demo.run_demo

Every number on screen comes from the gateway. This file used to carry its own copy
of the pipeline -- detectors, risk fabric, policy, correction, trace assembly -- and
that copy had drifted: four detectors instead of six, no input screening, no latency
budget, no fail-mode handling, and no re-verification of a correction. It demonstrated
a system the API did not implement. It now posts to the app, so what it shows is what
a caller gets.

Point it at a running gateway with AETHER_DEMO_API_BASE=http://localhost:8000;
with that unset it drives the ASGI app in-process and needs no server.
"""
import asyncio
import contextlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.table import Table

console = Console()

DECISION_COLORS = {
    "ALLOW": "green",
    "WARN": "yellow",
    "REDACT": "blue",
    "ESCALATE": "bright_red",
    "BLOCK": "red",
}

SCENARIOS = [
    {
        "title": "Scene 1 — Customer Support Chatbot",
        "subtitle": "A customer asks about their balance via the public chatbot.",
        "use_case": "customer_support",
        "action": "generate_text",
        "color": "yellow",
        "input_text": "What is my current account balance and recent activity?",
        "output_text": "I can provide a general summary: your balance is approximately "
                       "$12,800 and there were a few recent transfers and purchases.",
    },
    {
        "title": "Scene 2 — Internal Copilot",
        "subtitle": "An employee queries the same data to update a CRM record.",
        "use_case": "internal_copilot",
        "action": "update_crm",
        "color": "bright_magenta",
        "input_text": "Update the customer record with the latest known balance and "
                      "transaction details.",
        "output_text": "Customer balance is $12,847.53 as of March 15, 2026. Recent "
                       "activity includes a $2,500 wire transfer, a $347.99 purchase, "
                       "and a $14.99 subscription.",
    },
    {
        "title": "Scene 3 — Finance Agent",
        "subtitle": "An automated agent uses the uncertain figure to execute a payment.",
        "use_case": "finance_agent",
        "action": "execute_payment",
        "color": "red",
        "input_text": "Initiate payment using the verified account balance and recent "
                      "transaction activity.",
        "output_text": "Payment approved for $12,847.53. The account balance is confirmed "
                       "and the transfer is authorized for this amount.",
    },
]


@contextlib.asynccontextmanager
async def open_client():
    """A client for a running gateway, or for the app itself when none is running.

    In-process, the app's lifespan has to be entered explicitly -- ASGITransport does
    not run it, and startup is where the audit tables are created.
    """
    headers = {}
    if key := os.environ.get("AETHER_API_KEY", ""):
        headers["X-API-Key"] = key

    if base := os.environ.get("AETHER_DEMO_API_BASE", ""):
        async with httpx.AsyncClient(base_url=base.rstrip("/"), headers=headers,
                                     timeout=60) as client:
            yield client
        return

    from src.main import app
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://demo",
            headers=headers, timeout=60,
        ) as client:
            yield client


def print_detection_table(trace):
    t = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan")
    t.add_column("Detector", style="white")
    t.add_column("Score", justify="center")
    t.add_column("Flagged", justify="center")
    t.add_column("Branch / Detail")
    t.add_column("Latency", justify="right")

    for r in trace["detection_results"]:
        score = r["score"]
        color = "green" if score < 0.4 else "yellow" if score < 0.7 else "red"
        branch = r.get("branch_used") or r["details"].get("found_types") or "-"
        t.add_row(
            r["category"].upper(),
            f"[{color}]{score:.2f}[/{color}]",
            "[red]⚠ YES[/red]" if r["flagged"] else "[green]✓ NO[/green]",
            str(branch),
            f"{r['latency_ms']:.1f}ms",
        )

    console.print(t)
    console.print(f"  [dim]Verification depth: "
                  f"{trace['risk_assessment']['verification_depth']}[/dim]")


def print_risk_summary(trace):
    ra = trace["risk_assessment"]
    t = Table(box=box.ROUNDED, title="Risk Fabric Assessment", title_style="bold magenta")
    t.add_column("Field", style="cyan")
    t.add_column("Value", style="white")
    t.add_row("Current Turn Risk", f"{ra['current_turn_risk']:.3f}")
    t.add_row("Session Exposure", f"{ra['session_exposure']:.3f}")
    t.add_row("Trajectory", ra["trajectory"])
    t.add_row("Action Impact", ra["action_impact"])
    t.add_row("Action Reversibility", ra["action_reversibility"])
    t.add_row("Policy ID", trace["policy_id"])
    if trace["failed_detectors"]:
        t.add_row("Failed Detectors", ", ".join(trace["failed_detectors"]))
    console.print(t)


async def run_demo():
    console.print()
    console.print(Panel.fit(
        "[bold cyan] █████  ███████ ████████ ██   ██ ███████ ██████ [/bold cyan]\n"
        "[bold cyan]██   ██ ██         ██    ██   ██ ██      ██   ██[/bold cyan]\n"
        "[bold cyan]███████ █████      ██    ███████ █████   ██████ [/bold cyan]\n"
        "[bold cyan]██   ██ ██         ██    ██   ██ ██      ██   ██[/bold cyan]\n"
        "[bold cyan]██   ██ ███████    ██    ██   ██ ███████ ██   ██[/bold cyan]\n"
        "\n[dim]AI Runtime Control Plane — Connected Demo Narrative[/dim]",
        border_style="cyan",
    ))
    console.print()

    summary_rows = []
    escalated = None

    async with open_client() as client:
        for scene in SCENARIOS:
            console.print()
            console.print(Rule(f"[bold {scene['color']}]{scene['title']}[/bold {scene['color']}]"))
            console.print(f"  [dim]{scene['subtitle']}[/dim]")
            console.print(f"  Use Case: [cyan]{scene['use_case']}[/cyan] │ "
                          f"Action: [cyan]{scene['action']}[/cyan]")
            console.print()

            with Progress(SpinnerColumn(),
                          TextColumn("[progress.description]{task.description}"),
                          transient=True) as p:
                p.add_task(description="POST /api/evaluate…", total=None)
                response = await client.post("/api/evaluate", json={
                    "input_text": scene["input_text"],
                    "output_text": scene["output_text"],
                    "use_case": scene["use_case"],
                    "action": scene["action"],
                    "session_id": f"demo-session-{scene['use_case']}",
                })
                response.raise_for_status()
                payload = response.json()

            trace = payload["trace"]
            decision = payload["decision"]
            latency = trace["total_latency_ms"]

            print_detection_table(trace)
            print_risk_summary(trace)

            color = DECISION_COLORS.get(decision, "white")
            console.print(f"\n  ╔══ Decision: [{color}]{decision}[/{color}]")
            console.print(f"  ╚══ Reason:   {payload['reason']}")
            console.print(f"  [dim]Pipeline latency: {latency:.1f}ms[/dim]")

            if payload.get("corrected_output"):
                corrected = payload["corrected_output"]
                console.print("\n  [yellow]Correction applied and re-verified:[/yellow]")
                console.print(f"  {corrected[:120]}…" if len(corrected) > 120 else f"  {corrected}")

            if decision in ("BLOCK", "ESCALATE") and escalated is None:
                escalated = (trace["trace_id"], decision, scene["title"])

            summary_rows.append((scene["title"], scene["use_case"], scene["action"],
                                 decision, f"{latency:.0f}ms", payload["reason"][:60]))

        # ── Scene 4 — Human review, actually submitted ──────────────────────
        console.print()
        console.print(Rule("[bold bright_red]Scene 4 — Human Review[/bold bright_red]"))

        if escalated is None:
            console.print("  [dim]Nothing was stopped, so there is nothing to review.[/dim]")
        else:
            trace_id, decision, origin = escalated
            console.print(f"  [dim]The {decision} from {origin} goes to a reviewer. This "
                          f"posts to /api/review -- the verdict is appended to the audit "
                          f"chain, not printed.[/dim]\n")

            review = await client.post("/api/review", json={
                "trace_id": trace_id,
                "approved": True,
                "reviewer_id": "compliance_officer_01",
                "reason": "Balance confirmed via core banking system; payment authorized.",
            })
            review.raise_for_status()
            outcome = review.json()

            console.print(f"  Trace:     [dim]{trace_id}[/dim]")
            console.print("  Reviewer:  [cyan]compliance_officer_01[/cyan]")
            console.print(f"  Outcome:   [green]{outcome['review_outcome']}[/green] override "
                          f"of {outcome['original_decision']}")

            verify = await client.get("/api/audit/verify")
            verify.raise_for_status()
            chain = verify.json()
            state = "[green]intact[/green]" if chain["intact"] else "[red]BROKEN[/red]"
            console.print(f"  Audit:     {state} over {chain['rows_checked']} chained rows")

            stats = (await client.get("/api/stats")).json()
            console.print(f"  [dim]Approving a stopped response counts it as a false "
                          f"positive: now {stats['false_positive_count']}.[/dim]")

    # ── Summary ────────────────────────────────────────────────────────────
    console.print()
    console.print(Rule("[bold cyan]Decision Summary[/bold cyan]"))

    summary = Table(box=box.DOUBLE_EDGE, show_header=True, header_style="bold cyan")
    for column, justify in (("Scene", "left"), ("Use Case", "left"), ("Action", "left"),
                            ("Decision", "center"), ("Latency", "right"), ("Reason", "left")):
        summary.add_column(column, justify=justify)

    for title, use_case, action, decision, latency, reason in summary_rows:
        color = DECISION_COLORS.get(decision, "white")
        summary.add_row(title, use_case, action, f"[{color}]{decision}[/{color}]",
                        latency, reason)

    console.print(summary)

    console.print()
    console.print(Panel(
        "[bold italic white]Nothing about the AI's underlying answer changed across these "
        "three moments. The governance decision changed because the context and the action "
        "changed — that's the whole point of Aether.[/bold italic white]",
        border_style="cyan",
        title="[bold cyan]THE CORE THESIS[/bold cyan]",
    ))
    console.print()


def main():
    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
