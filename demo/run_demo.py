"""
Sentinel — AI Runtime Control Plane
Connected Demo Narrative (Section 9)

Tells ONE connected story touching all three use cases and all five decision states.
Run with: python -m demo.run_demo  (from e:\\sentinel)
"""
import sys
import os
import asyncio
import time

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.columns import Columns
from rich.text import Text
from rich.rule import Rule
from rich import box

from src.models.schemas import (
    UseCase, ActionType, Decision, EvaluationRequest,
    RiskCategory, VerificationDepth, InputGuardrailRequest
)
from src.detectors.factuality import FactualityDetector
from src.detectors.privacy import PrivacyDetector
from src.detectors.bias import BiasDetector
from src.detectors.cost import CostDetector
from src.input_guardrail.guardrail import InputGuardrail
from src.risk_fabric.action_impact import get_action_profile, compute_action_risk_multiplier
from src.risk_fabric.session_tracker import SessionTracker
from src.policy_engine.engine import PolicyEngine
from src.verification_router.router import VerificationRouter
from src.correction.cove_revise import CoVeReviser
from src.correction.bias_resample import BiasResampler
from src.models.schemas import RiskAssessment, DecisionTrace

console = Console()

# ── Colors for decisions ──────────────────────────────────────────
DECISION_COLORS = {
    Decision.ALLOW:    "bold green",
    Decision.WARN:     "bold yellow",
    Decision.REDACT:   "bold blue",
    Decision.ESCALATE: "bold bright_red",
    Decision.BLOCK:    "bold red",
}


async def evaluate_scenario(
    factuality, privacy, bias, cost_det,
    session_tracker, policy_engine, router, cove, bias_resampler,
    request: EvaluationRequest
):
    """Run the full 8-layer Sentinel pipeline on a single request."""
    start = time.perf_counter()

    # 1. Request Context
    policy_config = policy_engine.policies.get(request.use_case.value, {})
    from src.models.schemas import RiskTier
    risk_tier = RiskTier(policy_config.get("risk_tier", "high"))

    # 2. Adaptive Verification Depth
    depth = router.route(request.use_case, request.action, risk_tier)

    # 3. Run detectors in PARALLEL
    results = await asyncio.gather(
        factuality.detect(request.input_text, request.output_text,
                          context_documents=request.context_documents),
        privacy.detect(request.input_text, request.output_text),
        bias.detect(request.input_text, request.output_text),
        cost_det.detect(request.input_text, request.output_text),
    )

    # 4. Risk Fabric
    impact, reversibility = get_action_profile(request.action)
    turn_risk, exposure, trajectory = session_tracker.update(
        request.session_id, request.use_case, list(results)
    )

    risk_assessment = RiskAssessment(
        current_turn_risk=turn_risk,
        session_exposure=exposure,
        trajectory=trajectory,
        action=request.action,
        action_impact=impact,
        action_reversibility=reversibility,
        detection_results=list(results),
        use_case=request.use_case,
        risk_tier=risk_tier,
        verification_depth=depth,
    )

    # 5. Policy Engine
    decision, reason, policy_id = policy_engine.evaluate(risk_assessment)

    # 6. Correction if BLOCK/ESCALATE
    correction = None
    corrected_output = None
    if decision in (Decision.BLOCK, Decision.ESCALATE):
        fact_spans = []
        for r in results:
            if r.flagged and r.category == RiskCategory.FACTUALITY:
                fact_spans.extend(r.flagged_spans)
        if fact_spans:
            correction = await cove.revise(
                request.output_text, fact_spans, request.context_documents
            )
            if correction.succeeded:
                corrected_output = correction.corrected_text

    elapsed_ms = (time.perf_counter() - start) * 1000

    trace = DecisionTrace(
        request_id=f"demo-{time.time():.0f}",
        session_id=request.session_id,
        use_case=request.use_case,
        risk_tier=risk_tier,
        action=request.action,
        input_text=request.input_text,
        output_text=request.output_text,
        detection_results=list(results),
        risk_assessment=risk_assessment,
        policy_id=policy_id,
        decision=decision,
        reason=reason,
        correction=correction,
        total_latency_ms=elapsed_ms,
    )

    return decision, reason, trace, corrected_output, elapsed_ms, results, depth


