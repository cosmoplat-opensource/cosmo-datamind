"""Pure current-fact bundle and validated RDF restoration — DR-072."""
from copy import deepcopy
import base64
import hashlib
import json
from rdflib import Graph, BNode, Literal, URIRef, RDF, OWL
from rdflib.compare import isomorphic
from ._application_roles import issue
from ._application_execution import extract_execution
from ._application_graph import render
from .task_knowledge import inspect_structure
from .modeling.common import digest, canonical
from .modeling import abox
from ._application_rdf import parse_rdf

CONTRACT={'id':'robotics-application-design','version':2}


class AdaptationError(ValueError):
    def __init__(self):
        super().__init__('应用工件版本或内容不一致。'); self.code='application_artifact_invalid'


def _term(term):
    if isinstance(term,Literal):return {'kind':'literal','value':str(term),'datatype':str(term.datatype) if term.datatype else None,'language':term.language}
    return {'kind':'bnode' if isinstance(term,BNode) else 'iri','value':str(term)}


def _node(row):
    if row['kind']=='iri':return URIRef(row['value'])
    if row['kind']=='bnode':return BNode(row['value'])
    if row['kind']=='literal':return Literal(row['value'],datatype=URIRef(row['datatype']) if row['datatype'] else None,lang=row['language'])
    raise ValueError('term')


def _current(graph):
    schema_subjects=set(graph.subjects(RDF.type,OWL.Class))|set(graph.subjects(RDF.type,OWL.ObjectProperty))|set(graph.subjects(RDF.type,OWL.DatatypeProperty))
    rows={'tbox':[],'abox':[]}
    for triple in sorted(graph,key=lambda t:tuple(map(str,t))):
        rows['tbox' if triple[0] in schema_subjects else 'abox'].append([_term(t) for t in triple])
    return rows


def adapt(*, design, role_map, mode='real'):
    try:
        if mode not in ('real','demo') or hashlib.sha256(design['ontology']).hexdigest()!=design['origin']['payload_sha256']:
            raise ValueError('origin')
        if design['tbox']!=design['confirmation']['tbox']:raise ValueError('schema')
        # Validate selected instance assertions; host binding to v1 manifests remains a service responsibility.
        abox.validate_result(design['abox'],design['tbox'],design['evidence']['evidence'])
        extraction=extract_execution(confirmation=design['confirmation'],evidence_bundle=design['evidence'],role_map=role_map)
        if extraction['status']!='extracted':return {**extraction,'ontology':None,'document':None}
        g,identities=render(extraction['execution'],design,role_map,mode)
        inspect_structure(g,mode)
        raw=g.serialize(format='xml',encoding='utf-8')
        return {'status':'adapted','issues':[],'ontology':raw,'identity_map':identities,
                'execution':extraction['execution'],'current':_current(g),'role_map':deepcopy(role_map),
                'origin':{'asset_ref':{k:design['origin'][k] for k in ('ontology_id','version_id')},
                          'payload_sha256':design['origin']['payload_sha256'],
                          'design_snapshot':{k:deepcopy(design[k]) for k in ('tbox','confirmation','abox','coverage','evidence')}},
                'coverage':deepcopy(design['coverage']),'validation':{'structure':'passed','mode':mode},
                'components':{'adapter_version':'ur-fixed-adaptation-1','role_map_version':role_map['version']},
                'execution_readiness':'not_verified'}
    except (ValueError,KeyError,TypeError) as exc:
        return {'status':'blocked','issues':[issue('execution.source_integrity','适配依据或执行结构无效。',detail=type(exc).__name__)],
                'ontology':None,'document':None}


def export_bundle(product, *, output_asset_ref, parent_version_id):
    try:
        if product['status']!='adapted' or parent_version_id!=product['origin']['asset_ref']['version_id']:
            raise ValueError('lineage')
        if output_asset_ref['ontology_id']!=product['origin']['asset_ref']['ontology_id'] or output_asset_ref['version_id']==parent_version_id:
            raise ValueError('lineage')
        document={k:deepcopy(product[k]) for k in ('current','origin','identity_map','role_map','coverage','validation','components','execution_readiness')}
        raw=product['ontology']
        document.update(contract=CONTRACT,output_asset_ref=deepcopy(output_asset_ref),parent_version_id=parent_version_id,
                        payload_sha256=hashlib.sha256(raw).hexdigest(),payload_base64=base64.b64encode(raw).decode())
        document['current_sha256']=digest(document['current'])
        result={'owl':raw,'json':canonical(document).encode()}
        restore_bundle(result['json'])
        return result
    except (ValueError,KeyError,TypeError):raise AdaptationError() from None


def restore_bundle(json_bytes):
    try:
        if not isinstance(json_bytes,bytes) or len(json_bytes)>32*1024*1024:raise ValueError('size')
        d=json.loads(json_bytes)
        if d['contract']!=CONTRACT or digest(d['current'])!=d['current_sha256']:raise ValueError('current')
        raw=base64.b64decode(d['payload_base64'],validate=True)
        if hashlib.sha256(raw).hexdigest()!=d['payload_sha256']:raise ValueError('hash')
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('xml')
        stored=parse_rdf(raw); current=Graph()
        for section in ('tbox','abox'):
            if not isinstance(d['current'][section],list) or not d['current'][section]:raise ValueError('current')
            for triple in d['current'][section]:current.add(tuple(_node(t) for t in triple))
        if not isomorphic(stored,current) or list(stored.triples((None,OWL.imports,None))):raise ValueError('graph')
        inspect_structure(stored,d['validation']['mode'])
        return raw
    except Exception:raise AdaptationError() from None
