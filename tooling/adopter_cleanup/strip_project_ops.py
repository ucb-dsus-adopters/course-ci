#!/usr/bin/env python3
"""(c) Strip Berkeley operational content from PROJECT notebooks only:
  A) delete the **Checkpoint.** / **Partners.** / **Support.** labeled paragraphs
     from the ## Logistics cell (these hold all the Pensive/lab-section/office-hours/
     Ed/TA/tutor/walkthrough-video references), keeping Rules/Tests/Advice/point-table.
  B) neutralize the submission-platform NAME everywhere else (scattered submit cells):
     Pensive/Pensieve/Gradescope -> generic wording, so the submit instructions still
     read correctly but carry no Berkeley platform.
Markdown-only; does NOT touch autograder_zips. Idempotent."""
import nbformat, re, glob, sys, os

def strip_logistics(t):
    # remove each labeled paragraph up to the next blank line
    return re.sub(r'\n*\*\*(?:Checkpoint|Partners|Support)\.\*\*\s*.*?(?=\n\s*\n|$)',
                  '', t, flags=re.S)

def neutralize_platform(t):
    subs = [
        (r'the (?:Pensive|Pensieve|Gradescope) assignment', 'the assignment'),
        (r'(?:Pensive|Pensieve|Gradescope) assignment', 'assignment'),
        (r'to your (?:Pensive|Pensieve|Gradescope) submission', 'to your submission'),
        (r'submit to (?:Pensive|Pensieve|Gradescope)\b', 'submit'),
        (r'on (?:Pensive|Pensieve|Gradescope)\b', 'on the submission platform'),
        (r'(?:Pensive|Pensieve|Gradescope) does not save', 'the submission platform does not save'),
        (r'\b(?:Pensive|Pensieve|Gradescope)\b', 'the submission platform'),  # catch-all
    ]
    for pat, rep in subs:
        t = re.sub(pat, rep, t)
    return t

def process(nb_path):
    nb = nbformat.read(nb_path, as_version=4)
    changed = False
    for c in nb.cells:
        if c.get('cell_type') != 'markdown':
            continue
        t = ''.join(c['source']) if isinstance(c['source'], list) else c['source']
        new = t
        if re.search(r'\*\*(?:Checkpoint|Partners|Support)\.\*\*', new):
            new = strip_logistics(new)
        if re.search(r'Pensive|Pensieve|Gradescope', new):
            new = neutralize_platform(new)
        new = re.sub(r'\n{3,}', '\n\n', new)
        if new != t:
            c['source'] = new
            changed = True
    if changed:
        nbformat.write(nb, nb_path)
    return changed

def main():
    for label, pattern in [
        ("raw_notebooks",     "materials-fds-private-v2/raw_notebooks/project/**/*.ipynb"),
        ("student_notebooks", "materials-fds-private-v2/student_notebooks/project/**/*.ipynb"),
        ("instructor_nbs",    "materials-fds-private-v2/instructor_notebooks/project/**/*.ipynb"),
        ("public v2",         "materials-fds-v2/project/**/*.ipynb"),
    ]:
        files = sorted(glob.glob(pattern, recursive=True))
        n = sum(process(p) for p in files)
        print(f"  {label:18s}: {n:2d} project notebooks edited  (of {len(files)})")

if __name__ == "__main__":
    os.chdir(sys.argv[1])
    main()
