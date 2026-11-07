How to read and safely edit a file longer than 10,000 characters, which read_file cuts off.

# Editing large files

`read_file` shows at most 10,000 characters. If you rewrite a file with
`write_file` from that partial copy, everything after the cut is deleted.

## 1. Measure and locate

- `wc -l path/to/file.py` tells you how many lines there are.
- `grep -n "def target_function" path/to/file.py` gives the line number of what you need.

## 2. Read it in pages

- `sed -n '1,200p' path/to/file.py` prints lines 1-200. Continue with `'201,400p'`, and so on.
- To read around a match on line 850: `sed -n '820,900p' path/to/file.py`.
- In --plan mode `sed` is not allowed; use `head -n 900 path/to/file.py | tail -n 80`.

## 3. Edit only the part that changes

Do not rewrite the whole file. Replace one exact snippet with a short Python
script that refuses to guess when the snippet is missing or appears twice:

```sh
python3 - <<'PY'
from pathlib import Path
path = Path("path/to/file.py")
text = path.read_text()
old = "    return a - b\n"
new = "    return a + b\n"
assert text.count(old) == 1, text.count(old)
path.write_text(text.replace(old, new))
PY
```

Only use `write_file` for the whole file when you have read every line of it.

## 4. Check the result

- `wc -l` again: the line count should only change by what you added or removed.
- `git diff path/to/file.py` should show only your change.
- Run the tests.
