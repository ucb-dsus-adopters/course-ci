#!/usr/bin/env python3
"""(b) Remove Berkeley lab-credit-policy blocks + GSI + lab04 partner-form question."""
import nbformat, re, glob, sys, os

def strip_credit_block(t):
    # remove "As a reminder, here are the policies for getting full credit..." through
    # (but not including) the **Submission** line — covers both the labs 03-09 bullet
    # form and the lab02 Regular-Labs/Self-Service numbered form.
    t = re.sub(r'\n*As a reminder, here are the policies for getting full credit.*?(?=\*\*Submission\*\*)',
               '\n\n', t, flags=re.S)
    # neutralize the remaining GSI mention in the "Getting help" line
    t = re.sub(r'\bfind a GSI or tutor\b', 'find an instructor or tutor', t)
    t = re.sub(r'\bGSI\b', 'instructor', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    return t

def process(p):
    nb = nbformat.read(p, as_version=4)
    changed = False
    # 1. credit-policy block in markdown cells
    for c in nb.cells:
        if c.get('cell_type') != 'markdown':
            continue
        s = ''.join(c['source']) if isinstance(c['source'], list) else c['source']
        if 'policies for getting full credit' in s or 'GSI' in s:
            new = strip_credit_block(s)
            if new != s:
                c['source'] = new; changed = True
    # 2. lab04 only: delete the "Project 1 Partner Form" graded question wholesale
    idx = [i for i, c in enumerate(nb.cells)
           if c.get('cell_type') == 'markdown'
           and 'Project 1 Partner Form' in (''.join(c['source']) if isinstance(c['source'], list) else c['source'])]
    if idx:
        m = idx[0]
        begins = [i for i in range(m) if nb.cells[i].get('cell_type') == 'raw'
                  and 'BEGIN QUESTION' in ''.join(nb.cells[i]['source'])]
        ends = [i for i in range(m, len(nb.cells)) if nb.cells[i].get('cell_type') == 'raw'
                and 'END QUESTION' in ''.join(nb.cells[i]['source'])]
        if begins and ends:
            del nb.cells[begins[-1]:ends[0] + 1]
            changed = True
    if changed:
        nbformat.write(nb, p)
    return changed

if __name__ == "__main__":
    os.chdir(sys.argv[1])
    n = 0
    for l in ['lab02','lab03','lab04','lab05','lab06','lab07','lab08','lab09']:
        p = f'raw_notebooks/lab/{l}/{l}.ipynb'
        if os.path.exists(p) and process(p):
            n += 1; print(f"edited {l}")
    print(f"total: {n}")