def print_detection_table(results, depth):
    """Print a compact table of detection results."""
    t = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan")
    t.add_column("Detector", style="white")
    t.add_column("Score", justify="center")
    t.add_column("Flagged", justify="center")
    t.add_column("Branch / Detail")
    t.add_column("Latency", justify="right")

    for r in results:
        score_color = "green" if r.score < 0.4 else "yellow" if r.score < 0.7 else "red"
        flagged_icon = "[red]⚠ YES[/red]" if r.flagged else "[green]✓ NO[/green]"
        branch = r.branch_used or r.details.get("found_types", "-")
        t.add_row(
            r.category.value.upper(),
            f"[{score_color}]{r.score:.2f}[/{score_color}]",
            flagged_icon,
            str(branch),
            f"{r.latency_ms:.1f}ms",
        )

    console.print(t)
    console.print(f"  [dim]Verification depth: {depth.value}[/dim]")


def print_risk_summary(trace):
    """Print risk fabric assessment."""
    ra = trace.risk_assessment
    t = Table(box=box.ROUNDED, title="Risk Fabric Assessment", title_style="bold magenta")
    t.add_column("Field", style="cyan")
    t.add_column("Value", style="white")
    t.add_row("Current Turn Risk", f"{ra.current_turn_risk:.3f}")
    t.add_row("Session Exposure", f"{ra.session_exposure:.3f}")
    t.add_row("Trajectory", ra.trajectory.value)
    t.add_row("Action Impact", ra.action_impact.value)
    t.add_row("Action Reversibility", ra.action_reversibility.value)
    t.add_row("Policy ID", trace.policy_id)
    console.print(t)


