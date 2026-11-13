"""Pure role-bound adaptation facade; no storage, model calls or publication — DR-072."""
from ._application_roles import resolve_roles
from ._application_execution import extract_execution
from ._application_bundle import adapt, export_bundle, restore_bundle

__all__ = ['resolve_roles', 'extract_execution', 'adapt', 'export_bundle', 'restore_bundle']
