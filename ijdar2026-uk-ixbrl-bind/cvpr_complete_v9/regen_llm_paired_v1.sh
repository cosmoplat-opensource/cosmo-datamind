#!/bin/zsh
# Regenerate every paired comparison that involves a language-model baseline.
# Needed after the 2026-11-03 to_binding fix (a dash answered for a dash-printed cell is a filed zero); the
# stored answers were rebound offline (field "rebound" in each changed answer file), no API call was made.
# Run from anywhere:  zsh cvpr_complete_v9/regen_llm_paired_v1.sh
cd "${0:A:h}"
unset HELDOUT_ROOT HELDOUT_SOURCES  # set explicitly below for each split
LOG=${TMPDIR:-/tmp}/regen_llm_paired_logs; mkdir -p $LOG; pids=(); names=(); fail=0
run() { local name=$1; shift; "$@" > $LOG/${name//\//_}.log 2>&1 & pids+=($!); names+=($name); }
reap() { (( ${#pids} )) || return 0; local i; for i in {1..${#pids}}; do if wait $pids[$i]; then echo "ok   $names[$i]"; else echo "FAIL $names[$i] (log $LOG/${names[$i]//\//_}.log)"; fail=1; fi; done; pids=(); names=(); }
N=../runs/cvpr_complete_v10_20261014/native_correspondence
B=$N/common_candidate_v30/llm_baseline_v30
typeset -A DEV
DEV=(zeroshot_text_llm "$B/text/answers zeroshot_text_deepseek" zeroshot_vision_llm "$B/vision/answers zeroshot_vision_deepseek" fewshot_llm "$B/fewshot_history/deepseek_deepseek-flash/answers fewshot_deepseek" fewshot_linkbase_llm "$B/fewshot_linkbase/deepseek_deepseek-flash/answers fewshot_linkbase_deepseek" poe_gpt54_vision_llm "$B/poe_GPT-5.4/vision/answers gpt-5.4-zeroshot-vision" poe_gemini_vision_llm "$B/poe_gemini-3.1-pro/vision/answers gemini-3.1-pro-zeroshot-vision" poe_claude_vision_llm "$B/poe_claude-sonnet-5.5/vision/answers claude-sonnet-5.5-zeroshot-vision")
dev() { local run=$1 key=$2; local a=(${=DEV[$key]}); run dev_${run}_$key python3 compare_llm_run_v1.py dev $a[1] $N/common_candidate_$run/learning_v1/predictions $a[2] $run $N/common_candidate_$run/learning_v1/paired_vs_$key.json; }
for k in ${(k)DEV}; do dev v32 $k; dev v34 $k; done; reap
for k in fewshot_llm fewshot_linkbase_llm zeroshot_vision_llm; do dev v33 $k; dev v34_nollm $k; done; reap
H=$N/heldout_v1; L=$H/llm
export HELDOUT_ROOT=${H:A}
hd() { run ${5#$N/} python3 compare_heldout_v1.py llm:$1 run:$2 $3 $4 $5 ${@:6}; }
for ev in v4:v34 v5:v34_nollm; do e=${ev%%:*}; r=${ev##*:}
  for spec in zeroshot_llm:vision fewshot_llm:fewshot_history/deepseek_deepseek-flash fewshot_linkbase_llm:fewshot_linkbase/deepseek_deepseek-flash claude_zeroshot_llm:poe_claude-sonnet-5.5/vision gemini_zeroshot_llm:poe_gemini-3.1-pro/vision; do
    n=${spec%%:*}; d=${spec##*:}
    hd $L/$d/answers $H/evaluation_$e $n $r $H/evaluation_$e/paired_vs_${n}.json --methods semantic_memory
    hd $L/$d/answers $H/evaluation_$e $n $r $H/evaluation_$e/paired_vs_${n}_budget10.json --budgets 10 --methods semantic_memory
  done; reap; done
hd $L/vision/answers $H/evaluation_v1 zeroshot_llm v31 $H/evaluation_v1/paired_vs_zeroshot_llm_budget10.json --budgets 10 --methods semantic_memory
for ev in v2:v32 v3:v33; do e=${ev%%:*}; r=${ev##*:}
  hd $L/fewshot_history/deepseek_deepseek-flash/answers $H/evaluation_$e fewshot_llm $r $H/evaluation_$e/paired_vs_fewshot_llm_targetboot.json --methods semantic_memory
  hd $L/fewshot_history/deepseek_deepseek-flash/answers $H/evaluation_$e fewshot_llm $r $H/evaluation_$e/paired_vs_fewshot_llm_budget10.json --budgets 10 --methods semantic_memory
done
hd $L/fewshot_linkbase/deepseek_deepseek-flash/answers $H/evaluation_v3 fewshot_linkbase_llm v33 $H/evaluation_v3/paired_vs_fewshot_linkbase_llm_budget10.json --budgets 10 --methods semantic_memory
hd $L/vision/answers $H/evaluation_v3 zeroshot_llm v33 $H/evaluation_v3/paired_vs_zeroshot_llm_targetboot.json --methods semantic_memory
reap
S=$N/sealed_v1; LS=$S/llm
export HELDOUT_ROOT=${S:A}
for ev in v34:v34 v34_nollm:v34_nollm; do e=${ev%%:*}; r=${ev##*:}
  for spec in zeroshot_llm:vision fewshot_llm:fewshot_history/deepseek_deepseek-flash fewshot_linkbase_llm:fewshot_linkbase/deepseek_deepseek-flash; do
    n=${spec%%:*}; d=${spec##*:}
    hd $LS/$d/answers $S/evaluation_$e $n $r $S/evaluation_$e/paired_vs_${n}.json --methods semantic_memory
    hd $LS/$d/answers $S/evaluation_$e $n $r $S/evaluation_$e/paired_vs_${n}_budget10.json --budgets 10 --methods semantic_memory
  done; reap; done
[[ $fail == 0 ]] && echo ALL DONE || echo "SOME COMPARISONS FAILED"
exit $fail