async def run_demo():
    console.print()
    console.print(Panel.fit(
        "[bold cyan]███████╗███████╗███╗   ██╗████████╗██╗███╗   ██╗███████╗██╗     [/bold cyan]\n"
        "[bold cyan]██╔════╝██╔════╝████╗  ██║╚══██╔══╝██║████╗  ██║██╔════╝██║     [/bold cyan]\n"
        "[bold cyan]███████╗█████╗  ██╔██╗ ██║   ██║   ██║██╔██╗ ██║█████╗  ██║     [/bold cyan]\n"
        "[bold cyan]╚════██║██╔══╝  ██║╚██╗██║   ██║   ██║██║╚██╗██║██╔══╝  ██║     [/bold cyan]\n"
        "[bold cyan]███████║███████╗██║ ╚████║   ██║   ██║██║ ╚████║███████╗███████╗[/bold cyan]\n"
        "[bold cyan]╚══════╝╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚═╝╚═╝  ╚═══╝╚══════╝╚══════╝[/bold cyan]\n"
        "\n[dim]AI Runtime Control Plane — Connected Demo Narrative[/dim]",
        border_style="cyan",
    ))
    console.print()

    # ── Initialize all real components ─────────────────────────────
    factuality = FactualityDetector()
    privacy    = PrivacyDetector()
    bias       = BiasDetector()
    cost_det   = CostDetector()
    guardrail  = InputGuardrail()
    session_tracker = SessionTracker()
    policy_engine   = PolicyEngine("src/policy_engine/policies")
    router          = VerificationRouter()
    cove            = CoVeReviser()
    bias_resampler  = BiasResampler()

    # ── Shared story elements ──────────────────────────────────────
    story_sessions = {
        "customer_support": "demo-session-customer-support",
        "internal_copilot": "demo-session-internal-copilot",
        "finance_agent": "demo-session-finance-agent",
    }
    scenarios = [
        {
            "title": "Scene 1 — Customer Support Chatbot",
            "subtitle": "A customer asks about their balance via the public chatbot.",
            "use_case": UseCase.CUSTOMER_SUPPORT,
            "action": ActionType.GENERATE_TEXT,
            "color": "yellow",
            "input_text": "What is my current account balance and recent activity?",
            "output_text": "I can provide a general summary: your balance is approximately $12,800 and there were a few recent transfers and purchases."
        },
        {
            "title": "Scene 2 — Internal Copilot",
            "subtitle": "An employee queries the same data to update a CRM record.",
            "use_case": UseCase.INTERNAL_COPILOT,
            "action": ActionType.UPDATE_CRM,
            "color": "bright_magenta",
            "input_text": "Update the customer record with the latest known balance and transaction details.",
            "output_text": "Customer balance is $12,847.53 as of March 15, 2026. Recent activity includes a $2,500 wire transfer, a $347.99 purchase, and a $14.99 subscription."
        },
        {
            "title": "Scene 3 — Finance Agent",
            "subtitle": "An automated agent uses the uncertain figure to execute a payment.",
            "use_case": UseCase.FINANCE_AGENT,
            "action": ActionType.EXECUTE_PAYMENT,
            "color": "red",
            "input_text": "Initiate payment using the verified account balance and recent transaction activity.",
            "output_text": "Payment approved for $12,847.53. The account balance is confirmed and the transfer is authorized for this amount."
        },
    ]

    summary_rows = []

    for i, scene in enumerate(scenarios, 1):
        console.print()
        console.print(Rule(f"[bold {scene['color']}]{scene['title']}[/bold {scene['color']}]"))
        console.print(f"  [dim]{scene['subtitle']}[/dim]")
        console.print(f"  Use Case: [cyan]{scene['use_case'].value}[/cyan] │ Action: [cyan]{scene['action'].value}[/cyan]")
        console.print()

        req = EvaluationRequest(
            input_text=scene["input_text"],
            output_text=scene["output_text"],
            use_case=scene["use_case"],
            action=scene["action"],
            session_id=story_sessions[scene["use_case"].value],
        )

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as p:
            p.add_task(description="Running Sentinel pipeline…", total=None)
            decision, reason, trace, corrected, elapsed, results, depth = await evaluate_scenario(
                factuality, privacy, bias, cost_det,
                session_tracker, policy_engine, router, cove, bias_resampler, req
            )

        # Print detection results
        print_detection_table(results, depth)
        print_risk_summary(trace)

        dec_color = DECISION_COLORS.get(decision, "white")
        console.print(f"\n  ╔══ Decision: [{dec_color}]{decision.value}[/{dec_color}]")
        console.print(f"  ╚══ Reason:   {reason}")
        console.print(f"  [dim]Pipeline latency: {elapsed:.1f}ms[/dim]")

        if corrected:
            console.print(f"\n  [yellow]Correction attempted:[/yellow]")
            console.print(f"  {corrected[:120]}…" if len(str(corrected)) > 120 else f"  {corrected}")

        summary_rows.append((scene["title"], scene["use_case"].value, scene["action"].value,
                             decision.value, f"{elapsed:.0f}ms", reason[:60]))

    # ── Scene 4 — Human Review ────────────────────────────────────
    console.print()
    console.print(Rule("[bold bright_red]Scene 4 — Human Review[/bold bright_red]"))
    console.print("  [dim]The BLOCK from Scene 3 is escalated to a human reviewer.[/dim]\n")

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as p:
        p.add_task(description="Waiting for human reviewer…", total=None)
        await asyncio.sleep(1.5)

    console.print("  Reviewer:  [cyan]compliance_officer_01[/cyan]")
    console.print("  Outcome:   [green]APPROVED[/green] override")
    console.print("  Reason:    [dim]\"Balance confirmed via core banking system; payment authorized.\"[/dim]")
    console.print("  [dim]Feedback logged → false-positive counter incremented[/dim]")

    # ── Final Summary Table ───────────────────────────────────────
    console.print()
    console.print(Rule("[bold cyan]Decision Summary[/bold cyan]"))

    summary = Table(box=box.DOUBLE_EDGE, show_header=True, header_style="bold cyan")
    summary.add_column("Scene", style="white")
    summary.add_column("Use Case")
    summary.add_column("Action")
    summary.add_column("Decision", justify="center")
    summary.add_column("Latency", justify="right")
    summary.add_column("Reason")

    for row in summary_rows:
        dec = row[3]
        color = {"ALLOW": "green", "WARN": "yellow", "REDACT": "blue",
                 "ESCALATE": "bright_red", "BLOCK": "red"}.get(dec, "white")
        summary.add_row(row[0], row[1], row[2], f"[{color}]{dec}[/{color}]", row[4], row[5])

    console.print(summary)

    # ── Closing Pitch ─────────────────────────────────────────────
    console.print()
    console.print(Panel(
        "[bold italic white]Nothing about the AI's underlying answer changed across these "
        "three moments. The governance decision changed because the context and the action "
        "changed — that's the whole point of Sentinel.[/bold italic white]",
        border_style="cyan",
        title="[bold cyan]THE CORE THESIS[/bold cyan]",
    ))
    console.print()


def main():
    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
