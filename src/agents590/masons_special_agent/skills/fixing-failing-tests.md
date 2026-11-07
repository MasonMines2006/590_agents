How to find and fix a failing test quickly, within the 60-second shell timeout.

# Fixing failing tests

## 1. Reproduce one failure, fast

Shell commands are killed after 60 seconds, so do not start with the whole suite
if it is slow. Run the smallest thing that shows the failure:

- `python3 -m pytest -x -q` stops at the first failure.
- `python3 -m pytest -q tests/test_app.py::test_adds_numbers` runs one test.
- `python3 -m pytest -q -k "adds"` runs tests whose names match.

## 2. Read the traceback from the bottom up

The last lines say what went wrong (`AssertionError`, `KeyError`, ...). The
lines above them show where. Read the code at that line with `sed -n`.

## 3. Decide what is wrong: the code or the test

Fix the code under test. Only change a test when the task asks for it, or the
test clearly contradicts the task's instructions. Never delete or skip a test
to make the suite pass.

## 4. Verify

1. Re-run the single failing test until it passes.
2. Then run the full suite (`python3 -m pytest -q`, or `make test` if the repo
   has a Makefile), so the fix did not break something else.
3. In your final answer, say which tests you ran and whether they passed.
