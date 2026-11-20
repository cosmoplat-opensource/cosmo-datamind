#!/bin/bash
# 2026-11-20 input-aligned rebuild (review: the EvoOntology builder had read each question's source database).
# Both builders read the same workload: question text and evidence only, no source database, no BIRD ids, shuffled
# order (variants/opaque_merged/workload_aligned). EvoOntology: same builder agent and model as v2. DataMind: the
# b9fa576 code used for the Section 5 ontologies. Answering: the v4 settings (no per-question evidence, 30 turns,
# 3 repeats per fold) on the same 400 MG test questions; conditions evoA and dmA.
cd "$(dirname "$0")"
L=runs/v4/aligned.log
WL=$PWD/variants/opaque_merged/workload_aligned
MDB=$PWD/variants/opaque_merged/dev_databases
echo "$(date) aligned chain start" >> $L
# 1. EvoOntology builds (both folds at once; separate workspaces)
for f in fwd rev; do
  EVO_WORKLOAD_DIR=$WL EVO_ROOT_SUFFIX=_aligned python3 build_evo_v2.py bird_merged --track w --S opaque_merged --dir $f >> $L 2>&1 &
done
# 2. DataMind builds (one at a time: the harness shares one workdir)
for f in fwd rev; do
  port=8631; [ $f = rev ] && port=8632
  DM_CODE_ROOT=../cosmo-datamind-b9fa576 DM_WORKLOAD_DIR=$WL DM_ROOT_SUFFIX=_aligned \
    python3 build_dm_v2.py --track w --S opaque_merged --dir $f --port $port bird_merged >> $L 2>&1 \
    || { echo "$(date) dm build $f failed" >> $L; }
  DM_DBDIR=$MDB DM2EVO_FULL=1 python3 dm2evo.py runs/v2/dm/w_opaque_merged_${f}_aligned/graphs.json \
    runs/v2/dm2evo/w_opaque_merged_${f}_aligned >> $L 2>&1 || { echo "$(date) dm convert $f failed" >> $L; }
done
wait
for f in fwd rev; do
  [ -f runs/v2/evo/w_opaque_merged_${f}_aligned/bird_merged/active.json ] || { echo "$(date) evo build $f missing, stop" >> $L; exit 1; }
  [ -d runs/v2/dm2evo/w_opaque_merged_${f}_aligned/bird_merged ] || { echo "$(date) dm conversion $f missing, stop" >> $L; exit 1; }
done
echo "$(date) builds done" >> $L
# 3. Answering runs
run() {  # fold cond rep
  f=$1; c=$2; r=$3; sr=minidev; [ $f = rev ] && sr=minidev_rev
  out=$PWD/runs/v4/qa/MG_$f/${c}_r$r; [ -d "$out" ] && return
  sem=$PWD/runs/v2/evo/w_opaque_merged_${f}_aligned; [ $c = dmA ] && sem=$PWD/runs/v2/dm2evo/w_opaque_merged_${f}_aligned
  EVID=0 QDIR=$PWD/variants/opaque_merged/questions/$sr ./run_method.sh $out $sr test - $sem 0 1 1 "" $MDB > /dev/null 2>&1
  echo "$(date) done MG_$f $c r$r" >> $L
}
for r in 1 2 3; do
  run fwd evoA $r & run rev evoA $r & run fwd dmA $r & run rev dmA $r
  wait
done
echo "$(date) all done" >> $L
