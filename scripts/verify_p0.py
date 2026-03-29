import json, os
from collections import Counter

# Value V3 verification
for name, d in [('Value V3', 'output_v3_value')]:
    top = []
    labels = Counter()
    ranked_count = 0
    for f in os.listdir(d):
        if not f.endswith('.json'): continue
        with open(os.path.join(d, f), 'r', encoding='utf-8') as fh:
            x = json.load(fh)
        label = x.get('label', '')
        labels[label] += 1
        if x.get('rank') is not None:
            ranked_count += 1
        if label == '重点研究':
            top.append(x)
    
    print(f'=== {name} ===')
    for label, cnt in labels.most_common():
        print(f'  {label}: {cnt}')
    print(f'  Ranked (with percentile): {ranked_count}')
    print(f'  5% of ranked = {ranked_count * 0.05:.1f}')
    print(f'  重点研究 count: {len(top)}')
    
    ind = Counter(x['security']['industry_l1'] for x in top)
    total = len(top)
    print(f'  Industry distribution:')
    for industry, cnt in ind.most_common():
        pct = 100*cnt/total
        flag = ' OVER' if pct > 25 else ' OK'
        print(f'    {industry}: {cnt}/{total} = {pct:.0f}%{flag}')

print()

# Growth V3: check industry_momentum in sub_scores
with open('output_v3_growth/600519.json', 'r', encoding='utf-8') as fh:
    sample = json.load(fh)
ss_keys = list(sample.get('sub_scores', {}).keys())
print(f'Growth V3 sub_scores keys: {ss_keys}')
has_im = 'industry_momentum' in ss_keys
print(f'industry_momentum present: {has_im}')
