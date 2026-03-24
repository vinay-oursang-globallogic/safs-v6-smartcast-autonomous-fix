"""
SAFS CLI
========

Command-line interface for SAFS v6.0.

Usage:
    safs run --ticket SMART-12345 --logs /path/to/logs/
    safs setup --init-db
    safs test --scenario L-01
    safs monitor --proactive
"""

import typer
import asyncio
from rich.console import Console
from rich.table import Table
from typing import Optional
from pathlib import Path

app = typer.Typer(
    name="safs",
    help="SAFS v6.0 - SmartCast Autonomous Fix System CLI",
    add_completion=True,
)
console = Console()


@app.command()
def run(
    ticket: Optional[str] = typer.Option(
        None,
        "--ticket",
        "-t",
        help="Jira ticket key (e.g., SMART-12345)",
    ),
    logs: Optional[Path] = typer.Option(
        None,
        "--logs",
        "-l",
        help="Path to log files or directory",
    ),
    skip_validation: bool = typer.Option(
        False,
        "--skip-validation",
        help="Skip tri-path validation",
    ),
    skip_reproduction: bool = typer.Option(
        False,
        "--skip-reproduction",
        help="Skip bug reproduction",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Dry run mode (skip PR creation)",
    ),
) -> None:
    """Run the SAFS pipeline on a ticket."""
    if not ticket:
        console.print("[red]Error: --ticket is required[/red]")
        raise typer.Exit(1)
    
    if not logs:
        console.print("[red]Error: --logs is required[/red]")
        raise typer.Exit(1)
    
    log_path = Path(logs)
    if not log_path.exists():
        console.print(f"[red]Error: Log path does not exist: {log_path}[/red]")
        raise typer.Exit(1)
    
    # Collect log files
    if log_path.is_file():
        log_files = [log_path]
    else:
        log_files = list(log_path.glob("*.log")) + list(log_path.glob("*.txt"))
    
    if not log_files:
        console.print(f"[red]Error: No log files found in {log_path}[/red]")
        raise typer.Exit(1)
    
    console.print(f"[bold green]🚀 Starting SAFS v6.0 Pipeline[/bold green]")
    console.print(f"  Ticket: {ticket}")
    console.print(f"  Log files: {len(log_files)}")
    console.print(f"  Skip validation: {skip_validation}")
    console.print(f"  Skip reproduction: {skip_reproduction}")
    console.print(f"  Dry run: {dry_run}\n")
    
    # Run pipeline
    try:
        asyncio.run(_run_pipeline(
            ticket_key=ticket,
            log_files=log_files,
            skip_validation=skip_validation,
            skip_reproduction=skip_reproduction,
            dry_run=dry_run,
        ))
        console.print("\n[bold green]✅ Pipeline completed successfully![/bold green]")
    except Exception as e:
        console.print(f"\n[bold red]❌ Pipeline failed: {e}[/bold red]")
        raise typer.Exit(1)


async def _run_pipeline(
    ticket_key: str,
    log_files: list[Path],
    skip_validation: bool,
    skip_reproduction: bool,
    dry_run: bool,
) -> None:
    """Run the full SAFS pipeline."""
    from safs.agents.orchestrator import SAFSOrchestrator
    
    # Initialize orchestrator
    orchestrator = SAFSOrchestrator()
    
    # Run pipeline
    result = await orchestrator.run(
        ticket_key=ticket_key,
        log_files=log_files,
        skip_validation=skip_validation,
        skip_reproduction=skip_reproduction,
        dry_run=dry_run,
    )


@app.command()
def setup(
    init_db: bool = typer.Option(False, "--init-db", help="Initialize databases"),
    init_qdrant: bool = typer.Option(False, "--init-qdrant", help="Initialize Qdrant collections"),
    download_nltk: bool = typer.Option(False, "--download-nltk", help="Download NLTK data"),
    all: bool = typer.Option(False, "--all", help="Run all setup tasks"),
) -> None:
    """Setup SAFS environment."""
    if all:
        init_db = init_qdrant = download_nltk = True
    
    if init_db:
        console.print("[bold]Initializing PostgreSQL database...[/bold]")
        console.print("[yellow]⚠ Database initialization not implemented - use external DB setup[/yellow]")
    
    if init_qdrant:
        console.print("[bold]Initializing Qdrant collections...[/bold]")
        try:
            from safs.qdrant_collections.collection_setup import setup_collections
            setup_collections()
            console.print("[green]✓[/green] Qdrant collections created")
        except Exception as e:
            console.print(f"[red]✗[/red] Qdrant setup failed: {e}")
            console.print("[yellow]⚠ Use qdrant_collections/collection_setup.py directly if needed[/yellow]")
    
    if download_nltk:
        console.print("[bold]Downloading NLTK data...[/bold]")
        import nltk
        nltk.download('punkt', quiet=True)
        nltk.download('stopwords', quiet=True)
        console.print("[green]✓[/green] NLTK data downloaded")


