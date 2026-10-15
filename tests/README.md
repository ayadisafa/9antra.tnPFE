# Tests

19 suites check the application (about 1,300 checks): candidates, classes and their rules,
payments and reminders, level checks and behaviour, emails and the email assistant, the chat
assistant, PDFs, the website, the job market, sign-in and access, translations, copies of the data.

## Run them

From the application folder (`D:\Work\9antra`):

```
python tests/run_all.py               every suite, three at a time (about 3 minutes)
python tests/run_all.py pdf level     only the suites whose name holds one of these words
python tests/run_all.py --pages       also open every page on a copy of today's data
python tests/check_pages.py           only that last check (about 2 minutes)
```

The last line says `Everything passed.` or names the suite that did not pass, with the path of its full output.

## They are safe to run while the application is open

`testenv.py` runs first in every suite. It works on a copy of the database in the Windows temp
folder, empties every password and key, and makes any email or web request fail. Nothing is sent,
and `app.db` is only ever read.

## The test database

The suites were written against the database of 13 October 2026. The first run takes it out of
`_backups/pre-level-check-20261013.zip` and keeps it in `tests/data/baseline.db`. It holds real
people's data, so it is never put in git (see `.gitignore`). `check_pages.py` uses a copy of
today's `app.db` instead.

## Adding a check

```python
import testenv
testenv.prepare("my_feature")          # before importing anything from app
check = testenv.Checks()

from app.database import SessionLocal
...
check("what should be true", condition, "what to show when it is not")
check.done()
```

Name the file `tests/test_<something>.py` and `run_all.py` picks it up.
