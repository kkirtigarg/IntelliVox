"""Domain allowlist checks for browser navigation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from voice_agent.models import Action, PolicyContext
from voice_agent.policy_engine import PolicyEngine


def test_google_url_allowed():
    engine = PolicyEngine.load()
    action = Action(
        category="browser_navigate",
        args={"url": "https://www.google.com", "browser": "firefox"},
        justification="test",
    )
    ctx = PolicyContext(
        app_allowlist=("firefox",),
        domain_allowlist=("google.com", "www.google.com"),
    )
    decision = engine.evaluate(action, ctx)
    assert decision.allowed, decision.reason
