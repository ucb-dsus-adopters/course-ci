#!/usr/bin/env python3
"""Remove residual Berkeley URLs/tools from raw_notebooks: Pensieve submission
links/mentions and Google-Drive study-guide links. Markdown only."""
import nbformat, re, glob, sys, os

def transform(t):
    orig = t
    # 1. ANY markdown link whose URL points at pensieve -> generic (match by URL, not
    #    display text, so already-neutralized "[the submission platform](pensieve.co)" is caught)
    t = re.sub(r'\[[^\]]*\]\([^)]*pensieve[^)]*\)', "your course's submission platform", t, flags=re.I)
    # 2. any remaining bare pensieve URL -> generic
    t = re.sub(r'https?://[^\s)\]<"]*pensieve[^\s)\]<"]*', "your course's submission platform", t, flags=re.I)
    # 3. "Pensieve assignment" -> "submission"
    t = re.sub(r'\bPensi[ev]+e\s+assignment', 'submission', t, flags=re.I)
    # 4. bare Pensieve/Pensive word in prose -> the submission platform, but NOT an image
    #    filename (e.g. pensieve.png) — leave those for a separate call.
    t = re.sub(r'\bPensi[ev]+e\b(?!\.(?:png|jpe?g|gif|svg))', 'the submission platform', t, flags=re.I)
    # 5. drop whole lines carrying a Google-Drive link (guides / how-to videos)
    t = re.sub(r'(?m)^[ \t]*[-*]?[ \t]*.*drive\.google\.com.*\n?', '', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    return t, (t != orig)

def process(p):
    nb = nbformat.read(p, as_version=4); changed = False
    for c in nb.cells:
        if c.get('cell_type') != 'markdown': continue
        s = ''.join(c['source']) if isinstance(c['source'], list) else c['source']
        new, hit = transform(s)
        if hit: c['source'] = new; changed = True
    if changed: nbformat.write(nb, p)
    return changed

if __name__ == "__main__":
    os.chdir(sys.argv[1])
    files = sorted(glob.glob("raw_notebooks/**/*.ipynb", recursive=True))
    n = sum(process(f) for f in files)
    print(f"edited {n} of {len(files)} notebooks")
