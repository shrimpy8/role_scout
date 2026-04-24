"""T37: Verify run.py hardcodes host=127.0.0.1 for --serve mode."""
import inspect
import re


def test_serve_host_hardcoded():
    """T37: run.py --serve must hardcode 127.0.0.1, never actually bind to 0.0.0.0.

    Strips comments and docstrings before checking so that prohibition comments
    (e.g. "# never 0.0.0.0") don't cause false positives.
    """
    import run  # project root run.py
    source = inspect.getsource(run)

    # Remove single-line comments to avoid false positives from prohibition comments
    source_no_comments = re.sub(r"#[^\n]*", "", source)
    # Remove docstrings (triple-quoted strings)
    source_no_comments = re.sub(r'""".*?"""', "", source_no_comments, flags=re.DOTALL)
    source_no_comments = re.sub(r"'''.*?'''", "", source_no_comments, flags=re.DOTALL)

    assert "0.0.0.0" not in source_no_comments, (
        "run.py must never bind to 0.0.0.0 in executable code"
    )
    assert "127.0.0.1" in source, "run.py must hardcode 127.0.0.1"
