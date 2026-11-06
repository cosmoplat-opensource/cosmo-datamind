#!/usr/bin/env python3
"""Runnable offline task workflow and synthetic completion examples — DR-062 / DR-064 / DR-066."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from domain_runs.robotics_task_demo import run_demo


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True, choices=['generated', 'clarification', 'missing-knowledge', 'model-error', 'complete-information'])
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = run_demo(case=args.case, output=args.output)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['passed'] else 1
    except Exception as exc:
        print(f'演示失败：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
