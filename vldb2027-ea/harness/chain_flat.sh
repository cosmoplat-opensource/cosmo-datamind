#!/bin/bash
# 2026-11-20 flat term dictionary control on MG (make_flat_dictionary.py), protocol-v4 answering settings.
cd "$(dirname "$0")"
L=runs/v5/flat.log; MDB=$PWD/variants/opaque_merged/dev_databases
echo "$(date) flat chain start" >> $L
run() { f=$1; r=$2; sr=minidev; [ $f = rev ] && sr=minidev_rev
  out=$PWD/runs/v5/qa/MG_$f/evo_flat_r$r; [ -d "$out" ] && return
  EVID=0 QDIR=$PWD/variants/opaque_merged/questions/$sr ./run_method.sh $out $sr test - $PWD/runs/v5/onto/evo_flat_$f 0 1 1 "" $MDB > /dev/null 2>&1
  echo "$(date) done MG_$f evo_flat r$r" >> $L; }
for r in 1 2 3; do run fwd $r & run rev $r; wait; done
echo "$(date) all done" >> $L
