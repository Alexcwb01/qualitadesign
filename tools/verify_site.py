#!/usr/bin/env python3
"""
Verifica o site estático gerado:
  * todo href/src/srcset/data-bg/url() local aponta para um arquivo existente;
  * todo link para âncora (#id) existe na página de destino;
  * nenhum resto do WordPress (domínio antigo, wp-content, wp-json, xmlrpc, ?ver=, emoji...).

Uso: python3 tools/verify_site.py [raiz]   (sai com código 1 se houver problemas)
"""
import os
import re
import sys
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
FORBIDDEN = [r"qualitadesign\.com\.br(?!\b[^\"']*@)", r"wp-content", r"wp-includes", r"wp-json",
             r"xmlrpc", r"wlwmanifest", r"wp-emoji", r"wpemoji", r"admin-ajax", r"\?ver=",
             r"shortpixel", r"s\.w\.org", r"api\.w\.org"]
CSS_URL = re.compile(r"""url\(\s*(['"]?)([^'")]+)\1\s*\)""")
SKIP_DIRS = {".git", "tools", "node_modules"}
problems = []


def local_target(base_file, ref):
    ref = ref.strip()
    if os.sep + os.path.join("assets", "data") + os.sep in base_file:
        # fragmentos HTML carregados via AJAX: caminhos relativos à página (na raiz)
        base_file = os.path.join(ROOT, "_fragment.html")
    if not ref or ref.startswith(("http://", "https://", "//", "mailto:", "tel:", "data:", "javascript:")):
        return None, None
    sp = urlsplit(ref)
    path = unquote(sp.path)
    target = os.path.normpath(os.path.join(os.path.dirname(base_file), path)) if path else base_file
    return target, sp.fragment


ids_cache = {}


def ids_of(html_file):
    if html_file not in ids_cache:
        with open(html_file, encoding="utf-8") as f:
            s = BeautifulSoup(f.read(), "html.parser")
        ids_cache[html_file] = {t["id"] for t in s.find_all(id=True)} | {t["name"] for t in s.find_all("a", attrs={"name": True})}
    return ids_cache[html_file]


def check_ref(src_file, ref, what):
    target, frag = local_target(src_file, ref)
    if target is None:
        return
    if not os.path.isfile(target):
        problems.append(f"{os.path.relpath(src_file, ROOT)}: {what} quebrado -> {ref}")
    elif frag and target.endswith(".html") and what == "link" and frag not in ids_of(target):
        problems.append(f"{os.path.relpath(src_file, ROOT)}: âncora inexistente -> {ref}")


html_files, css_files = [], []
for d, dirs, files in os.walk(ROOT):
    dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
    for fn in files:
        p = os.path.join(d, fn)
        if fn.endswith(".html"):
            html_files.append(p)
        elif fn.endswith(".css"):
            css_files.append(p)

links_checked = 0
for f in html_files:
    with open(f, encoding="utf-8") as fh:
        text = fh.read()
    for pat in FORBIDDEN:
        for m in re.finditer(pat, text):
            ctx = text[max(0, m.start() - 40):m.end() + 40].replace("\n", " ")
            if "mailto:" in ctx or "@qualitadesign" in ctx:
                continue
            problems.append(f"{os.path.relpath(f, ROOT)}: resto do WordPress '{m.group(0)}' em ...{ctx}...")
    s = BeautifulSoup(text, "html.parser")
    for a in s.find_all("a", href=True):
        h = a["href"]
        if h.startswith("#") and len(h) > 1:
            if h[1:] not in ids_of(f):
                problems.append(f"{os.path.relpath(f, ROOT)}: âncora inexistente -> {h}")
        else:
            check_ref(f, h, "link")
        links_checked += 1
    for t in s.find_all(True):
        for attr, val in t.attrs.items():
            if not isinstance(val, str):
                continue
            if attr in ("src", "data-bg", "data-src", "poster") or (t.name == "link" and attr == "href"):
                check_ref(f, val, attr)
            elif "srcset" in attr:
                for m in re.finditer(r"\s*(\S+?)(?:,(?=\s|$)|(\s+[^,]*)(?:,|$)|$)", val):
                    if m.group(1).rstrip(","):
                        check_ref(f, m.group(1).rstrip(","), attr)
            elif attr == "style":
                for m in CSS_URL.finditer(val):
                    check_ref(f, m.group(2), "style url()")

for f in css_files:
    with open(f, encoding="utf-8") as fh:
        css = fh.read()
    for m in CSS_URL.finditer(css):
        ref = m.group(2)
        if ref.startswith(("data:", "#", "http", "//")) or not ref.strip():
            continue
        check_ref(f, ref, "url() do CSS")
    for pat in FORBIDDEN[:3]:
        if re.search(pat, css):
            problems.append(f"{os.path.relpath(f, ROOT)}: referência ao WordPress '{pat}'")

print(f"{len(html_files)} páginas, {len(css_files)} CSS, {links_checked} links verificados")
if problems:
    print(f"{len(problems)} problema(s):")
    for p in sorted(set(problems)):
        print("  -", p)
    sys.exit(1)
print("OK: nenhum link quebrado nem resto do WordPress.")
