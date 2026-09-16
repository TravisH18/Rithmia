"""Auto-interlink Rithmia wiki docs.
Usage:
  python scripts/autolink_wiki.py --dry-run   # report only
  python scripts/autolink_wiki.py --apply      # write links
Idempotent: skips text already inside [...](...), code blocks, inline code, H1 lines.
"""
import re, sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1] / "rithmia" / "docs"
if not ROOT.exists():  # fallback when run from repo root differently
    ROOT = Path("C:/Users/thudson/Documents/git-repos/Rithmia/rithmia/docs")

STOP_ALIASES = {
    "overview", "background", "note", "notes", "intro",
    "rithmia", "deities", "magic", "history", "setting",
    "cosmology", "religion", "creatures", "artifacts",
    "creatures in the area", "creatures found", "points of interest",
    "campaign hooks", "rival group", "backbone elements",
    "arc 1 (tier 1)", "arc 1", "tier 1",
    # generic single-word pages: linking every common noun is wrong
    # (e.g. every "mountains" -> Salphas/mountains.md, every "druid"
    # class mention -> Lichdom/druid.md ritual page)
    "mountains", "mountain", "druid", "druids",
}

def humanize_filename(p: Path) -> str:
    s = p.stem.replace("-", " ").replace("_", " ").strip()
    # title-case each word, keep apostrophes
    return " ".join(w[:1].upper() + w[1:] if w else w for w in s.split())

def primary_title(text: str, f: Path):
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return None

def aliases_for(title: str):
    out = set()
    out.add(title)
    # strip parenthetical, but also extract aka
    m = re.search(r"\((.*?)\)", title)
    if m:
        inner = m.group(1)
        aka = re.search(r"aka\s+(.+)", inner, re.I)
        if aka:
            out.add(aka.group(1).strip())
        base = re.sub(r"\(.*?\)", "", title).strip()
        if base:
            out.add(base)
    # slash parts
    if "/" in title:
        for part in title.split("/"):
            p = part.strip()
            if p:
                out.add(p)
    # comma: bare name + epithet
    if "," in title:
        parts = [p.strip() for p in title.split(",", 1)]
        if parts[0]:
            out.add(parts[0])
        if len(parts) > 1 and parts[1] and len(parts[1]) >= 4:
            out.add(parts[1])
            # epithet without leading "the "? keep both
            no_the = re.sub(r"^the\s+", "", parts[1], flags=re.I).strip()
            if no_the and no_the.lower() not in STOP_ALIASES:
                out.add(no_the)
                out.add("The " + no_the) if not parts[1].lower().startswith("the ") else None
    # leading The
    mt = re.match(r"^the\s+(.+)", title, re.I)
    if mt:
        out.add(mt.group(1).strip())
    # clean empties / stops / too short
    clean = set()
    for a in out:
        a = re.sub(r"\s+", " ", a).strip()
        if len(a) < 4:
            continue
        if a.lower() in STOP_ALIASES:
            continue
        clean.add(a)
    return clean

def collect_files():
    return sorted([p for p in ROOT.rglob("*") if p.is_file() and p.suffix.lower() in (".md", ".mdx")])

def build_index(files):
    # alias_lower -> (alias_display, target_path)
    index = {}
    targets = {}  # target -> primary title
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        t = primary_title(text, f)
        if not t:
            t = humanize_filename(f)
        targets[f] = t
    for f, t in targets.items():
        for a in aliases_for(t):
            key = a.lower()
            # first wins; on collision keep longer title's file? prefer existing (stable)
            if key in STOP_ALIASES:
                continue
            if key not in index:
                index[key] = (a, f)
    # folder-name -> folder overview.md (parent-folder semantics for generic mentions)
    for f in files:
        if f.name.lower() == "overview.md":
            folder = f.parent.name.strip()
            if len(folder) >= 4 and folder.lower() not in STOP_ALIASES:
                key = folder.lower()
                if key not in index:
                    index[key] = (folder, f)
    return index, targets

# split text into protected/unprotected segments
PROTECT_RE = re.compile(
    r"(```.*?```|`[^`\n]+`|\[[^\]]*\]\([^)]*\)|!\[[^\]]*\]\([^)]*\)|https?://\S+)",
    re.DOTALL,
)

