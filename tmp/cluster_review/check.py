import csv
import re
from collections import defaultdict
from difflib import SequenceMatcher

clusters_file = "tmp/cluster_review/current_clusters.csv"
merge_history_file = "tmp/cluster_review/merge_history.csv"

def read_csv(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)

def get_similarity(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

clusters = read_csv(clusters_file)
merges = read_csv(merge_history_file)

# --- Helpers for Semantic Checks ---
def extract_semantics(text):
    text = text.lower()
    signals = []
    # Weights
    if re.search(r'\b60\s*g', text): signals.append(('weight', 60))
    if re.search(r'\b120\s*g', text): signals.append(('weight', 120))
    if re.search(r'\b160\s*g', text): signals.append(('weight', 160))
    if re.search(r'\b200\s*g', text): signals.append(('weight', 200))
    if re.search(r'\b220\s*g', text): signals.append(('weight', 220))
    if re.search(r'\b500\s*g', text): signals.append(('weight', 500))
    
    # Volumes
    if re.search(r'\b200\s*ml', text): signals.append(('volume', 200))
    if re.search(r'\b300\s*ml', text): signals.append(('volume', 300))
    if re.search(r'\b325\s*ml', text): signals.append(('volume', 325))
    if re.search(r'\b700\s*ml', text): signals.append(('volume', 700))
    if re.search(r'\b725\s*ml', text): signals.append(('volume', 725))
    
    # Formats
    if 'junior scoop' in text or 'small scoop' in text: signals.append(('format', 'junior_scoop'))
    if 'regular scoop' in text: signals.append(('format', 'regular_scoop'))
    if 'mini tub' in text: signals.append(('format', 'mini_tub'))
    if 'perfect plenty' in text: signals.append(('format', 'perfect_plenty'))
    if 'family' in text or 'feast' in text: signals.append(('format', 'family_tub'))
    if '1pc' in text or '1\s*pc' in text: signals.append(('format', '1_piece'))
    if '2pc' in text or '2\s*pc' in text: signals.append(('format', '2_piece'))
    
    return signals

def check_semantic_mismatch(raw_name, variant_name):
    signals = extract_semantics(raw_name)
    variant = variant_name.upper()
    
    mismatches = []
    for kind, value in signals:
        if kind == 'weight':
            if value == 60 and variant != 'JUNIOR_SCOOP_60GMS': mismatches.append(f"{value}g vs {variant}")
            if value == 120 and variant != 'REGULAR_SCOOP_120GMS': mismatches.append(f"{value}g vs {variant}")
            # Tubs often have varying weight vs volume, but 500g in a scoop is definitely wrong
            if value == 500 and 'SCOOP' in variant: mismatches.append(f"{value}g vs {variant}")
        elif kind == 'volume':
            if value == 200 and 'MINI_TUB' not in variant and '200ML' not in variant: mismatches.append(f"{value}ml vs {variant}")
            if value >= 700 and 'FAMILY' not in variant and '725ML' not in variant: mismatches.append(f"{value}ml vs {variant}")
        elif kind == 'format':
            if value == 'junior_scoop' and 'JUNIOR_SCOOP' not in variant: mismatches.append(f"{value} vs {variant}")
            if value == 'regular_scoop' and 'REGULAR_SCOOP' not in variant: mismatches.append(f"{value} vs {variant}")
            if value == 'mini_tub' and 'MINI_TUB' not in variant: mismatches.append(f"{value} vs {variant}")
            if value == 'perfect_plenty' and 'PERFECT_PLENTY' not in variant and 'REGULAR_TUB' not in variant: mismatches.append(f"{value} vs {variant}")
            if value == 'family_tub' and 'FAMILY' not in variant: mismatches.append(f"{value} vs {variant}")
            
    return mismatches

# --- Analysis ---
print("### 1 & 6: Naming & Duplicate Opportunities")
parent_map = defaultdict(list)
for c in clusters:
    pname = c["parent_cluster_name"]
    parent_map[pname].append(c)

pnames = sorted(parent_map.keys())
for i, p1 in enumerate(pnames):
    for j in range(i+1, len(pnames)):
        p2 = pnames[j]
        sim = get_similarity(p1, p2)
        if sim > 0.9:
            id1 = parent_map[p1][0]['menu_item_id']
            id2 = parent_map[p2][0]['menu_item_id']
            if id1 != id2:
                print(f"DUPLICATE CANDIDATE: '{p1}' vs '{p2}' (Sim: {sim:.2f})")

print("\n### 3: Raw Name Conflicts within Parent")
for pname, entries in parent_map.items():
    all_raws = set()
    for e in entries:
        raws = [r.strip() for r in e['raw_names'].split('|') if r.strip() != "(none)"]
        all_raws.update(raws)
    
    # Check if raw names suggest different product
    p_words = set(re.findall(r'\w+', pname.lower()))
    for r in all_raws:
        r_words = set(re.findall(r'\w+', r.lower()))
        # If raw name has distinct product keywords not in parent name
        # (Very naive, but highlights big ones)
        distinct = r_words - p_words - {'ice', 'cream', 'scoop', 'tub', 'perfect', 'plenty', 'junior', 'regular', 'gm', 'gms', 'ml'}
        if len(distinct) > 2: # heuristic
            # print(f"CONFLICT? Parent '{pname}' has Raw '{r}' (Extra words: {distinct})")
            pass

print("\n### 4: Semantic Mismatches")
for c in clusters:
    raw_names = [r.strip() for r in c['raw_names'].split('|') if r.strip() and r.strip() != "(none)"]
    variant = c['child_cluster_name']
    for r in raw_names:
        mismatches = check_semantic_mismatch(r, variant)
        if mismatches:
            print(f"SEMANTIC MISMATCH: Parent '{c['parent_cluster_name']}' Child '{variant}' Raw '{r}' -> {mismatches}")

print("\n### 5: Suspicious Merges")
for m in merges:
    sim = get_similarity(m['source_name'], m['current_target_name'])
    if sim < 0.6:
        print(f"BAD MERGE? ID {m['merge_id']}: '{m['source_name']}' -> '{m['current_target_name']}' (Sim: {sim:.2f})")
    
    # Variant check
    vas = m['variant_assignments']
    if '->' in vas:
        parts = vas.split('->')
        if len(parts) == 2:
            s_v = parts[0].strip()
            t_v = parts[1].strip()
            if s_v != t_v and s_v != 'UNKNOWN' and t_v != 'UNKNOWN' and s_v != '1_PIECE' and t_v != '1_PIECE':
                print(f"VARIANT SHIFT Merge {m['merge_id']}: {s_v} -> {t_v}")

print("\n### 7: Insufficient Evidence")
for c in clusters:
    if not c['raw_names'] or c['raw_names'] == "(none)":
        if int(c['item_row_count']) > 0 or int(c['addon_row_count']) > 0:
            print(f"NO EVIDENCE: '{c['parent_cluster_name']}' :: '{c['child_cluster_name']}' (Rows: {c['item_row_count']}/{c['addon_row_count']})")
