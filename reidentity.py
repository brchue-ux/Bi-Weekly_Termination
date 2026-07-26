"""
Full re-identity of the de-identified rosters (user-authorized 2026-07-23).

Why: the original de-identification left real names in the sheets (UserName-column
desyncs like 'LaTroyce Usher', whitespace-randomizer escapees, real emails in the
Invalid-UPN tab), and those names were carried into Okta. Mandate: no name from
these worksheets may exist in Okta. Fix at the root: every identity in BOTH
workbooks gets a fresh name, worksheets and tenant rewritten in lockstep.

Design:
  - One deterministic map (seed 20260723): identity anchor -> (First, Last).
    Anchor = UPN/email local part when valid, else first|last pair, else alias.
    Same person across tabs maps to the same new identity (join semantics survive).
  - New names come from a curated international pool, filtered so that NO first or
    last token appearing in any identity cell of the originals survives into the
    pool. Clearly-fictional-to-the-source guarantee by construction + filter.
  - Per-tab schemes preserved: alias case/format, MERRYCORP\\ prefixes, CloudForce
    .cf/.ca login suffixes, email case style, display-name padding. The 70
    name-in-status defect rows stay defective — with the NEW names.
  - Non-identity cells are copied verbatim; row/tab counts unchanged.
  - Originals backed up; map saved next to them. Verification is SEPARATE
    (reidentity_verify.py) per the project's verification gate.
"""

import json
import random
import re
import shutil
from pathlib import Path

from xlsx_min import load_workbook_rows
from xlsx_write import write_xlsx

BASE = Path(__file__).parent / "App User Lists"
STARS = "FAKE USERS - STARS Report.xlsx"
EXCEPT = "FAKE USERS - Exception List.xlsx"
BACKUP = BASE / ".originals" / "pre_reidentity_20260723"
MAP_OUT = BASE / ".originals" / "reidentity_map_20260723.json"
SEED = 20260723

STATUSES = {"Active", "Paid Leave", "Unpaid Leave", "Terminated", "Retired",
            "Not found in TalentHub", ""}
SENTINELS = ("Not found in TalentHub", "Not Available in")
INFRA = {"merrycorp", "bitermtest", "com", "noemail"}  # non-name infrastructure tokens

FIRST = """Amara Naoko Kenji Priya Rohan Ines Mateo Lucia Tomasz Agnieszka Bogdan Ilya
Katya Dmitri Sanjay Meera Arjun Kavita Ravi Anika Farid Leila Omar Yasmin Tariq Zainab
Kofi Amina Chidi Ngozi Femi Adaeze Thabo Naledi Sipho Yuki Haruto Sakura Ren Aoi Minho
Jisoo Hana Soren Freja Mikkel Astrid Lars Ingrid Bjorn Sigrid Matteo Chiara Alessandro
Giulia Lorenzo Bianca Rafael Camila Thiago Fernanda Joao Beatriz Diego Valentina Andres
Ximena Nikolai Oksana Petro Yevhen Marta Zofia Piotr Henryk Elzbieta Janusz Renata Vasil
Milan Dragan Jovana Nemanja Teodora Anouk Willem Sanne Jeroen Femke Daan Lotte Bram
Aoife Cillian Niamh Padraig Siobhan Declan Roisin Eamon Aylin Emre Deniz Cem Elif Baran
Seda Volkan Anwar Basim Dalia Eshan Firuza Gita Hamid Imani Jelani Kamal Laleh Mahmoud
Parvin Qasim Rashida Suleiman Tahira Umar Vandana Wafa Xiomara Yusuf Zahra Bao Linh
Minh Thao Trung Huong Quang Duc Mei Xiu Jing Feng Hui Lan Ming Tao Yan Marisol Itzel
Citlali Yaretzi Tenoch Xochitl Nayeli Anahi Cuauhtemoc Maite Amaia Nerea Unai Iker
Ainhoa Eneko Oihane Gorka Izaro Maialen""".split()

