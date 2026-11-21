#!/usr/bin/env python3
"""Робота з робочим файлом задач Togetherly (togetherly-work.csv).

Без сторонніх бібліотек. Зберігає формат, який розуміє togetherly-work.html:
UTF-8 з BOM, кома, рядки CRLF, колонки
ID, Задача, Опис, Строк, Час, Пріоритет, Готово, Нагадування, Змінено в Togetherly, Синхр.

Приклади:
  tasks.py FILE list --period today|tomorrow|week|this-week|next-week|overdue|all|done [--date 2026-11-25]
  tasks.py FILE find "звіт"
  tasks.py FILE add "Назва" [--due 2026-11-25] [--time 10:00] [--prio high|medium|low] [--desc "..."] [--remind "2026-11-24 18:00"]
  tasks.py FILE update REF [--title ..] [--due ..|--no-due] [--time ..|--no-time] [--prio ..|--no-prio] [--desc ..]
  tasks.py FILE comment REF "текст"        # дописує в Опис з датою
  tasks.py FILE remind REF "2026-11-24 18:00"
  tasks.py FILE done REF  /  reopen REF
  tasks.py FILE delete REF
REF — ID задачі або унікальний фрагмент назви.
"""
import argparse, csv, io, json, os, re, sys, tempfile
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    NOW = datetime.now(ZoneInfo("Europe/Madrid"))
except Exception:  # без tzdata — локальний час машини
    NOW = datetime.now()
TODAY = NOW.date()

HEAD = ["ID", "Задача", "Опис", "Строк", "Час", "Пріоритет", "Готово", "Нагадування", "Змінено в Togetherly", "Синхр"]
PRIO_IN = {"1": "Високий", "high": "Високий", "високий": "Високий", "h": "Високий",
           "2": "Середній", "medium": "Середній", "середній": "Середній", "m": "Середній",
           "3": "Низький", "low": "Низький", "низький": "Низький", "l": "Низький"}
PRIO_RANK = {"Високий": 0, "Середній": 1, "": 2, "Низький": 3}
WD = ["пн", "вт", "ср", "чт", "пт", "сб", "нд"]


def die(msg):
    print("ПОМИЛКА: " + msg, file=sys.stderr)
    sys.exit(1)


def norm_date(s):
    s = (s or "").strip()
    if not s:
        return ""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return s[:10]
    m = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$", s)
    if m:
        return f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}"
    die(f"Незрозуміла дата «{s}». Формат: РРРР-ММ-ДД")


def norm_time(s):
    s = (s or "").strip()
    if not s:
        return ""
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        die(f"Незрозумілий час «{s}». Формат: ГГ:ХХ")
    return f"{int(m[1]):02d}:{m[2]}"


def norm_rem(s):
    out = []
    for p in re.split(r"[;\n]", s or ""):
        p = p.strip()
        if not p:
            continue
        m = re.match(r"^(\S+)[ T]+(\d{1,2}:\d{2})$", p)
        if not m:
            die(f"Незрозуміле нагадування «{p}». Формат: РРРР-ММ-ДД ГГ:ХХ")
        out.append(f"{norm_date(m[1])} {norm_time(m[2])}")
    return "; ".join(sorted(set(out)))


def is_done(v):
    return (v or "").strip().lower() in ("true", "так", "done", "1", "✓", "готово", "виконано", "x")


