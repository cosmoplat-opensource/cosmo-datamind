"""Validate and atomically publish new OWL files without overwrite — DR-060 / DR-065."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile

from rdflib import Literal, XSD

from .artifact_contract import ARTIFACT_CONTRACT, trusted_shapes
from .consumer import load_ontology, validate_graph
from .graph import ONTOLOGY, S


def _normalize_port_metadata(graph):
    """v2 stores names/directions/types on ports; reject conflicting duplicates."""
    for parameter in set(graph.subjects(S.mapsToPort, None)):
        ports = list(graph.objects(parameter, S.mapsToPort))
        if len(ports) != 1:
            raise ValueError(f"参数必须指向唯一端口契约：{parameter}")
        for prop in (S.parameterName, S.direction, S.cppType):
            direct = set(graph.objects(parameter, prop))
            inherited = set(graph.objects(ports[0], prop))
            if len(inherited) != 1 or (direct and direct != inherited):
                raise ValueError(f"参数与端口契约冲突：{parameter} / {prop}")
            graph.remove((parameter, prop, None))


def publish_ontology(graph, target, *, mode="real"):
    """Publish a copy and return validation of the actual serialized artifact."""
    target = Path(target)
    if target.suffix.lower() != ".owl":
        raise ValueError("发布目标必须为 .owl 文件。")
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    candidate = deepcopy(graph)
    _normalize_port_metadata(candidate)
    # Only missing publication metadata may be added. Never repair conflicting metadata.
    for prop, expected in ((S.artifactContract, ARTIFACT_CONTRACT), (S.validationShapes, trusted_shapes())):
        if not list(candidate.objects(ONTOLOGY, prop)):
            candidate.add((ONTOLOGY, prop, Literal(expected, datatype=XSD.string)))
    validate_graph(candidate, mode=mode)
    content = candidate.serialize(format="xml", encoding="utf-8")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".owl", prefix=".robotics-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        context = load_ontology(temporary, mode=mode)
        # Same-directory hard link publishes complete bytes exclusively, even under races.
        os.link(temporary, target)
        return context["validation"]
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
