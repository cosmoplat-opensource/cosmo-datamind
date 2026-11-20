#!/bin/zsh
# Protocol v5: rebuild the BIRD-readable-merged DataMind ontologies with commit b9fa576 (the code of the opaque-merged
# builds), one fold after the other, convert them (dm2evo, DM2EVO_FULL=1), re-plan queue_v5 and run dm_b9 / dmdoc_b9.
cd "$(dirname "$0")/../.."
EXP=$PWD
export DM_CODE_ROOT=$EXP/../cosmo-datamind-b9fa576 DM_ROOT_SUFFIX=_b9fa576
for d in fwd rev; do
  python3 build_dm_v2.py --track w --S readable_merged --dir $d --port 8621 bird_merged || { echo "$(date) build $d failed"; exit 1; }
  [[ -f runs/v2/dm/w_readable_merged_${d}_b9fa576/graphs.json ]] || { echo "$(date) no graphs for $d"; exit 1; }
  DM_DBDIR=$EXP/variants/readable_merged/dev_databases DM2EVO_FULL=1 python3 dm2evo.py runs/v2/dm/w_readable_merged_${d}_b9fa576/graphs.json runs/v2/dm2evo/w_readable_merged_${d}_b9fa576 || { echo "$(date) dm2evo $d failed"; exit 1; }
done
unset DM_CODE_ROOT DM_ROOT_SUFFIX
python3 queue_v5.py plan
echo "$(date) starting dm_b9 runs"
exec python3 queue_v5.py run --parallel 4 --only "_b9"
