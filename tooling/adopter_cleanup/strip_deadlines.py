#!/usr/bin/env python3
"""Remove ONLY the Deadline blocks from student-facing notebooks (markdown-only,
no grading impact). Three variants:
  - hw:      **Deadline:**  + following paragraph
  - project: **Deadline.**  + its paragraph (leaves the rest of ## Logistics intact)
  - lab:     bold "... Submission Deadline: ..." single line
Does NOT touch autograder_zips. Idempotent."""
import nbformat, re, glob, sys, os

PATS = [
    r'\n*\*\*Deadline:\*\*\s*\n+.*?(?=\n\s*\n|$)',          # hw block
    r'\n*\*\*Deadline\.\*\*\s*.*?(?=\n\s*\n|$)',            # project paragraph
    r'\n*\*\*[^*\n]*Submission Deadline:[^*\n]*\*\*[ \t]*',  # lab bold line
]

def strip_deadline(src):
    new, hit = src, False
    for pat in PATS:
        new2, n = re.subn(pat, '', new, count=1, flags=re.S | re.I)
        if n:
            hit = True
            new = new2
    if not hit:
        return src, False
    new = re.sub(r'\n{3,}', '\n\n', new)
    return new, True

def process(nb_path):
    nb = nbformat.read(nb_path, as_version=4)
    changed = False
    for c in nb.cells:
        if c.get('cell_type') != 'markdown':
            continue
        t = ''.join(c['source']) if isinstance(c['source'], list) else c['source']
        new, hit = strip_deadline(t)
        if hit:
            c['source'] = new
            changed = True
    if changed:
        nbformat.write(nb, nb_path)
    return changed

def main():
    for label, pattern in [
        ("raw_notebooks",     "materials-fds-private-v2/raw_notebooks/**/*.ipynb"),
        ("student_notebooks", "materials-fds-private-v2/student_notebooks/**/*.ipynb"),
        ("instructor_nbs",    "materials-fds-private-v2/instructor_notebooks/**/*.ipynb"),
        ("public v2",         "materials-fds-v2/**/*.ipynb"),
    ]:
        files = sorted(glob.glob(pattern, recursive=True))
        n = sum(process(p) for p in files)
        print(f"  {label:18s}: {n:2d} notebooks edited  (of {len(files)} scanned)")

if __name__ == "__main__":
    os.chdir(sys.argv[1])
    main()
