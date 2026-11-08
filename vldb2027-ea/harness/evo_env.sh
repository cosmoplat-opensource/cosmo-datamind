# 从 cosmo-datamind/.env 取模型凭据,不回显
_get(){ grep -E "^$1=" ${DATAMIND_ENV:-../cosmo-datamind/.env} | head -1 | cut -d= -f2- | sed -e "s/^'//" -e "s/'$//" -e 's/^"//' -e 's/"$//'; }
if [ -n "${DEEPSEEK_API_KEY_FILE:-}" ]; then
  export BIRD_AGENT_API_KEY="$(cat "$DEEPSEEK_API_KEY_FILE")"
else
  export BIRD_AGENT_API_KEY="${BIRD_AGENT_API_KEY:-${DEEPSEEK_API_KEY:-$(_get DATAMIND_LLM_KEY)}}"
fi
export BIRD_AGENT_EXTRA_BODY="${BIRD_AGENT_EXTRA_BODY:-$(_get DATAMIND_LLM_EXTRA_BODY)}"
