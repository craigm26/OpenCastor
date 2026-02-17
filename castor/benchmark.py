"""
OpenCastor Benchmark -- profile a single perception-action loop iteration.

Measures each phase individually so users can identify bottlenecks:
  - Camera capture time
  - Provider inference time
  - Driver execution time
  - Total loop overhead

Usage:
    castor benchmark --config robot.rcan.yaml
    castor benchmark --config robot.rcan.yaml --iterations 5
"""

import logging
import time

import yaml

logger = logging.getLogger("OpenCastor.Benchmark")


def run_benchmark(config_path: str, iterations: int = 3, simulate: bool = False):
    """Run a perception-action benchmark and print results."""
    with open(config_path) as f:
        config = yaml.safe_load(f)

    robot_name = config.get("metadata", {}).get("robot_name", "Robot")

    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        has_rich = True
    except ImportError:
        has_rich = False

    if has_rich:
        console.print(f"\n[bold cyan]  OpenCastor Benchmark[/] -- {robot_name}")
        console.print(f"  Iterations: {iterations}\n")
    else:
        print(f"\n  OpenCastor Benchmark -- {robot_name}")
        print(f"  Iterations: {iterations}\n")

    # Initialize components
    from castor.providers import get_provider
    from castor.main import Camera, get_driver

    print("  Initializing components...")

    brain = get_provider(config["agent"])
    camera = Camera(config)
    driver = get_driver(config) if not simulate else None

    results = []

    for i in range(iterations):
        timings = {}
        print(f"\n  --- Iteration {i + 1}/{iterations} ---")

        # Phase 1: Camera capture
        t0 = time.perf_counter()
        frame = camera.capture_jpeg()
        timings["capture_ms"] = (time.perf_counter() - t0) * 1000

        # Phase 2: Provider inference
        t0 = time.perf_counter()
        thought = brain.think(frame, "Describe what you see in one sentence.")
        timings["inference_ms"] = (time.perf_counter() - t0) * 1000

        # Phase 3: Driver execution (if available)
        if driver and thought.action:
            t0 = time.perf_counter()
            action_type = thought.action.get("type", "")
            if action_type == "move":
                driver.move(
                    thought.action.get("linear", 0),
                    thought.action.get("angular", 0),
                )
                time.sleep(0.1)
                driver.stop()
            timings["driver_ms"] = (time.perf_counter() - t0) * 1000
        else:
            timings["driver_ms"] = 0.0

        timings["total_ms"] = (
            timings["capture_ms"] + timings["inference_ms"] + timings["driver_ms"]
        )
        timings["frame_size"] = len(frame)
        timings["thought_len"] = len(thought.raw_text)

        results.append(timings)

    # Cleanup
    if driver:
        driver.stop()
        driver.close()
    camera.close()

    # Print results
    _print_results(results, config, has_rich, console if has_rich else None)


def _print_results(results, config, has_rich, console):
    """Print benchmark results as a summary table."""
    n = len(results)
    avg = lambda key: sum(r[key] for r in results) / n

    avg_capture = avg("capture_ms")
    avg_inference = avg("inference_ms")
    avg_driver = avg("driver_ms")
    avg_total = avg("total_ms")
    budget = config.get("agent", {}).get("latency_budget_ms", 3000)

    if has_rich:
        from rich.table import Table

        table = Table(title="Benchmark Results", show_header=True)
        table.add_column("Phase", style="bold")
        table.add_column("Avg (ms)", justify="right")
        table.add_column("Min (ms)", justify="right")
        table.add_column("Max (ms)", justify="right")

        for label, key in [
            ("Camera Capture", "capture_ms"),
            ("AI Inference", "inference_ms"),
            ("Driver Exec", "driver_ms"),
            ("Total", "total_ms"),
        ]:
            vals = [r[key] for r in results]
            table.add_row(
                label,
                f"{sum(vals)/n:.1f}",
                f"{min(vals):.1f}",
                f"{max(vals):.1f}",
            )

        console.print()
        console.print(table)

        status = "[green]PASS[/]" if avg_total < budget else "[red]OVER BUDGET[/]"
        console.print(
            f"\n  Latency budget: {budget}ms | Average: {avg_total:.0f}ms | {status}"
        )
        console.print(
            f"  Model: {config.get('agent', {}).get('model', '?')} | "
            f"Frame: {avg('frame_size'):.0f} bytes\n"
        )
    else:
        print(f"\n  {'Phase':<20} {'Avg':>8} {'Min':>8} {'Max':>8}")
        print(f"  {'-'*20} {'-'*8} {'-'*8} {'-'*8}")
        for label, key in [
            ("Camera Capture", "capture_ms"),
            ("AI Inference", "inference_ms"),
            ("Driver Exec", "driver_ms"),
            ("Total", "total_ms"),
        ]:
            vals = [r[key] for r in results]
            print(
                f"  {label:<20} {sum(vals)/n:>7.1f} {min(vals):>7.1f} {max(vals):>7.1f}"
            )

        status = "PASS" if avg_total < budget else "OVER BUDGET"
        print(f"\n  Latency budget: {budget}ms | Average: {avg_total:.0f}ms | {status}")
        print(
            f"  Model: {config.get('agent', {}).get('model', '?')} | "
            f"Frame: {avg('frame_size'):.0f} bytes\n"
        )
