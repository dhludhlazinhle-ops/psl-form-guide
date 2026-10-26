from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def read(name):
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_the_weekly_refresh_can_also_be_started_by_hand():
    text = read("refresh.yml")
    assert "schedule:" in text and "cron:" in text
    assert "workflow_dispatch:" in text


def test_the_refresh_uses_the_cached_warehouse_and_the_auto_mode():
    text = read("refresh.yml")
    assert "actions/cache/restore" in text and "actions/cache/save" in text
    assert "python -m src.pipeline --auto --export" in text


def test_the_refresh_may_commit_and_ask_pages_to_rebuild():
    text = read("refresh.yml")
    assert "contents: write" in text
    assert "pages: write" in text
    assert "pages/builds" in text


def test_the_refresh_commit_does_not_skip_the_pages_build():
    # "[skip ci]" in a commit message would also skip GitHub's own Pages build.
    assert "skip ci" not in read("refresh.yml").lower()


def test_the_tests_still_run_on_every_push():
    text = read("ci.yml")
    assert "pytest" in text and "push:" in text