def load(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        text = f.read()
    if not text.strip():
        return []
    first = text.splitlines()[0]
    sep = ";" if first.count(";") > first.count(",") else ","
    rd = csv.reader(io.StringIO(text), delimiter=sep)
    head = [h.strip() for h in next(rd)]
    if "Задача" not in head:
        die("У файлі немає колонки «Задача» — це не файл задач Togetherly")
    rows = []
    for r in rd:
        if not any(x.strip() for x in r):
            continue
        d = {h: (r[i] if i < len(r) else "") for i, h in enumerate(head)}
        rows.append({h: d.get(h, "") for h in HEAD} | {k: v for k, v in d.items() if k not in HEAD})
    return rows


def save(path, rows):
    extra = []
    for r in rows:
        for k in r:
            if k not in HEAD and k not in extra:
                extra.append(k)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(HEAD + extra)
    for r in rows:
        w.writerow([r.get(h, "") for h in HEAD + extra])
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as f:
        f.write(buf.getvalue())
    os.replace(tmp, path)


def find(rows, ref):
    hit = [r for r in rows if r["ID"] and r["ID"] == ref]
    if not hit:
        low = ref.lower()
        hit = [r for r in rows if low in r["Задача"].lower()]
        exact = [r for r in hit if r["Задача"].lower() == low]
        hit = exact or hit
    if not hit:
        die(f"Задачу «{ref}» не знайдено")
    if len(hit) > 1:
        die("Знайдено кілька задач, уточни (ID або точніша назва):\n" + "\n".join(f"  {r['ID'] or '—'} | {r['Задача']}" for r in hit))
    return hit[0]


def fmt(r):
    parts = []
    if r["Строк"]:
        dd = date.fromisoformat(r["Строк"])
        parts.append(f"{WD[dd.weekday()]} {dd:%d.%m}" + (f" {r['Час']}" if r["Час"] else ""))
        if dd < TODAY and not is_done(r["Готово"]):
            parts.append("ПРОСТРОЧЕНО")
    if r["Пріоритет"]:
        parts.append(r["Пріоритет"].lower())
    if r["Нагадування"]:
        parts.append("🔔 " + r["Нагадування"])
    mark = "[x]" if is_done(r["Готово"]) else "[ ]"
    line = f"{mark} {r['Задача']}" + (f"  ({', '.join(parts)})" if parts else "")
    if r["Опис"]:
        line += "\n      " + r["Опис"].replace("\n", "\n      ")
    return line + f"\n      id: {r['ID'] or '— (отримає після відкриття в клієнті)'}"


def key(r):
    return (r["Строк"] or "9999", PRIO_RANK.get(r["Пріоритет"], 2), r["Час"] or "99")


def cmd_list(rows, a):
    base = date.fromisoformat(norm_date(a.date)) if a.date else TODAY
    mon = base - timedelta(days=base.weekday())
    p = a.period
    if p == "done":
        sel = [r for r in rows if is_done(r["Готово"])]
    else:
        act = [r for r in rows if not is_done(r["Готово"])]
        due = lambda r: date.fromisoformat(r["Строк"]) if r["Строк"] else None
        if p == "today":
            sel = [r for r in act if due(r) and due(r) <= base]  # разом із простроченими
        elif p == "tomorrow":
            sel = [r for r in act if due(r) == base + timedelta(days=1)]
        elif p == "week":
            sel = [r for r in act if due(r) and due(r) <= base + timedelta(days=6)]  # 7 днів від дати, з простроченими
        elif p == "this-week":
            sel = [r for r in act if due(r) and due(r) <= mon + timedelta(days=6)]  # до неділі, з простроченими
        elif p == "next-week":
            sel = [r for r in act if due(r) and mon + timedelta(days=7) <= due(r) <= mon + timedelta(days=13)]
        elif p == "overdue":
            sel = [r for r in act if due(r) and due(r) < TODAY]
        else:
            sel = act
    sel.sort(key=key)
    if a.json:
        print(json.dumps(sel, ensure_ascii=False, indent=1))
        return
    print(f"Сьогодні {WD[TODAY.weekday()]} {TODAY:%d.%m.%Y}. Задач: {len(sel)}")
    for r in sel:
        print(fmt(r))


def apply_fields(r, a):
    if getattr(a, "title", None):
        r["Задача"] = a.title.strip()
    if getattr(a, "no_due", False):
        r["Строк"] = r["Час"] = ""
    elif getattr(a, "due", None):
        r["Строк"] = norm_date(a.due)
    if getattr(a, "no_time", False):
        r["Час"] = ""
    elif getattr(a, "time", None):
        if not r["Строк"]:
            die("Час можна задати лише разом зі строком")
        r["Час"] = norm_time(a.time)
    if getattr(a, "no_prio", False):
        r["Пріоритет"] = ""
    elif getattr(a, "prio", None):
        r["Пріоритет"] = PRIO_IN.get(a.prio.lower()) or die("Пріоритет: high | medium | low")
    if getattr(a, "desc", None) is not None:
        r["Опис"] = a.desc.strip()
    if getattr(a, "remind", None):
        r["Нагадування"] = norm_rem("; ".join(x for x in [r["Нагадування"], a.remind] if x))


def main():
    ap = argparse.ArgumentParser(description="Робочі задачі Togetherly (CSV)")
    ap.add_argument("file")
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("list"); s.add_argument("--period", default="today",
        choices=["today", "tomorrow", "week", "this-week", "next-week", "overdue", "all", "done"]); s.add_argument("--date"); s.add_argument("--json", action="store_true")
    s = sp.add_parser("find"); s.add_argument("text")
    for name in ("add", "update"):
        s = sp.add_parser(name)
        s.add_argument("title" if name == "add" else "ref")
        if name == "update":
            s.add_argument("--title"); s.add_argument("--no-due", action="store_true"); s.add_argument("--no-time", action="store_true"); s.add_argument("--no-prio", action="store_true")
        s.add_argument("--due"); s.add_argument("--time"); s.add_argument("--prio"); s.add_argument("--desc"); s.add_argument("--remind")
    s = sp.add_parser("comment"); s.add_argument("ref"); s.add_argument("text")
    s = sp.add_parser("remind"); s.add_argument("ref"); s.add_argument("when")
    for name in ("done", "reopen", "delete"):
        sp.add_parser(name).add_argument("ref")
    a = ap.parse_args()

    rows = load(a.file)
    if a.cmd == "list":
        return cmd_list(rows, a)
    if a.cmd == "find":
        hit = [r for r in rows if a.text.lower() in (r["Задача"] + " " + r["Опис"]).lower()]
        for r in sorted(hit, key=key):
            print(fmt(r))
        print(f"Знайдено: {len(hit)}")
        return
    if a.cmd == "add":
        r = {h: "" for h in HEAD}
        r["Готово"] = "FALSE"
        apply_fields(r, a)
        if not r["Задача"]:
            die("Порожня назва")
        rows.append(r); save(a.file, rows)
        print("Додано:\n" + fmt(r)); return
    r = find(rows, a.ref)
    if a.cmd == "update":
        apply_fields(r, a); msg = "Оновлено"
    elif a.cmd == "comment":
        stamp = NOW.strftime("%Y-%m-%d %H:%M")
        r["Опис"] = (r["Опис"].rstrip() + "\n\n" if r["Опис"].strip() else "") + f"[{stamp}] {a.text.strip()}"; msg = "Коментар додано"
    elif a.cmd == "remind":
        r["Нагадування"] = norm_rem("; ".join(x for x in [r["Нагадування"], a.when] if x)); msg = "Нагадування додано"
    elif a.cmd == "done":
        r["Готово"] = "TRUE"; msg = "Виконано"
    elif a.cmd == "reopen":
        r["Готово"] = "FALSE"; msg = "Знову відкрито"
    elif a.cmd == "delete":
        rows.remove(r); save(a.file, rows)
        print(f"Видалено з файлу: {r['Задача']}")
        if r["Змінено в Togetherly"]:
            print("УВАГА: задача вже була в загальному Togetherly — там її треба видалити окремо.")
        return
    save(a.file, rows)
    print(f"{msg}:\n" + fmt(r))


if __name__ == "__main__":
    main()
