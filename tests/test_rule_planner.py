"""Offline rule-based planner coverage (no API key, no network)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from voice_agent.planner import Planner, RuleBasedPlanner


def test_open_notepad():
    r = RuleBasedPlanner().plan("open notepad")
    assert r.plan is not None
    assert r.plan.steps[0].action.category == "open_app"
    assert r.plan.steps[0].action.args["app"] == "notepad"


def test_list_windows():
    r = RuleBasedPlanner().plan("list windows")
    assert r.plan is not None
    assert r.plan.steps[0].action.category == "read_window_titles"


def test_screenshot():
    r = RuleBasedPlanner().plan("take a screenshot")
    assert r.plan is not None
    assert r.plan.steps[0].action.category == "screenshot"


def test_delete_file_path():
    r = RuleBasedPlanner().plan("delete the file at C:/report.docx")
    assert r.plan is not None
    step = r.plan.steps[0].action
    assert step.category == "file_delete"
    assert "report.docx" in step.args["path"]


def test_write_file():
    r = RuleBasedPlanner().plan('write "hello" to C:/tmp/note.txt')
    assert r.plan is not None
    step = r.plan.steps[0].action
    assert step.category == "file_write_new"
    assert step.args["content"] == "hello"
    assert "note.txt" in step.args["path"]


def test_browser_navigate():
    r = RuleBasedPlanner().plan("go to https://wikipedia.org")
    assert r.plan is not None
    step = r.plan.steps[0].action
    assert step.category == "browser_navigate"
    assert "wikipedia.org" in step.args["url"]


def test_open_firefox_and_search_for_google():
    r = RuleBasedPlanner().plan("open firefox and search for google")
    assert r.plan is not None
    assert len(r.plan.steps) == 2
    assert r.plan.steps[0].action.category == "open_app"
    assert r.plan.steps[0].action.args["app"] == "firefox"
    step = r.plan.steps[1].action
    assert step.category == "browser_navigate"
    assert "google.com" in step.args["url"]
    assert step.args.get("browser") == "firefox"


def test_open_google_com():
    r = RuleBasedPlanner().plan("open google.com")
    assert r.plan is not None
    step = r.plan.steps[0].action
    assert step.category == "browser_navigate"
    assert "google.com" in step.args["url"]
    assert step.args.get("browser") == "firefox"


def test_seach_typo_for_google():
    r = RuleBasedPlanner().plan("seach for google.com")
    assert r.plan is not None
    step = r.plan.steps[0].action
    assert step.category == "browser_navigate"
    assert "google.com" in step.args["url"]


def test_open_firefox_and_open_google_com():
    r = RuleBasedPlanner().plan("open firefox and open google.com")
    assert r.plan is not None
    assert len(r.plan.steps) == 2
    assert r.plan.steps[0].action.category == "open_app"
    assert r.plan.steps[1].action.category == "browser_navigate"
    assert r.plan.steps[1].action.args.get("browser") == "firefox"


def test_search_for_cats_on_google_strips_site():
    r = RuleBasedPlanner().plan("search for cats on google")
    assert r.plan is not None
    url = r.plan.steps[0].action.args["url"]
    assert "google.com/search" in url
    assert "q=cats" in url
    assert "on+google" not in url


def test_search_google_for_dogs():
    r = RuleBasedPlanner().plan("search google for dogs")
    assert r.plan is not None
    assert "q=dogs" in r.plan.steps[0].action.args["url"]


def test_open_google_and_search_for_python():
    r = RuleBasedPlanner().plan("open google and search for python")
    assert r.plan is not None
    urls = [
        s.action.args.get("url", "")
        for s in r.plan.steps
        if s.action.category == "browser_navigate"
    ]
    assert any("search?q=python" in u for u in urls)


def test_open_firefox_and_search_for_multiword():
    r = RuleBasedPlanner().plan("open firefox and search for weather in london")
    assert r.plan is not None
    assert r.plan.steps[0].action.category == "open_app"
    nav = [s for s in r.plan.steps if s.action.category == "browser_navigate"]
    assert nav
    assert "weather" in nav[0].action.args["url"]
    assert "london" in nav[0].action.args["url"]


def test_open_vscode_and_create_file():
    r = RuleBasedPlanner().plan("open vscode and create a file with name app.py")
    assert r.plan is not None
    cats = [s.action.category for s in r.plan.steps]
    assert cats[0] == "open_app"
    assert r.plan.steps[0].action.args["app"] == "vscode"
    assert "file_write_new" in cats
    write = next(s for s in r.plan.steps if s.action.category == "file_write_new")
    assert write.action.args["path"].endswith("app.py")
    assert "open_file" in cats


def test_create_file_with_name():
    r = RuleBasedPlanner().plan("create a file named notes.txt")
    assert r.plan is not None
    step = r.plan.steps[0].action
    assert step.category == "file_write_new"
    assert step.args["path"].endswith("notes.txt")


def test_chain_split_three_steps():
    r = RuleBasedPlanner().plan(
        'open notepad and create a file named todo.txt and type "hello"'
    )
    assert r.plan is not None
    cats = [s.action.category for s in r.plan.steps]
    assert cats[0] == "open_app"
    assert "file_write_new" in cats
    assert "gui_type" in cats



def test_facade_rules_backend_no_api_key():
    p = Planner(backend="rules")
    r = p.plan("open chrome")
    assert r.plan is not None
    assert r.clarification_question is None


def test_unknown_asks_clarification_without_mentioning_api_key():
    r = RuleBasedPlanner().plan("reticulate the splines")
    assert r.plan is None
    assert r.clarification_question
    assert "ANTHROPIC" not in r.clarification_question


def test_open_first_link_needs_memory():
    r = RuleBasedPlanner().plan("open the first link on the google")
    assert r.plan is None
    assert r.clarification_question
    assert "remember" in r.clarification_question.lower()


def test_open_first_link_with_memory():
    r = RuleBasedPlanner().plan(
        "Now open the first link on the google",
        memory={"last_search_query": "best football team in the world"},
    )
    assert r.plan is not None
    step = r.plan.steps[0].action
    assert step.category == "open_search_result"
    assert step.args["index"] == 1
    assert step.args["query"] == "best football team in the world"


def test_open_second_result_with_memory():
    r = RuleBasedPlanner().plan(
        "open the second search result",
        memory={"last_search_query": "cats"},
    )
    assert r.plan is not None
    assert r.plan.steps[0].action.args["index"] == 2


def test_open_first_link_recovers_query_from_url():
    r = RuleBasedPlanner().plan(
        "open the first link",
        memory={
            "last_url": "https://www.google.com/search?q=best+football+team+in+the+world",
        },
    )
    assert r.plan is not None
    assert "football" in r.plan.steps[0].action.args["query"]
