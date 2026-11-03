#!/usr/bin/env python3
"""Run offline storage scenarios or independently verify persisted files — DR-064 / DR-065."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from domain_runs.offline_demo import EXPECTED, process_worker, run_demo
from domain_runs.verification import verify_storage


def main(argv=None):
    fixtures = ROOT / "tests/fixtures/robotics"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["all", *EXPECTED], default="all")
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".check-results/domain-storage-demo")
    parser.add_argument("--source", type=Path, default=fixtures / "sources/xingweishu.xml")
    parser.add_argument("--models", type=Path, default=fixtures / "sources/xml_models.xml")
    parser.add_argument("--supplement", type=Path, default=fixtures / "demo_supplement.json")
    parser.add_argument("--verify-storage", type=Path, help="只读检查已有存储，输出 JSON，不执行启动恢复")
    parser.add_argument("--_worker", choices=["crash", "recover"], help=argparse.SUPPRESS)
    parser.add_argument("--storage", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.verify_storage:
            print(json.dumps(verify_storage(args.verify_storage.resolve()), ensure_ascii=False))
            return 0
        if args._worker:
            if not args.storage or not args.run_id:
                raise ValueError("内部进程需要明确的存储目录和运行 ID。")
            process_worker(args._worker, args.storage.resolve(), args.run_id)
            return 0
        summary = run_demo(args.case, args.output_dir, source=args.source, models=args.models, supplement=args.supplement)
    except Exception as exc:
        print(f"FAIL：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"运行目录：{summary['run_dir']}")
    print(f"存储目录：{summary['storage_root']}")
    for row in summary["cases"]:
        print(f"{'PASS' if row['passed'] else 'FAIL'} {row['case']}: "
              f"预期={row['expected_status']} 实际={row['actual_status']} run_id={row.get('run_id', '未创建')}")
        for issue in row["issues"]:
            print(f"  {issue['code']}: {issue['message']}")
    print(f"独立进程核验：{'PASS' if summary['verification']['passed'] else 'FAIL'}")
    print(f"汇总：{Path(summary['run_dir']) / 'summary.md'}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
