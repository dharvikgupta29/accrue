"""Swap the budget accrue enforces, for experiments that change the policy.

Kept apart from stress.py so it imports nothing but src/ - CI can test it
without installing the ML packages (tests/test_policy_swap.py).
"""

import tempfile
from contextlib import contextmanager
from pathlib import Path

from src import authorize

from .benchmark import BUDGET


@contextmanager
def budget_policy(budget):
    """Temporarily point authorize.py at a copy of budget.cedar with a
    different cap. This is the whole cost of a policy change for accrue:
    edit one number. A learned guard has to be retrained."""
    if budget == BUDGET:
        yield
        return
    original = authorize.POLICY_PATH
    with tempfile.TemporaryDirectory() as tmp:
        patched = Path(tmp) / "budget.cedar"
        text = original.read_text()
        old = f"context.cumulative_cost > {BUDGET}"
        assert old in text, "budget.cedar no longer has the expected forbid"
        patched.write_text(text.replace(old, f"context.cumulative_cost > {budget}"))
        authorize.POLICY_PATH = patched
        try:
            yield
        finally:
            authorize.POLICY_PATH = original
