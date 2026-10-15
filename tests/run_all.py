"""Run the application's test suites and say plainly whether everything passed.

    python tests/run_all.py               every suite, three at a time
    python tests/run_all.py pdf level     only the suites whose name holds one of these words
    python tests/run_all.py --one-by-one  one suite at a time (slower, lighter for the PC)
    python tests/run_all.py --pages       also open every page on a copy of today's data (check_pages.py)

Each suite works on its own throwaway copy of the database (see testenv.py):
nothing is sent and app.db is never changed. A suite takes 20 to 40 seconds
(most of it loading the language libraries); the whole run, about 3 minutes.
The full output of a suite that failed is kept in the temp folder; its path is printed.
"""
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import testenv

SUMMARY = re.compile(r"(\d+)\s*(?:of|/)\s*(\d+)\s+(?:checks\s+)?passed")


def run(path: str) -> dict:
    name = os.path.splitext(os.path.basename(path))[0]
    started = time.time()
    environment = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=testenv.TESTS)
    try:
        done = subprocess.run([sys.executable, "-u", path], cwd=testenv.PROJECT, env=environment, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=45 * 60)
        output, code = done.stdout + done.stderr, done.returncode
    except subprocess.TimeoutExpired as late:
        output, code = f"{late.stdout or ''}\nstopped after 45 minutes", -1
    found = SUMMARY.findall(output)
    passed, total = (int(found[-1][0]), int(found[-1][1])) if found else (0, 0)
    fails = [line for line in output.splitlines() if line.startswith("FAIL")]
    ok = code == 0 and total > 0 and passed == total
    log = None
    if not ok:
        log = os.path.join(testenv.WORK, "logs", f"{name}.log")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "w", encoding="utf-8") as handle:
            handle.write(output)
    return {"name": name, "ok": ok, "passed": passed, "total": total, "fails": fails, "log": log,
            "seconds": time.time() - started, "tail": output.strip().splitlines()[-3:]}


def main(arguments: list[str]) -> int:
    words = [word for word in arguments if not word.startswith("--")]
    suites = sorted(os.path.join(testenv.TESTS, name) for name in os.listdir(testenv.TESTS)
                    if name.startswith("test_") and name.endswith(".py"))
    if words:
        suites = [path for path in suites if any(word in os.path.basename(path) for word in words)]
    if "--pages" in arguments:
        suites.append(os.path.join(testenv.TESTS, "check_pages.py"))
    if not suites:
        print("No suite matches", " ".join(words))
        return 2
    testenv.ensure_baseline()           # once, before the suites start side by side
    at_once = 1 if "--one-by-one" in arguments else 3
    print(f"Running {len(suites)} suite(s), {at_once} at a time, on copies in {testenv.WORK}\n", flush=True)

    results = []
    with ThreadPoolExecutor(max_workers=at_once) as pool:
        for future in as_completed([pool.submit(run, path) for path in suites]):
            result = future.result()
            results.append(result)
            mark = "ok  " if result["ok"] else "FAIL"
            count = f"{result['passed']} of {result['total']}" if result["total"] else "no result"
            print(f"  {mark} {result['name']:<20} {count:>14} checks   {result['seconds']:5.0f} s", flush=True)

    failed = [result for result in results if not result["ok"]]
    checks = sum(result["total"] for result in results)
    passed = sum(result["passed"] for result in results)
    print(f"\n{passed} of {checks} checks passed in {len(results)} suite(s).")
    for result in failed:
        print(f"\n{result['name']} did not pass. Full output: {result['log']}")
        for line in (result["fails"] or result["tail"])[:15]:
            print("   ", line)
    print("\nEverything passed." if not failed else f"\n{len(failed)} suite(s) need a look.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
