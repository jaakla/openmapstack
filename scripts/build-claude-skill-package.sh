#!/usr/bin/env bash
# Build one uploadable zip per skill in collection.json, from the working tree,
# for claude.ai / Claude Desktop (Settings > Capabilities > Skills > Upload).
# Each zip holds a single top-level <skill-name>/ folder with SKILL.md inside.
#
# Usage: scripts/build-claude-skill-package.sh [--out DIR] [SKILL ...]
#   --out DIR  output directory (default: dist/claude-skills)
#   SKILL      build only these skills (default: the whole collection)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/dist/claude-skills"
SKILLS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="$(mkdir -p "$2" && cd "$2" && pwd)"; shift 2 ;;
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) SKILLS+=("$1"); shift ;;
  esac
done

# Installed copies of shared references/templates must match their canonical
# sources; otherwise the package would ship stale guidance.
python3 "$ROOT/scripts/sync_skill_assets.py" --check || {
  echo "Shared skill assets have drifted; run scripts/sync_skill_assets.py first." >&2
  exit 1
}

mkdir -p "$OUT"
python3 - "$ROOT" "$OUT" "${SKILLS[@]}" <<'EOF'
import json, re, sys, zipfile
from pathlib import Path

root, out, wanted = Path(sys.argv[1]), Path(sys.argv[2]), set(sys.argv[3:])
skills = json.loads((root / "collection.json").read_text())["skills"]
unknown = wanted - {s["name"] for s in skills}
if unknown:
    sys.exit(f"Unknown skill(s): {', '.join(sorted(unknown))}")

EXCLUDED_NAMES = {".DS_Store", ".env"}
for skill in skills:
    if wanted and skill["name"] not in wanted:
        continue
    src = root / skill["path"]
    text = (src / "SKILL.md").read_text()
    front = re.match(r"---\n(.*?)\n---\n", text, re.S)
    name = re.search(r"^name:\s*\"?([^\"\n]+)\"?\s*$", front.group(1), re.M) if front else None
    desc = re.search(r"^description:\s*\"?(.*?)\"?\s*$", front.group(1), re.M) if front else None
    if not name or name.group(1) != src.name:
        sys.exit(f"{src}/SKILL.md: frontmatter name must equal folder name {src.name!r}")
    if not desc or len(desc.group(1)) > 1024:
        sys.exit(f"{src}/SKILL.md: description missing or over 1024 characters")

    target = out / f"{src.name}.zip"
    files = sorted(
        f for f in src.rglob("*")
        if f.is_file() and "__pycache__" not in f.parts
        and f.suffix != ".pyc" and f.name not in EXCLUDED_NAMES
        and not f.name.startswith(".env.")
    )
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, f.relative_to(src.parent).as_posix())
    print(f"{target}  ({len(files)} files, {target.stat().st_size // 1024} KB)")
EOF

rev="$(git -C "$ROOT" rev-parse --short HEAD)"
if [[ -n "$(git -C "$ROOT" status --porcelain -- skills collection.json)" ]]; then
  echo "Built from $rev plus uncommitted changes under skills/."
else
  echo "Built from $rev."
fi