LAST = """Okonkwo Adeyemi Chukwu Nwosu Obiora Ezeani Afolabi Balogun Mensah Boateng
Asante Owusu Diallo Toure Keita Traore Ndiaye Sowande Camara Fofana Watanabe Kobayashi
Yamamoto Nakamura Takahashi Fujimoto Ishikawa Matsuda Hoshino Kuroda Sakamoto Uchida
Jeong Hwang Yoon Kwon Seo Bae Gupta Mehta Iyer Nair Menon Pillai Reddy Rao Chatterjee
Banerjee Mukherjee Bhattacharya Desai Trivedi Joshi Kulkarni Deshpande Agarwal Malhotra
Kapoor Chopra Saxena Varma Kowalski Nowak Wisniewski Wojcik Kaminski Lewandowski
Zielinski Szymanski Wozniak Dabrowski Kozlowski Jankowski Mazur Krawczyk Piotrowski
Grabowski Novak Horvat Kovac Babic Jovanovic Petrovic Nikolic Markovic Djordjevic
Stojanovic Ivanov Petrov Sokolov Volkov Morozov Novikov Fedorov Orlov Egorov Titov
Ferreira Oliveira Carvalho Almeida Ribeiro Barbosa Cardoso Teixeira Fonseca Azevedo
Rossi Ferrari Esposito Ricci Marino Greco Gallo Conti Deluca Moretti Barone Vandenberg
Devries Visser Meijer Mulder Bosman Vosberg Dekker Hendriks Lindqvist Bergstrom Nystrom
Holmgren Sandberg Forsberg Lundgren Axelsson Dahlberg Engstrom Yilmaz Kaya Demir Celik
Sahin Aydin Ozturk Arslan Dogan Kilic Aksoy Polat Kocak Aslan Haddad Nassar Khoury
Sleiman Fakhoury Maalouf Ghanem Saliba Rahal Baraka Pham Hoang Vuong Dang Bui Ngo
Duong Truong Lai Mifsud Borg Vella Farrugia Zammit Camilleri Galea Micallef Grech
Attard Cassar Ohtani Kirigaya Amari Tesfaye Bekele Girma Abebe Alemu Haile Mekonnen
Desta Kebede Lemma Worku""".split()


# ------------------------------------------------------------ harvesting originals

def valid_email(s):
    return "@" in s and " " not in s.strip() and not s.strip().startswith("@")


def local_of(s):
    return s.strip().split("@")[0].lower()


def split_local(s):
    """Normalize an email local: -> (base identity key, trailing digits, .cf/.ca suffix).
    'jphilpott1' -> ('jphilpott', '1', ''); 'Emery.White.cf' -> ('emery.white', '', '.cf')."""
    loc = local_of(s)
    m = re.fullmatch(r"(.*?)\.(cf|ca)", loc)
    cfca = f".{m.group(2)}" if m else ""
    if m:
        loc = m.group(1)
    m = re.fullmatch(r"(.*?)(\d+)", loc)
    return (m.group(1), m.group(2), cfca) if m else (loc, "", cfca)


def name_tokens(s):
    """Name-ish tokens from an identity cell: split all separators, alpha, len>=3."""
    return {t.lower() for t in re.split(r"[\s.\\@,_/-]+", s)
            if t.isalpha() and len(t) >= 3}


def is_sentinel(v):
    return any(v.strip().startswith(s) for s in SENTINELS)


def identity_columns(header):
    """Column index -> kind, from a header-row dict."""
    kinds = {}
    for ci, name in header.items():
        n = str(name).strip()
        if n.endswith(("_NetworkAlias",)):
            kinds[ci] = "alias"
        elif n.endswith(("_UserName", "_USERNAME")):
            kinds[ci] = "username"
        elif n.endswith("_EmailAddress") or n in ("TH_UPN", "TH_BusinessEmail"):
            kinds[ci] = "email"
        elif n == "TH_EmployeeStatus":
            kinds[ci] = "status"
        elif n == "TH_FirstName":
            kinds[ci] = "first"
        elif n == "TH_LastName":
            kinds[ci] = "last"
    return kinds