def rel_link(src: Path, dst: Path) -> str:
    rel = Path(__import__("os").path.relpath(str(dst), str(src.parent)))
    parts = [quote(p) for p in Path(rel).parts]
    return "/".join(parts)

def process_file(src: Path, index, sorted_aliases, apply: bool):
    text = src.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    # find H1 line indices to skip
    h1_idx = {i for i, l in enumerate(lines) if l.strip().startswith("# ")}
    # build alternation regex once (passed in)
    pattern, lookup = sorted_aliases  # compiled pattern, dict lower->(display,target)
    hits = []
    out_lines = []
    for i, line in enumerate(lines):
        if i in h1_idx:
            out_lines.append(line)
            continue
        # skip frontmatter-ish first lines? only sidebar_position lines starting without prose - still safe to process; skip --- delimiters
        segs = PROTECT_RE.split(line)
        new_segs = []
        for s in segs:
            if not s or PROTECT_RE.fullmatch(s):
                new_segs.append(s)
                continue
            def repl(m):
                matched = m.group(0)
                tgt = lookup.get(matched.lower())
                if not tgt:
                    return matched
                _, dst = tgt
                if dst.resolve() == src.resolve():
                    return matched  # no self-links
                link = rel_link(src, dst)
                hits.append((matched, str(dst.relative_to(ROOT)).replace("\\", "/")))
                if apply:
                    return f"[{matched}]({link})"
                return matched
            new_segs.append(pattern.sub(repl, s))
        # reassemble: if apply, use replaced; else original (hits already counted)
        if apply:
            out_lines.append("".join(new_segs))
        else:
            out_lines.append(line)
    new_text = "".join(out_lines)
    if apply and new_text != text:
        src.write_text(new_text, encoding="utf-8")
    return hits

def main():
    mode = "--apply" if "--apply" in sys.argv else "--dry-run"
    apply = mode == "--apply"
    files = collect_files()
    index, targets = build_index(files)
    print(f"FILES={len(files)} ALIASES={len(index)}")
    # sort aliases longest-first for alternation priority
    ordered = sorted(index.keys(), key=len, reverse=True)
    lookup = {k: index[k] for k in ordered}
    escaped = [re.escape(index[k][0]) for k in ordered]
    # allow optional trailing 's' for plurals? do explicit: alias + s? version
    # Instead of complicating regex, add plural variants into alternation
    plural_extra = []
    for k in ordered:
        disp, dst = index[k]
        if disp.lower() not in STOP_ALIASES and not disp.lower().endswith("s") and len(disp) > 5 and " " in disp:
            plural_extra.append((disp + "s", dst))
    for disp, dst in plural_extra:
        if disp.lower() not in lookup:
            lookup[disp.lower()] = (disp, dst)
            escaped.append(re.escape(disp))
    # rebuild ordered with plurals (longest first)
    escaped_sorted = sorted(escaped, key=len, reverse=True)
    pattern = re.compile(r"(?<!\w)(" + "|".join(escaped_sorted) + r")(?!\w)", re.IGNORECASE)
    total_hits = 0
    per_target = {}
    per_source = {}
    for src in files:
        hits = process_file(src, index, (pattern, lookup), apply=apply)
        if hits:
            per_source[str(src.relative_to(ROOT)).replace("\\", "/")] = len(hits)
            total_hits += len(hits)
            for _, t in hits:
                per_target[t] = per_target.get(t, 0) + 1
    print(f"MODE={mode} TOTAL_MATCHES={total_hits} FILES_WITH_MATCHES={len(per_source)}")
    print("--- TOP TARGETS (top 30) ---")
    for t, c in sorted(per_target.items(), key=lambda x: -x[1])[:30]:
        print(f"{c}x {t}")
    print("--- TOP SOURCES (top 30) ---")
    for s, c in sorted(per_source.items(), key=lambda x: -x[1])[:30]:
        print(f"{c}x {s}")
    if not apply:
        print("Dry run only. Re-run with --apply to write.")

if __name__ == "__main__":
    main()