@app.command()
def test(
    scenario: Optional[str] = typer.Option(None, "--scenario", "-s", help="Test scenario ID (e.g., L-01)"),
    category: str = typer.Option("all", "--category", "-c", help="Test category: unit, integration, e2e, all"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
) -> None:
    """Run SAFS tests."""
    console.print(f"[bold]Running {category} tests[/bold]")
    if scenario:
        console.print(f"Scenario: {scenario}")
    
    console.print("[yellow]⚠ Use 'pytest tests/{category}/' directly[/yellow]")
    console.print("   Examples:")
    console.print("     pytest tests/unit/")
    console.print("     pytest tests/integration/")
    console.print("     pytest tests/e2e/")


@app.command()
def monitor(
    proactive: bool = typer.Option(False, "--proactive", help="Run proactive monitoring"),
    regression: bool = typer.Option(False, "--regression", help="Run regression monitoring"),
    pr_id: Optional[str] = typer.Option(None, "--pr-id", help="Monitor specific PR"),
) -> None:
    """Run telemetry monitoring."""
    if proactive:
        console.print("[bold]Starting proactive telemetry monitoring...[/bold]")
        asyncio.run(_run_proactive_monitor())
    
    if regression:
        console.print("[bold]Starting regression monitoring...[/bold]")
        if not pr_id:
            console.print("[red]Error: --pr-id required for regression monitoring[/red]")
            raise typer.Exit(1)
        asyncio.run(_run_regression_monitor(pr_id))
    
    if pr_id and not regression:
        console.print(f"[yellow]Note: Use --regression with --pr-id to monitor PR regressions[/yellow]")


async def _run_proactive_monitor() -> None:
    """Run proactive telemetry monitoring cron job."""
    from safs.telemetry.proactive_monitor import ProactiveTelemetryMonitor
    
    monitor = ProactiveTelemetryMonitor()
    console.print("[cyan]Running proactive spike detection...[/cyan]")
    
    # Run monitoring check
    tickets = await monitor.check()
    
    if tickets:
        console.print(f"[red]⚠️  Created {len(tickets)} proactive tickets![/red]")
        for ticket in tickets:
            console.print(f"  - {ticket.jira_ticket_key}: {ticket.title}")
            console.print(f"    Spike: {ticket.spike_factor:.2f}x baseline, "
                        f"Affected users: {ticket.affected_users}")
    else:
        console.print("[green]✓ No anomalies detected[/green]")


async def _run_regression_monitor(pr_id: str) -> None:
    """Monitor for production regressions after PR merge."""
    from safs.telemetry.regression_correlator import ProductionRegressionCorrelator
    from safs.telemetry.models import MergedPR
    from datetime import datetime, timezone
    
    correlator = ProductionRegressionCorrelator()
    console.print(f"[cyan]Monitoring PR {pr_id} for regressions (72h window)...[/cyan]")
    
    # Create MergedPR object (in production this would come from GitHub API)
    merged_pr = MergedPR(
        pr_url=f"https://github.com/org/repo/pull/{pr_id}",
        pr_number=int(pr_id),
        ticket_id=f"SMART-{pr_id}",
        merged_at=datetime.now(timezone.utc),
        error_category="UNKNOWN",
        strategy="SURGICAL",
        confidence=0.8,
        repo="smartcast",
        branch="main",
    )
    
    # Monitor for regression
    result = await correlator.monitor(merged_pr)
    
    if result:
        console.print(f"[red]❌ Regression detected![/red]")
        console.print(f"  Error category: {result.error_category}")
        console.print(f"  Spike detected at: {result.detected_at}")
    else:
        console.print(f"[green]✓ No regression detected[/green]")



@app.command()
def status() -> None:
    """Show SAFS system status."""
    table = Table(title="SAFS v6.0 Status")
    table.add_column("Component", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Phase", style="yellow")
    
    components = [
        ("Project Structure", "✓ Complete", "Phase 0"),
        ("Data Models", "⏳ Pending", "Phase 1"),
        ("BugLayerRouter", "⏳ Pending", "Phase 2"),
        ("Log Quality Gate", "⏳ Pending", "Phase 3"),
        ("Log Intelligence", "⏳ Pending", "Phase 4"),
        ("Timestamp Extraction", "⏳ Pending", "Phase 5"),
        ("Symbolication", "⏳ Pending", "Phase 6-8"),
        ("Retrieval Router", "⏳ Pending", "Phase 9"),
        ("Qdrant Memory", "⏳ Pending", "Phase 10"),
        ("Context Builder", "⏳ Pending", "Phase 11"),
        ("Validators", "⏳ Pending", "Phase 12-15"),
        ("Fix Generator", "⏳ Pending", "Phase 16"),
        ("Confidence Ensemble", "⏳ Pending", "Phase 17"),
        ("PR Creator", "⏳ Pending", "Phase 18"),
        ("Jira Intake", "⏳ Pending", "Phase 19"),
        ("Test Generator", "⏳ Pending", "Phase 20"),
        ("Telemetry", "⏳ Pending", "Phase 21"),
        ("Orchestrator", "⏳ Pending", "Phase 22"),
    ]
    
    for name, status, phase in components:
        table.add_row(name, status, phase)
    
    console.print(table)


@app.command()
def version() -> None:
    """Show SAFS version."""
    from safs import __version__
    console.print(f"[bold]SAFS v{__version__}[/bold]")
    console.print("SmartCast Autonomous Fix System")
    console.print("Built with ❤️ by Vizio Engineering")


if __name__ == "__main__":
    app()