def find_header(rows):
    for i, r in enumerate(rows[:4]):
        if any(str(v).strip() == "TH_EmployeeID" for v in r.values()):
            return i
    return None


def harvest(stars, exc):
    """(exclusion token set, ordered list of identity anchors)."""
    tokens, anchors = set(), []

    def add_anchor(key):
        if key and key not in seen:
            seen.add(key)
            anchors.append(key)
    seen = set()

    for tab, rows in stars.items():
        hi = find_header(rows)
        if hi is None:
            continue
        kinds = identity_columns(rows[hi])
        for r in rows[hi + 1:]:
            vals = {ci: str(r.get(ci, "")) for ci in kinds}
            if not any(v.strip() for v in vals.values()):
                continue
            upn, first, last, alias = "", "", "", ""
            for ci, kind in kinds.items():
                v = vals[ci]
                if is_sentinel(v) or not v.strip():
                    continue
                if kind in ("email", "username") and valid_email(v):
                    tokens |= name_tokens(v.split("@")[0])
                    add_anchor(split_local(v)[0])   # EVERY distinct local is an identity
                    if kind == "email" and not upn:
                        upn = split_local(v)[0]
                elif kind == "alias":
                    tokens |= name_tokens(v)
                    alias = v.strip().lower()
                elif kind == "username":
                    tokens |= name_tokens(v.split("@")[0] if "@" in v else v)
                elif kind == "status" and v.strip() not in STATUSES:
                    tokens |= name_tokens(v)
                elif kind == "first":
                    tokens |= name_tokens(v)
                    first = v.strip().lower()
                elif kind == "last":
                    tokens |= name_tokens(v)
                    last = v.strip().lower()
            add_anchor(upn or (f"namepair:{first}|{last}" if (first or last) else
                               (f"alias:{alias}" if alias else "")))

    for tab, rows in exc.items():
        for r in rows[1:]:
            name, upn, alias, owner = (str(r.get(i, "")) for i in (0, 1, 3, 6))
            if not upn.strip():
                continue
            for v in (name, alias, upn.split("@")[0], owner.split("@")[0]):
                tokens |= name_tokens(v)
            add_anchor(local_of(upn))
            if valid_email(owner):
                add_anchor(local_of(owner))
    return tokens, anchors


def collect_vocab(stars):
    """Tokens appearing in NON-identity cells of the original STARS book (headers,
    Header tab, sentinels, job titles...) — legit vocabulary, never treated as a
    name leak even if some identity cell also contained the same token."""
    vocab = set()
    for tab, rows in stars.items():
        hi = find_header(rows)
        kinds = identity_columns(rows[hi]) if hi is not None else {}
        for ri, r in enumerate(rows):
            for ci, v in r.items():
                v = str(v)
                if hi is not None and ri > hi and ci in kinds and not is_sentinel(v):
                    continue
                vocab |= name_tokens(v)
    return vocab


def scrub(sheets, forbidden):
    """Final guarantee pass over rewritten output: any forbidden token that
    survived the structured rewrite (malformed junk cells the email/alias rules
    can't parse) is replaced token-wise, deterministically, case-mimicked.
    Returns replacement count."""
    subs = {}
    hits = 0

    def sub_token(m):
        nonlocal hits
        t = m.group(0)
        if t.lower() not in forbidden:
            return t
        hits += 1
        if t.lower() not in subs:
            subs[t.lower()] = LAST[random.Random(t.lower()).randrange(len(LAST))]
        new = subs[t.lower()]
        return new.upper() if t.isupper() else (new.lower() if t.islower() else new)

    pat = re.compile(r"[A-Za-z]{3,}")
    for _, rows in sheets:
        for row in rows:
            for ci, v in enumerate(row):
                if pat.search(v):
                    row[ci] = pat.sub(sub_token, v)
    return hits


