#!/usr/bin/env python3
"""Print a self-contained agent brief; never execute tasks or create worktrees."""

import argparse
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    package = root / "docs/agent_tasks"
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    tasks = {task["id"]: task for task in manifest["tasks"]}
    parser = argparse.ArgumentParser(description="Ready-to-paste full-flow agent tasks")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List tasks and completion dependencies")
    show = commands.add_parser("show", help="Print the common brief and one task")
    show.add_argument("task_id", choices=sorted(tasks))
    args = parser.parse_args()
    if args.command == "list":
        for task in tasks.values():
            marker = " [отдельный трек]" if task["optional"] else ""
            dependencies = ", ".join(task["accept_after"]) or "—"
            print(task["id"] + "  " + task["title"] + marker + " | приёмка после: " + dependencies)
    else:
        task = tasks[args.task_id]
        print("# Общее задание и границы проекта\n")
        print((root / "AGENTS.md").read_text(encoding="utf-8"))
        print((package / manifest["brief"]).read_text(encoding="utf-8"))
        print("\n# Назначенная задача: " + task["id"])
        print("Рекомендуемая ветка: " + task["branch"] + "\n")
        print((package / task["file"]).read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
