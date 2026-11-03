#!/usr/bin/env python3
"""Run the three offline robotics acceptance scenarios — DR-060 / DR-065 / IR-016."""
import argparse
from pathlib import Path
import sys

# The repository CLI can run by absolute path from any cwd; domain modules never alter sys.path.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from domains.robotics.offline_demo import EXPECTED, run_demo


def main(argv=None):
    fixtures = ROOT / "tests/fixtures/robotics"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["all", *EXPECTED], default="all")
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".check-results/robotics-demo")
    parser.add_argument("--source", type=Path, default=fixtures / "sources/xingweishu.xml")
    parser.add_argument("--models", type=Path, default=fixtures / "sources/xml_models.xml")
    parser.add_argument("--supplement", type=Path, default=fixtures / "demo_supplement.json")
    args = parser.parse_args(argv)
    try:
        summary = run_demo(args.case, args.output_dir, source=args.source, models=args.models, supplement=args.supplement)
    except Exception as exc:
        print(f"FAIL：无法完成演示报告：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"运行目录：{summary['run_dir']}")
    for row in summary["cases"]:
        print(f"{'PASS' if row['passed'] else 'FAIL'} {row['case']}: 预期={row['expected_status']} 实际={row['actual_status']}")
        for issue in row["issues"]:
            print(f"  {issue['code']}: {issue['message']}")
    print(f"汇总：{Path(summary['run_dir']) / 'summary.md'}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
