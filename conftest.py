"""Root pytest config — keep collection out of operational tools.

tools/notify_test.py matches the default *_test.py collection pattern,
so a bare `pytest` run collects and EXECUTES it — and its functions send
real Discord notifications on any box with a webhook configured (they
did, during the droplet P4 deploy runs). It is an operator CLI tool, not
a test module.
"""

collect_ignore = ["tools/notify_test.py"]
