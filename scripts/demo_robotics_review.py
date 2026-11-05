#!/usr/bin/env python3
"""Offline knowledge review scenarios and independent verification — DR-061 / DR-064 / DR-065."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from ontology_assets.review_demo import CASES, run_demo, verify_output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',choices=['all',*CASES],default='all')
    parser.add_argument('--output-dir',type=Path,default=ROOT / '.check-results/robotics-review-demo')
    parser.add_argument('--verify',type=Path,help='只读核验已有输出，不初始化或恢复存储')
    args = parser.parse_args(argv)
    try:
        result = verify_output(args.verify) if args.verify else run_demo(args.case,args.output_dir)
        print(json.dumps(result,ensure_ascii=False))
        return 0 if result['passed'] else 1
    except Exception as exc:
        print(f'FAIL: {type(exc).__name__}: {exc}',file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
