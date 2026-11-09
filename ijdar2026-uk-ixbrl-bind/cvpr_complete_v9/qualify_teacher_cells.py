"""Separate publisher/pixel consistency audit, without inventing header ownership.

Only the 2023 development reports are eligible for later supervised training.
Future development years are retained for annotation-quality diagnostics only.
Canonical scale is a private teacher attribute, not a visible scope label.
"""
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re

from prepare import OUT, save, sha


def public_number_candidates(text):
    if not isinstance(text,str):return []
    t=text.strip().replace('−','-').replace('–','-').replace('\u00a0',' ')
    t=re.sub(r'^[$£€]\s*','',t)
    t=re.sub(r'\s*(?:%|p|million|millions|thousand|thousands|billion|billions)\s*$', '', t, flags=re.I)
    t=re.sub(r'\s+','',t)
    negative=t.startswith('(') and t.endswith(')')
    if negative:t=t[1:-1]
    output=set()
    if re.fullmatch(r'[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?',t) or re.fullmatch(r'[+-]?\.\d+',t):
        output.add(Decimal(t.replace(',','')))
    # A single comma can also be a decimal separator. Do not resolve locale by
    # looking at the private reference value or silently pick one convention.
    if re.fullmatch(r'[+-]?\d+,\d+',t):output.add(Decimal(t.replace(',','.')))
    if negative:output={-v for v in output}
    return sorted(str(v) for v in output)


def overlap_fraction(a,b):
    x=max(0,min(a[2],b[2])-max(a[0],b[0]));y=max(0,min(a[3],b[3])-max(a[1],b[1]))
    area=max(0,b[2]-b[0])*max(0,b[3]-b[1])
    return x*y/area if area else 0


def main():
    root=OUT/'expanded_development_sources'
    rendering=json.loads((root/'native_renders_v3/render_manifest_dpr2.json').read_text())
    ocr_meta=json.loads((root/'common_perception/manifest.json').read_text())
    observations={(r['report'],r['page']):r for r in ocr_meta['pages']}
    rows=[];counts=Counter()
    for report in rendering:
        identifier=report['entity']+'_'+report['period_end'][:4]
        audit_path=root/'scoring_only'/(report['entity']+'_'+report['period_end']+'.json')
        reference=json.loads(audit_path.read_text())
        facts={r['region_id']:r for r in reference['rows'] if r['region_id']}
        for index,page in enumerate(report['selected_pages']):
            observation=observations[identifier,index];path=Path(observation['ocr_path'])
            if sha(path)!=observation['ocr_sha256']:raise RuntimeError('Frozen OCR changed')
            data=json.loads(path.read_text());w,h=data['width'],data['height']
            for region in page['scoring_only_region_boxes']:
                counts['rendered_tag_regions']+=1
                fact=facts.get(region['id'])
                if fact is None or not fact.get('published_fact_found'):
                    counts['regions_without_matching_publisher_fact']+=1;continue
                x,y,dx,dy=region['bbox'];box=[x-12,y-12,x+dx+12,y+dy+12]
                words=[]
                for word in data['words']:
                    b=[v*(w if j%2==0 else h) for j,v in enumerate(word['box'])]
                    if overlap_fraction(box,b)>=.5:words.append(word)
                words.sort(key=lambda a:(round(a['box'][1]/.005),a['box'][0]))
                texts=[t['text'] for t in words];joined=' '.join(texts)
                candidates=set(public_number_candidates(joined))
                if not candidates:
                    for text in texts:candidates.update(public_number_candidates(text))
                canonical=fact.get('canonical_value');agreement=False;inversion=False;valid_reference=True;zero=False
                try:
                    scale=Decimal(10)**int(fact['scale']);value=Decimal(str(canonical))
                    agreement=any(Decimal(c)*scale==value for c in candidates)
                    inversion=any(-Decimal(c)*scale==value for c in candidates)
                    zero=value==0
                    canonical_negative=value<0
                except (ValueError,InvalidOperation,TypeError):valid_reference=False;canonical_negative=False
                literal_negative=any(Decimal(c)<0 for c in candidates)
                row={'report':identifier,'page':index,'publisher_region_id':region['id'],
                    'training_year_allowed':report['period_end'].startswith('2023-'),
                    'browser_region_visible_flag':region['visible'],'public_ocr_strings':texts,
                    'public_numeric_parse_candidates':sorted(candidates),'multiple_public_parses':len(candidates)>1,
                    'private_teacher_numeric_reference_valid':valid_reference,
                    'glyph_agrees_using_private_publisher_scale':agreement,
                    'sign_inversion_agrees_using_private_publisher_scale':inversion,
                    'zero_quantity_cannot_identify_presentation_polarity':zero,
                    'canonical_negative':canonical_negative,'negative_seen_in_literal_ocr_candidates':literal_negative,
                    'private_publisher_scale_is_not_independently_certified_visible_scale':True,
                    'header_ownership_label_derived':False,'full_semantic_binding_certified':False,
                    'unsupported_or_ambiguous_aspects_remain_unknown':True}
                rows.append(row)
                counts['matched_publisher_regions']+=1
                counts['regions_with_public_numeric_parse']+=bool(candidates)
                counts['regions_with_multiple_public_parses']+=len(candidates)>1
                counts['glyph_agreement_given_private_scale']+=agreement
                counts['sign_inversion_agreement_given_private_scale_nonzero']+=inversion and not zero
                counts['polarity_requires_further_semantic_definition_not_reference_repair']+=inversion and not agreement and not zero
                counts['canonical_negative_without_literal_negative_ocr']+=canonical_negative and not literal_negative
    dest=root/'teacher_qualification';save(dest/'scoring_only/cells.json',rows)
    save(dest/'manifest.json',{'counts':dict(counts),'private_teacher_records':len(rows),
        'supervised_training_years_allowed':[2023],'future_years_are_quality_audit_only':True,
        'source_quality_reference_files_used':True,'these_are_not_student_inputs':True,
        'not_independent_visible_scale_or_header_scope_certification':True,
        'no_scope_ownership_labels_created_from_numeric_agreement':True,'human_confirmations':0,
        'render_manifest_sha256':sha(root/'native_renders_v3/render_manifest_dpr2.json'),
        'native_ocr_manifest_sha256':sha(root/'common_perception/manifest.json'),'code_sha256':sha(__file__)})
    print(json.dumps(dict(counts),indent=2))


if __name__=='__main__':main()
