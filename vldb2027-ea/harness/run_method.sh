#!/bin/zsh
# One run of the ReAct agent with ontology-compiled consumption (VLDB method), or of a ReAct baseline, on one split.
# usage: run_method.sh <out_dir> <splitroot> <split> <dm_graphs.json|-> <semantic_root|-> <port> [ctx=1] [check=1] [ablate=dict] [db_root|-]
#   dm_graphs.json  DataMind graph set compiled into the context and used by the check ("-": no DataMind instance)
#   semantic_root   store for the agent's browse/resolve tools ("-": baseline config without ontology tools)
#   ctx, check      1/0: inject the compiled context; check the final SQL once
#   ablate          DATAMIND_ABLATE value for the DataMind instance (default "dict": no external column documentation)
#   db_root         schema-variant database directory for both agent and DataMind ("-": original databases)
set -e
EXP=.
OUT=$1 SR=$2 SPLIT=$3 G=$4 SEM=$5 PORT=$6 CTX=${7:-1} CHK=${8:-1} ABL=${9-dict} DBR=${10:--}
CFG=deepseek_ontology; [[ $SEM == - ]] && CFG=deepseek_baseline
mkdir -p $OUT
EVAL="source $EXP/evo_env.sh; cd $EXP/EvoOntology/benchmarks/bird; exec $EXP/.venv-evo/bin/python run_evaluation.py --config configs/$CFG.yaml --dataset minidev --split-dir ${QDIR:-data/$SR}/$SPLIT --parallel 5 --save-traces --output $OUT"
export EVO_USE_EVIDENCE=${EVID:-1}
[[ $SEM != - ]] && export EVO_SEMANTIC_ROOT=$SEM
[[ $DBR != - ]] && export EVO_DB_ROOT=$DBR DM_DBDIR=$DBR DM_SOURCE_TAG=$(basename $(dirname $DBR))
if [[ $G == - ]]; then
  exec bash -c "$EVAL"
fi
[[ $CTX == 1 ]] && export EVO_CONTEXT_MODE=dmc
[[ $CHK == 1 ]] && export EVO_DM_CHECK=1
export DM_ROOT=../cosmo-datamind-exp DM_PORT=$PORT DM_GRAPHS=$G DM_SPLITROOT=$SR DM_SPLIT=$SPLIT DATAMIND_ABLATE=$ABL
exec python3 $EXP/bird_datamind.py with "bash -c '$EVAL'"
