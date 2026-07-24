"""Tests for the participant evaluation desktop environment."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pathlib import Path

from voice_agent.eval_env import (
    GOLDEN_DIR,
    WORKSPACE_DIR,
    get_task,
    load_tasks,
    reset_environment,
    seed_golden,
    seed_tasks,
)
from voice_agent.planner import RuleBasedPlanner


def test_seed_and_reset_restores_sample_files(tmp_path, monkeypatch):
    # Use project paths; reset should recreate Documents/invoice.pdf etc.
    seed_golden(force=True)
    seed_tasks(force=True)
    assert (GOLDEN_DIR / "Documents" / "invoice.pdf").exists()
    assert (GOLDEN_DIR / "Documents" / "budget.csv").exists()

    info = reset_environment()
    assert (WORKSPACE_DIR / "Documents" / "invoice.pdf").exists()
    assert (WORKSPACE_DIR / "Documents" / "meeting_notes.txt").exists()
    assert "Documents/invoice.pdf" in info["files"]

    # Mutate then reset
    victim = WORKSPACE_DIR / "Documents" / "meeting_notes.txt"
    victim.write_text("CHANGED", encoding="utf-8")
    reset_environment()
    assert "CHANGED" not in victim.read_text(encoding="utf-8")


def test_predefined_tasks_exist():
    tasks = load_tasks()
    ids = {t.id for t in tasks}
    assert "task_open_invoice" in ids
    assert "task_budget_total" in ids
    t = get_task("task_open_invoice")
    assert t is not None
    assert "invoice" in t.instruction.lower()


def test_planner_reset_environment():
    r = RuleBasedPlanner().plan("reset environment")
    assert r.plan is not None
    assert r.plan.steps[0].action.category == "reset_environment"


def test_planner_open_pdf_viewer():
    r = RuleBasedPlanner().plan("open pdf viewer")
    assert r.plan is not None
    assert r.plan.steps[0].action.category == "open_app"
    assert r.plan.steps[0].action.args["app"] in {"pdf viewer", "pdf"}


def test_planner_open_spreadsheet():
    r = RuleBasedPlanner().plan("open spreadsheet")
    assert r.plan is not None
    assert r.plan.steps[0].action.args["app"] == "spreadsheet"


def test_planner_open_invoice_pdf_is_file_not_url():
    reset_environment()
    r = RuleBasedPlanner().plan("open invoice.pdf")
    assert r.plan is not None
    step = r.plan.steps[0].action
    assert step.category == "open_file"
    assert step.args["path"].endswith("invoice.pdf")
    assert "Documents" in step.args["path"] or Path(step.args["path"]).exists()

