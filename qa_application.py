"""Monolith composition boundary for modular data-application checks — DR-156."""
from application_contracts.models import ContractError as QAContractError
from application_contracts.deepqa_host import RequestGate as QARequestGate, failure as qa_contract_failure
from application_contracts.deepqa_host import cache_key as qa_contract_cache_key, record_delete as qa_record_delete

__all__ = ['QAContractError', 'QARequestGate', 'qa_contract_failure',
           'qa_contract_cache_key', 'qa_record_delete']
