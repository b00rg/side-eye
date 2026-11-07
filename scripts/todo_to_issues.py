"""Turn TODO.md tasks (T1, T2, ...) into GitHub issues, assigned and labelled.

  python3 scripts/todo_to_issues.py            # dry run: print what would be created
  python3 scripts/todo_to_issues.py --create   # create issues with the gh CLI

Needs `gh auth login`. Re-running skips tasks that already have an issue ("T11 · ..." titles).
After creating, "Needs: T7, T9" and other T-references in bodies become links (#12, #14).
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

REPO = "b00rg/side-eye"
TODO = Path(__file__).resolve().parent.parent / "TODO.md"
PEOPLE = {"A": "lilipropanol", "B": "rachel-ranjith", "C": "b00rg"}
SECTIONS = {  # TODO.md heading prefix -> label
    "Pi setup": "pi-setup",
    "Calibrate": "recording",
    "Model evaluation": "model-eval",
    "Public data": "nist",
    "Demo": "demo",
    "Pitch": "pitch",
    "Optional": "optional",
}
LABEL_COLORS = {"A": "f9a825", "B": "8e24aa", "C": "00897b"}
TASK = re.compile(r"^- \[ \] \*\*(T\d+) · (.+?)[.:]?\*\*\s*(.*?)\s*\*Owner: (.*?)\. Needs: (.*?)\.\*$")
TREF = re.compile(r"\bT(\d+)\b")


def parse():
    tasks, section = [], None
    for line in TODO.read_text().splitlines():
        if line.startswith("## "):
            section = next((v for k, v in SECTIONS.items() if line[3:].startswith(k)), None)
            continue
        m = TASK.match(line)
        if not m:
            continue
        tid, title, text, owner, needs = m.groups()
        if re.search(r"\b(team|all)\b", owner):
            roles = list(PEOPLE)
        else:
            roles = sorted(set(re.findall(r"\b([ABC])\b", owner)))
        tasks.append(dict(id=tid, title=f"{tid} · {title}", text=text, owner=owner,
                          needs=needs, roles=roles, labels=roles + ([section] if section else [])))
    return tasks


def body(task, numbers=None):
    def link(s):
        if numbers is None:
            return s
        return TREF.sub(lambda m: f"#{numbers[m.group(0)]}" if m.group(0) in numbers else m.group(0), s)
    return (f"{link(task['text'])}\n\n"
            f"**Owner:** {task['owner']}\n"
            f"**Needs:** {link(task['needs'])}\n\n"
            f"_From [TODO.md](https://github.com/{REPO}/blob/master/TODO.md), task {task['id']}._")


def gh(*args):
    return subprocess.run(["gh", *args, "--repo", REPO], check=True, capture_output=True, text=True).stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--create", action="store_true", help="actually create issues (default: dry run)")
    args = ap.parse_args()
    tasks = parse()

    if not args.create:
        for t in tasks:
            who = ", ".join(PEOPLE[r] for r in t["roles"])
            print(f"{t['title']:<48} -> {who:<36} [{', '.join(t['labels'])}]  needs: {t['needs']}")
        print(f"\n{len(tasks)} issues would be created in {REPO}. Run with --create to do it.")
        return

    for name in {l for t in tasks for l in t["labels"]}:
        gh("label", "create", name, "--color", LABEL_COLORS.get(name, "c5def5"), "--force")

    existing = json.loads(gh("issue", "list", "--state", "all", "--limit", "500", "--json", "number,title"))
    numbers = {i["title"].split(" · ")[0]: i["number"] for i in existing if TREF.match(i["title"])}
    for t in tasks:
        if t["id"] in numbers:
            print(f"skip {t['title']} (#{numbers[t['id']]} exists)")
            continue
        args_ = ["issue", "create", "--title", t["title"], "--body", body(t)]
        for r in t["roles"]:
            args_ += ["--assignee", PEOPLE[r]]
        for l in t["labels"]:
            args_ += ["--label", l]
        url = gh(*args_).strip()
        numbers[t["id"]] = int(url.rsplit("/", 1)[1])
        print(f"created #{numbers[t['id']]} {t['title']}")

    for t in tasks:  # second pass: turn T-references into issue links
        gh("issue", "edit", str(numbers[t["id"]]), "--body", body(t, numbers))
    print(f"\nLinked dependencies on {len(tasks)} issues.")


if __name__ == "__main__":
    main()
