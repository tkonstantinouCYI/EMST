"""Persist analytical regression evidence, including the active solver backend."""
import contextlib,io,json,platform,unittest
from pathlib import Path
from emst import optimization


def main():
    project=Path(__file__).resolve().parents[1]
    suite=unittest.defaultTestLoader.discover(str(project/"tests"))
    log=io.StringIO()
    with contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
        result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    backend="highs" if optimization.highspy is not None else "cbc"
    summary=dict(passed=result.wasSuccessful(),tests=result.testsRun,skipped=len(result.skipped),failures=len(result.failures),
                 errors=len(result.errors),backend=backend,python=platform.python_version())
    output=project/"output"
    output.mkdir(exist_ok=True)
    (output/f"unit-tests-{backend}.json").write_text(json.dumps(summary,indent=2))
    (output/f"unit-tests-{backend}.log").write_text(log.getvalue())
    print(json.dumps(summary,indent=2))
    if not result.wasSuccessful():raise SystemExit(1)


if __name__=="__main__":main()
