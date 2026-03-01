"""One-off: Check all Feb 13 company cards for placeholder text."""
import json, re, sys
sys.path.insert(0, ".")
from modules.data.db_utils import get_table_data

PLACEHOLDER_PATTERNS = [
    r"AI Updates:",
    r"AI RULE:",
    r"Set in Static Editor",
    r"Set during initialization",
    r"Your \*?new\*? ",
    r"Your \*?evolved\*? ",
    r"Your \*?first\*? output",
    r"Carry over from \[Previous Card\]",
    r"AI will provide",
]
EXEMPT = set()

def scan(card, prefix=""):
    hits = []
    for k, v in card.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            hits.extend(scan(v, path))
        elif isinstance(v, list):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    hits.extend(scan(item, f"{path}[{i}]"))
        elif isinstance(v, str) and path not in EXEMPT:
            for pat in PLACEHOLDER_PATTERNS:
                if re.search(pat, v, re.IGNORECASE):
                    hits.append((path, v[:120]))
                    break
    return hits

df = get_table_data("aw_company_cards")
df13 = df[df["date"] == "2026-02-13"]
print(f"Found {len(df13)} company cards for 2026-02-13\n")

for _, row in df13.iterrows():
    ticker = row["ticker"]
    card = json.loads(row["company_card_json"])
    hits = scan(card)
    status = f"🔴 {len(hits)} placeholder(s)" if hits else "✅ Clean"
    print(f"{ticker:8s} {status}")
    for field, val in hits:
        print(f"         → {field}: {val}")
