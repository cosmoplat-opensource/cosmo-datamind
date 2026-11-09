#!/bin/bash
# Protocol v5: score every finished unit (strict and column-tolerant EX) on the database it used -> rescore.json
cd "$(dirname "$0")"
for sf in MG_fwd MG_rev MGR_fwd MGR_rev; do
  S=${sf%_*}; f=${sf#*_}; sr=minidev; [ $f = rev ] && sr=minidev_rev
  case $S in MG) q=$PWD/variants/opaque_merged/questions/$sr; v=opaque_merged;; MGR) q=$PWD/variants/readable_merged/questions/$sr; v=readable_merged;; esac
  echo "== $sf"; python3 score_react_dir.py $q test $v runs/v5/qa/$sf/*_r[123]
done