# ------------------------------------------------------------ mapping construction

def build_map(tokens, anchors):
    """Full synthetic replacement (enterprise-sufficient scrubbing: no source
    token survives — rotation/shuffle schemes were considered and rejected
    2026-07-23 because permuted real values, esp. unmoved rare surnames and
    initial+surname stems, remain re-identifiable)."""
    firsts = [n for n in FIRST if n.lower() not in tokens]
    lasts = [n for n in LAST if n.lower() not in tokens]
    # a combo is unusable if ANY derived form (dotted local, concat stem) collides
    # with an original token — e.g. new 'L* Fonseca' would emit stem 'lfonseca'
    combos = [(f, l) for f in firsts for l in lasts
              if f"{f}.{l}".lower() not in tokens and f"{f[0]}{l}".lower() not in tokens]
    if len(combos) < len(anchors):
        raise SystemExit(f"pool too small: {len(combos)} combos for {len(anchors)} anchors")
    rng = random.Random(SEED)
    rng.shuffle(combos)
    print(f"pool: {len(firsts)} first x {len(lasts)} last = {len(combos)} combos "
          f"({len(FIRST) - len(firsts)} first / {len(LAST) - len(lasts)} last removed by filter); "
          f"{len(anchors)} anchors")
    return {a: combos[i] for i, a in enumerate(sorted(anchors))}


# ------------------------------------------------------------ cell rewriting

def mimic_case(orig_local, first, last):
    """New local in the ORIGINAL's shape: dotted stays dotted, off-scheme concat
    stems stay concat (initial+last), case style preserved."""
    new = f"{first}.{last}" if "." in orig_local else f"{first[0]}{last}"
    if orig_local == orig_local.upper():
        return new.upper()
    if orig_local == orig_local.lower():
        return new.lower()
    return f"{first.title()}.{last.title()}" if "." in orig_local else f"{first[0].upper()}{last.title()}"


def rewrite_email(orig, mapping):
    """Rebuild an email/UPN cell by ITS OWN local's mapped identity, preserving
    domain text, case style, digit suffixes, and CloudForce .cf/.ca suffixes.
    Returns None when the local has no mapping (caller decides)."""
    local, domain = orig.strip().split("@", 1)
    base, digits, cfca = split_local(orig)
    if base not in mapping:
        return None
    nf, nl = mapping[base]
    core = local[:len(local) - len(digits) - len(cfca)]
    return f"{mimic_case(core, nf, nl)}{digits}{cfca}@{domain}"


def pad_like(orig, new):
    if orig != orig.rstrip() and len(orig) > len(new):
        return new + " " * (len(orig) - len(new))
    return new


def rewrite_tab(rows, mapping):
    hi = find_header(rows)
    width = max((max(r.keys(), default=0) for r in rows), default=0) + 1
    out = [[str(r.get(c, "")) for c in range(width)] for r in rows]
    if hi is None:
        return out
    kinds = identity_columns(rows[hi])
    for ri, r in enumerate(rows[hi + 1:], hi + 1):
        vals = {ci: str(r.get(ci, "")) for ci in kinds}
        if not any(v.strip() for v in vals.values()):
            continue
        # resolve this row's identity anchor exactly as harvest() did
        upn = next((split_local(vals[ci])[0] for ci, k in kinds.items()
                    if k == "email" and valid_email(vals[ci]) and not is_sentinel(vals[ci])), "")
        first_c = next((vals[ci].strip().lower() for ci, k in kinds.items() if k == "first"), "")
        last_c = next((vals[ci].strip().lower() for ci, k in kinds.items() if k == "last"), "")
        alias_c = next((vals[ci].strip().lower() for ci, k in kinds.items()
                        if k == "alias" and vals[ci].strip() and not is_sentinel(vals[ci])), "")
        key = upn or (f"namepair:{first_c}|{last_c}" if (first_c or last_c) else
                      (f"alias:{alias_c}" if alias_c else ""))
        if key not in mapping:
            continue
        nf, nl = mapping[key]
        for ci, kind in kinds.items():
            v = vals[ci]
            if not v.strip() or is_sentinel(v):
                continue
            if kind in ("email", "username") and valid_email(v):
                new = rewrite_email(v, mapping)          # per-cell: desynced locals
                out[ri][ci] = new if new else v          # keep their own identities
            elif kind == "alias":
                s = v.strip()
                if "." in s:
                    out[ri][ci] = mimic_case(s, nf, nl)
                else:                       # Saturn Corp concat scheme: initial+last
                    out[ri][ci] = (nf[0] + nl).upper()
            elif kind == "username":
                s = v.rstrip()
                if "\\" in s:               # MERRYCORP\FIRST.LAST
                    prefix, tail = s.split("\\", 1)
                    out[ri][ci] = f"{prefix}\\{mimic_case(tail.strip(), nf, nl)}"
                else:                       # display name, possibly padded
                    out[ri][ci] = pad_like(v, f"{nf} {nl}")
            elif kind == "status" and v.strip() not in STATUSES:
                out[ri][ci] = pad_like(v, f"{nf} {nl}")   # keep the defect, new name
            elif kind == "first":
                out[ri][ci] = pad_like(v, nf)
            elif kind == "last":
                out[ri][ci] = pad_like(v, nl)
    return out


