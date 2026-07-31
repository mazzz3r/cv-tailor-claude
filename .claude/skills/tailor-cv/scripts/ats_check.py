#!/usr/bin/env python3
"""ATS-проверка собранного резюме (PDF) перед откликом.

Проверки:
  1. profile.json заполнен (нет __FILL_ME__)               — FAIL
  2. Покрытие ключевых слов вакансии (keywords.tsv)        — информационно
  3. Аномалии извлечения текста (битые токены, U+FFFD)     — FAIL/WARN
  4. Имя человека присутствует в PDF                       — FAIL (снимается --anonymous)
  5. Комплект контактов соответствует рынку (--market)     — FAIL
  6. Запрещённые [gap]-термины (gap-blacklist.txt)         — FAIL
  7. Provenance: каждый содержательный блок .tex несёт
     % FACT: <id>, и каждый ID существует в facts.md       — FAIL

Корень репозитория ищется автоматически: вверх от --pdf до папки с profile.json.
Пути можно задать и явно (--root / --profile / --facts / --blacklist).

Выход: markdown-отчёт в stdout. Коды: 0 — PASS, 1 — FAIL, 2 — ошибка окружения.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata

PLACEHOLDER = "__FILL_ME__"
WORDCH = r"A-Za-zА-Яа-яЁё0-9+#"


# --------------------------------------------------------------------------- io

def find_root(start):
    """Вверх от start до папки с profile.json."""
    cur = os.path.abspath(start if os.path.isdir(start) else os.path.dirname(start))
    while True:
        if os.path.isfile(os.path.join(cur, "profile.json")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def extract_pdf_text(pdf_path):
    if shutil.which("pdftotext"):
        r = subprocess.run(["pdftotext", pdf_path, "-"], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"pdftotext failed: {r.stderr.strip()}")
        return r.stdout
    try:
        from pypdf import PdfReader
        return "\f".join(p.extract_text() or "" for p in PdfReader(pdf_path).pages)
    except ImportError:
        print("ERROR: нет ни pdftotext, ни pypdf. Установить: brew install poppler",
              file=sys.stderr)
        sys.exit(2)


def normalize(text):
    t = unicodedata.normalize("NFC", text)
    t = re.sub(r"-\n(?=[a-zа-яё])", "", t)      # склейка переносов по дефису
    t = t.replace("ё", "е").replace("Ё", "Е")
    t = re.sub(r"[–—−]", "-", t)                # унификация тире
    t = t.replace("­", "")                 # мягкий перенос
    return t.casefold()


def kw_pattern(kw):
    esc = re.escape(kw.casefold().replace("ё", "е"))
    esc = esc.replace(r"\ ", r"\s+")            # пробел = любой whitespace
    return re.compile(rf"(?<![{WORDCH}]){esc}(?![{WORDCH}])")


# ------------------------------------------------------------------- профиль

def markets(profile):
    return [k for k in profile.get("contacts", {}) if not k.startswith("_")]


def contact_values(profile, market):
    """Все непустые контактные значения рынка (email, phone, что угодно ещё)."""
    block = profile["contacts"][market]
    return [v.strip() for k, v in block.items()
            if not k.startswith("_") and k != "label" and isinstance(v, str) and v.strip()]


def name_variants(profile):
    names = profile.get("identity", {}).get("name", {})
    return [v.strip() for v in names.values() if isinstance(v, str) and v.strip()]


def check_profile(profile, market, anonymous, out):
    """Незаполненный profile.json — самая частая причина мусора в отклике."""
    ok, bad = True, []
    if not anonymous:
        bad += [n for n in name_variants(profile) if PLACEHOLDER in n]
    if market:
        bad += [c for c in contact_values(profile, market) if PLACEHOLDER in c]
    out.append("## profile.json\n")
    if bad:
        ok = False
        out.append(f"- FAIL: в profile.json остались незаполненные поля ({PLACEHOLDER}): "
                   f"{len(bad)} шт. Заполнить перед сборкой отклика.")
    else:
        out.append("- OK: профиль заполнен.")
    out.append("")
    return ok


# -------------------------------------------------------------------- проверки

def check_coverage(norm_text, keywords, out):
    out.append("## Покрытие ключевых слов\n")
    out.append("| Ключевое слово | Тир | Найдено | Вхождений |")
    out.append("|---|---|---|---|")
    missing_must, must_total, nice_total, must_hit, nice_hit = [], 0, 0, 0, 0
    for tier, kw in keywords:
        n = len(kw_pattern(kw).findall(norm_text))
        out.append(f"| {kw} | {tier} | {'да' if n else '**НЕТ**'} | {n} |")
        if tier == "must":
            must_total += 1
            must_hit += bool(n)
            if not n:
                missing_must.append(kw)
        else:
            nice_total += 1
            nice_hit += bool(n)
    out.append("")
    pct = lambda a, b: f"{a}/{b} ({round(100 * a / b)}%)" if b else "—"
    out.append(f"- must: {pct(must_hit, must_total)} · nice: {pct(nice_hit, nice_total)}")
    if missing_must:
        out.append(f"- WARN: непокрытые must-слова: {', '.join(missing_must)}. "
                   "Есть реальный факт — переформулировать резюме; факта нет — оставить "
                   "непокрытым и вынести в сопроводительное (в резюме НЕ дописывать).")
    out.append("")
    return True  # информационно, на PASS/FAIL не влияет


def check_anomalies(raw_text, max_pages, out):
    ok = True
    out.append("## Аномалии извлечения\n")
    broken = sorted(set(re.findall(rf"[{WORDCH}]+/\s", raw_text)))
    if broken:
        ok = False
        out.append(f"- FAIL: битые слэш-токены (перенос после «/»): {broken}. "
                   "Обернуть сокращение в \\mbox{}.")
    if "�" in raw_text:
        ok = False
        out.append("- FAIL: найден U+FFFD — глифы без Unicode-маппинга "
                   "(проверить \\input{glyphtounicode} и \\pdfgentounicode=1).")
    if not raw_text.strip():
        ok = False
        out.append("- FAIL: из PDF не извлекается текст вообще — ATS увидит пустой документ.")
    longtok = sorted(set(re.findall(rf"[{WORDCH}]{{41,}}", raw_text)))
    if longtok:
        out.append(f"- WARN: токены длиннее 40 символов (склейка?): {longtok[:5]}")
    pages = raw_text.count("\f") or 1
    if pages > max_pages:
        out.append(f"- WARN: страниц {pages} при бюджете {max_pages} — сокращать по "
                   "порядку из facts.md/README.")
    if ok and not longtok and pages <= max_pages:
        out.append(f"- OK: извлечение чистое, страниц: {pages}.")
    out.append("")
    return ok


def _present(value, squashed, nospace, digits):
    """Контакт найден в тексте. Телефоны сверяются по цифрам, чтобы разное
    форматирование (+7 (900) 000-11-22 vs +7 900 000 11 22) не давало ложный FAIL."""
    d = re.sub(r"\D", "", value)
    if len(d) >= 7 and not re.search(r"[A-Za-z@]", value):
        return d in digits
    return value in squashed or value.replace(" ", "") in nospace


def check_name(raw_text, profile, out):
    out.append("## Имя\n")
    norm = normalize(raw_text)
    variants = name_variants(profile)
    if not variants:
        out.append("- WARN: в profile.json не задано имя — проверка пропущена.")
        out.append("")
        return True
    for v in variants:
        if all(kw_pattern(tok).search(norm) for tok in v.split()):
            out.append(f"- OK: имя найдено в PDF ({v}).")
            out.append("")
            return True
    out.append(f"- FAIL: ни один вариант имени не найден в PDF: {variants}. "
               "Если резюме намеренно обезличено — запускать с --anonymous.")
    out.append("")
    return False


def check_contacts(raw_text, profile, market, out):
    ok = True
    label = profile["contacts"][market].get("label", market)
    out.append(f"## Контакты (рынок: {market} — {label})\n")

    squashed = re.sub(r"\s+", " ", raw_text)
    nospace = re.sub(r"\s+", "", raw_text)
    digits = re.sub(r"\D", "", squashed)

    need = contact_values(profile, market)
    other = {v for m in markets(profile) if m != market for v in contact_values(profile, m)}
    deny = sorted(other - set(need))

    for c in need:
        if not _present(c, squashed, nospace, digits):
            ok = False
            out.append(f"- FAIL: нет обязательного контакта: {c}")
    for c in deny:
        if _present(c, squashed, nospace, digits):
            ok = False
            out.append(f"- FAIL: контакт чужого рынка: {c} — комплекты не смешивать.")
    if ok:
        out.append("- OK: комплект контактов соответствует рынку.")
    out.append("")
    return ok


def check_blacklist(norm_text, path, out):
    ok = True
    out.append("## Blacklist ([gap]-термины)\n")
    if not os.path.isfile(path):
        out.append(f"- WARN: файл не найден: {path} — проверка пропущена.")
        out.append("")
        return True
    n = 0
    with open(path, encoding="utf-8") as f:
        for ln in f:
            term = ln.strip()
            if not term or term.startswith("#"):
                continue
            n += 1
            if kw_pattern(term).search(norm_text):
                ok = False
                out.append(f"- FAIL: [gap]-термин в резюме: «{term}» — убрать (допустим "
                           "только в сопроводительном как «готов освоить»).")
    if ok:
        out.append(f"- OK: запрещённых терминов нет (проверено {n}).")
    out.append("")
    return ok


STRUCTURAL = re.compile(
    r"^\s*(\\(section|begin|end|vspace|newpage|par|hfill|item\s*$)|%|$)")


def tex_blocks(tex_path):
    """Содержательные блоки тела документа: (первая строка, есть ли % FACT:)."""
    body, inside = [], False
    with open(tex_path, encoding="utf-8") as f:
        for ln in f:
            if r"\begin{document}" in ln:
                inside = True
                continue
            if r"\end{document}" in ln:
                break
            if inside:
                body.append(ln.rstrip("\n"))
    blocks, cur = [], []
    for ln in body + [""]:
        if ln.strip():
            cur.append(ln)
        elif cur:
            blocks.append(cur)
            cur = []
    for blk in blocks:
        if any(not STRUCTURAL.match(l) for l in blk):
            yield next(l.strip() for l in blk if l.strip()), any("% FACT:" in l for l in blk)


def check_provenance(tex_path, facts_path, out):
    ok = True
    out.append("## Provenance (строка → факт)\n")
    with open(facts_path, encoding="utf-8") as f:
        known = set(re.findall(r"`([A-Z]+-[A-Za-z0-9-]+)`", f.read()))
    used = set()
    with open(tex_path, encoding="utf-8") as f:
        for ln in f:
            for m in re.findall(r"% FACT:\s*([A-Za-z0-9 -]+)", ln):
                used.update(m.split())
    unknown = sorted(used - known)
    if unknown:
        ok = False
        out.append(f"- FAIL: ID отсутствуют в facts.md: {unknown}. Либо опечатка, либо "
                   "в резюме попал факт, которого нет в банке.")
    orphans = [first for first, has_fact in tex_blocks(tex_path) if not has_fact]
    if orphans:
        ok = False
        out.append("- FAIL: блоки без % FACT::")
        for o in orphans:
            out.append(f"    - `{o[:90]}`")
    if ok:
        out.append(f"- OK: все блоки размечены; {len(used)} ID, все существуют в facts.md.")
    out.append("")
    return ok


# ------------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", required=True, help="собранный PDF резюме")
    ap.add_argument("--tex", help="исходник резюме (включает проверку provenance)")
    ap.add_argument("--keywords", help="keywords.tsv: 'must<TAB>термин' / 'nice<TAB>термин'")
    ap.add_argument("--market", help="ключ рынка из profile.json -> contacts")
    ap.add_argument("--root", help="корень репозитория (по умолчанию ищется по profile.json)")
    ap.add_argument("--profile", help="путь к profile.json")
    ap.add_argument("--facts", help="путь к facts.md")
    ap.add_argument("--blacklist", help="путь к gap-blacklist.txt")
    ap.add_argument("--anonymous", action="store_true",
                    help="резюме намеренно без имени — не проверять имя")
    ap.add_argument("--max-pages", type=int, help="бюджет страниц (по умолчанию из profile.json)")
    a = ap.parse_args()

    if not os.path.isfile(a.pdf):
        sys.exit(f"ERROR: нет файла: {a.pdf}")

    root = a.root or find_root(a.pdf) or find_root(os.getcwd())
    profile_path = a.profile or (os.path.join(root, "profile.json") if root else None)
    if not profile_path or not os.path.isfile(profile_path):
        sys.exit("ERROR: не найден profile.json. Указать --root или --profile.")
    with open(profile_path, encoding="utf-8") as f:
        profile = json.load(f)

    facts_path = a.facts or (os.path.join(root, "facts.md") if root else None)
    blacklist_path = a.blacklist or (os.path.join(root, "gap-blacklist.txt") if root else None)
    max_pages = a.max_pages or profile.get("defaults", {}).get("page_budget", 2)

    if a.market and a.market not in markets(profile):
        sys.exit(f"ERROR: рынок {a.market!r} не описан в profile.json. "
                 f"Доступны: {', '.join(markets(profile))}")
    if a.tex and not (facts_path and os.path.isfile(facts_path)):
        sys.exit("ERROR: для проверки provenance нужен facts.md (--facts).")

    raw = extract_pdf_text(a.pdf)
    norm = normalize(raw)
    out, passed = [f"# ATS-отчёт: {os.path.basename(a.pdf)}\n"], True

    passed &= check_profile(profile, a.market, a.anonymous, out)
    if a.keywords:
        check_coverage(norm, load_keywords(a.keywords), out)
    passed &= check_anomalies(raw, max_pages, out)
    if not a.anonymous:
        passed &= check_name(raw, profile, out)
    if a.market:
        passed &= check_contacts(raw, profile, a.market, out)
    if blacklist_path:
        passed &= check_blacklist(norm, blacklist_path, out)
    if a.tex:
        passed &= check_provenance(a.tex, facts_path, out)

    out.append(f"RESULT: {'PASS' if passed else 'FAIL'}")
    print("\n".join(out))
    sys.exit(0 if passed else 1)


def load_keywords(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            parts = ln.split("\t", 1)
            if len(parts) != 2 or parts[0] not in ("must", "nice"):
                sys.exit("keywords.tsv: ожидается 'must<TAB>слово' или 'nice<TAB>слово', "
                         f"получено: {ln!r}")
            rows.append((parts[0], parts[1].strip()))
    return rows


if __name__ == "__main__":
    main()
