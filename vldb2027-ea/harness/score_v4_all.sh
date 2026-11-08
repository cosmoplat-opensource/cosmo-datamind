#!/bin/bash
cd "$(dirname "$0")"
for sf in R_fwd R_rev O_fwd O_rev MG_fwd MG_rev; do
  S=${sf%_*}; f=${sf#*_}; sr=minidev; [ $f = rev ] && sr=minidev_rev
  case $S in R) q=$sr; v=full;; O) q=$PWD/variants/opaque/questions/$sr; v=opaque;; MG) q=$PWD/variants/opaque_merged/questions/$sr; v=opaque_merged;; esac
  echo "== $sf"; python3 score_react_dir.py $q test $v runs/v4/qa/$sf/*_r[123]
done