def rewrite_exceptions(rows, mapping):
    width = max((max(r.keys(), default=0) for r in rows), default=0) + 1
    out = [[str(r.get(c, "")) for c in range(width)] for r in rows]
    for ri, r in enumerate(rows[1:], 1):
        upn = str(r.get(1, "")).strip()
        if not upn:
            continue
        key = split_local(upn)[0]
        if key in mapping:
            nf, nl = mapping[key]
            out[ri][0] = f"{nf} {nl}"
            out[ri][1] = rewrite_email(upn, mapping)
            alias = str(r.get(3, "")).strip()
            if alias:
                out[ri][3] = mimic_case(alias, nf, nl) if "." in alias else (nf[0] + nl).upper()
        owner = str(r.get(6, "")).strip()
        if valid_email(owner):
            new = rewrite_email(owner, mapping)
            if new:
                out[ri][6] = new
    return out


def main():
    # re-run safe: once a backup exists, ORIGINALS are the source of truth —
    # never re-read (and double-rewrite) already-rewritten files
    src = BACKUP if (BACKUP / STARS).exists() else BASE
    stars = load_workbook_rows(src / STARS)
    exc = load_workbook_rows(src / EXCEPT)

    tokens, anchors = harvest(stars, exc)
    print(f"exclusion tokens: {len(tokens)}; identity anchors: {len(anchors)}")
    mapping = build_map(tokens, anchors)

    if src is BASE:
        BACKUP.mkdir(parents=True, exist_ok=True)
        for f in (STARS, EXCEPT):
            shutil.copy2(BASE / f, BACKUP / f)
        print(f"originals backed up -> {BACKUP}")
    else:
        print(f"re-run: reading originals from {BACKUP}")

    new_stars = [(tab, rewrite_tab(rows, mapping)) for tab, rows in stars.items()]
    new_exc = [(tab, rewrite_exceptions(rows, mapping)) for tab, rows in exc.items()]

    forbidden = tokens - collect_vocab(stars) - INFRA
    scrubbed = scrub(new_stars, forbidden) + scrub(new_exc, forbidden)
    print(f"scrub pass: {scrubbed} residual forbidden tokens replaced")

    write_xlsx(BASE / STARS, new_stars)
    write_xlsx(BASE / EXCEPT, new_exc)

    json.dump({k: {"first": f, "last": l} for k, (f, l) in mapping.items()},
              open(MAP_OUT, "w"), indent=0)
    print(f"rewrote {STARS} + {EXCEPT}; map ({len(mapping)}) -> {MAP_OUT}")
    print("NOT VERIFIED — run reidentity_verify.py")


if __name__ == "__main__":
    main()
