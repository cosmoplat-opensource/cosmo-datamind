"""Application contract core; no registration or evaluation on import — DR-071 / IR-025."""
from .checks import evaluate, require_ready
from .freshness import compare_freshness, make_basis
from .models import ContractError
from .registry import Registry

__all__ = ['ContractError', 'Registry', 'compare_freshness', 'evaluate', 'make_basis', 'require_ready']
