"""Development-only oracle decomposition and source-label qualification.

Publisher fact identity and source transformation agreement are distinct from
visible semantic support. No result in this audit is a learned-method score.
"""
from collections import Counter
from decimal import Decimal,InvalidOperation
import json
import math
from pathlib import Path
import re
from PIL import Image

from prepare import SOURCE,OUT,opaque,save,sha


def valid_box(box):
    return (isinstance(box,list) and len(box)==4
       and all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=1 for v in box)
       and box[0]<box[2] and box[1]<box[3])


def canonical_header(header):
    if not valid_box(header.get('box')):raise ValueError('Invalid source header geometry')
    scopes=header.get('applies_to',[]);accepted=[];unknown=[]
    if not isinstance(scopes,list):unknown.append('scope_is_not_a_list');scopes=[]
    for box in scopes:
        if valid_box(box):accepted.append(box)
        else:unknown.append('unvalidated_scope_coordinates')
    return {'text':header['text'],'kind':header['kind'],'box':header['box'],
       'possible_scope_regions':accepted,'scope_uncertainty':unknown,
       'ambiguous':header.get('ambiguous') is not False or bool(unknown),
       'independent_ownership_label':False}


def compact(text):return re.sub(r'\s','',text).replace('−','-').replace('–','-').replace('—','-')


def target_words(words,box):
    return [w for w in words if box[0]-.002<=sum(w['box'][::2])/2<=box[2]+.002
               and box[1]-.002<=sum(w['box'][1::2])/2<=box[3]+.002]


def main():
    questions=json.loads((SOURCE/'questions/dev/questions.json').read_text());rows=[];counts=Counter();golds={};page_headers={}
    raw=OUT/'common_perception/headers';canonical=OUT/'common_perception/qualified_headers'
    for file in sorted(raw.glob('*.json')):
        data=json.loads(file.read_text());items=[canonical_header(h) for h in data['observation']['headers']]
        save(canonical/file.name,{**data,'observation':{'headers':items,'unresolved':data['observation'].get('unresolved',[])},
           'possible_scope_regions_are_predictions_not_gold':True,'raw_header_sha256':sha(file)})
        page_headers[data['page_id']]=items
    for q in questions:
        rid=q['report_id']
        if rid not in golds:golds[rid]=json.loads((SOURCE/'assets'/rid/'gold.json').read_text())['facts']
        # A region can have multiple publisher tags; keep all, not a convenient singleton.
        matches=[g for g in golds[rid] if g['page_id']==q['page_id'] and g['bbox']==q['bbox']]
        counts['tasks']+=1;counts['source_geometry_has_tag']+=bool(matches)
        image=SOURCE/q['image'];pid=opaque(sha(image));op=OUT/'common_perception/ocr'/f'{pid}.json'
        words=json.loads(op.read_text())['words'] if op.exists() else []
        with Image.open(image) as im:width,height=im.size
        x,y,w,h=q['bbox'];box=[x/width,y/height,(x+w)/width,(y+h)/height]
        inside=sorted(target_words(words,box),key=lambda r:(r['box'][1],r['box'][0]))
        glyph=''.join(w['text'] for w in inside);literal=compact(q['display'])
        ambiguous_placeholder=literal in ('-','')
        missing_ocr=not op.exists();exact_glyph=compact(glyph)==literal and not ambiguous_placeholder
        hidden_sign=any(g.get('sign')=='-' and not ('-' in literal or literal.startswith('(')) for g in matches)
        source_scale={str(g.get('scale','0')) for g in matches}
        display_matches=bool(matches) and all(g.get('display_matches_value') is True for g in matches)
        counts['missing_common_ocr']+=missing_ocr;counts['target_glyph_exact_ocr']+=exact_glyph
        counts['placeholder_has_unresolved_zero_or_missing_meaning']+=ambiguous_placeholder
        counts['negative_sign_not_literal_in_target_glyph']+=hidden_sign
        counts['source_transformation_agrees_with_published_fact']+=display_matches
        counts['nontrivial_scale_needs_visible_support']+=any(v!='0' for v in source_scale)
        headers=page_headers.get(pid,[])
        years={int(n) for head in headers if head['kind']=='period' for n in re.findall(r'\b(?:19|20)\d{2}\b',head['text'])}
        period=q['gold']['period'];date=period.get('instant') or period.get('end')
        possible_year=bool(date and int(date[:4]) in years)
        counts['any_compatible_public_year_anchor']+=possible_year
        rows.append({'task_id':opaque(q['qid']),'page_id':pid,'source_geometry_tag_candidates':len(matches),
            'source_transform_agrees':display_matches,'exact_target_glyph_ocr':exact_glyph,
            'placeholder_ambiguous':ambiguous_placeholder,'hidden_sign_requires_semantic_evidence':hidden_sign,
            'possible_year_anchor_exists':possible_year,'scope_ownership_certified':False,
            'complete_period_unit_dimension_version_binding_certified':False,
            'label_status':'publisher_fact_proxy_not_certified_native_visual_QA',
            'missing_common_ocr':missing_ocr})
    save(OUT/'scoring_only/oracle_decomposition.json',{'counts':dict(counts),'tasks':rows,
       'development_only':True,'human_confirmations':0,'learned_method_scores':False,
       'native_complete_binding_correctness_is_not_identifiable_from_current_proxy_labels':True,
       'unknown_scope_edges_cannot_be_used_as_ownership_training_labels':True,
       'code_sha256':sha(__file__)})
    print(json.dumps(dict(counts),ensure_ascii=False))


if __name__=='__main__':main()
