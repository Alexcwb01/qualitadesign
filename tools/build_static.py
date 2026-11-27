#!/usr/bin/env python3
"""
Converte o WordPress https://qualitadesign.com.br/ em um site estático puro.

O que ele faz:
  * descobre as páginas pelos sitemaps do WordPress e por rastreamento a partir da home;
  * baixa HTML, CSS, JS, fontes (inclusive Google Fonts) e imagens (originais,
    sem passar pelo CDN do ShortPixel);
  * reescreve todos os links/caminhos para caminhos relativos locais
    (assets/css, assets/js, assets/images, assets/fonts);
  * remove metadados, scripts e tags do WordPress (wp-json, xmlrpc/EditURI,
    wlwmanifest, shortlink, feeds, oEmbed, wp-emoji, comment-reply, ?ver=, ...);
  * remove o conteúdo de spam injetado no site (o WordPress original está
    comprometido: posts de cassino/"lorem ipsum"/"test-gnd" publicados a partir
    de 2025). Só entra no site estático o conteúdo publicado antes de CUTOFF.

Uso:
  pip install requests beautifulsoup4 html5lib
  python3 tools/build_static.py --out . [--cache /tmp/qd-cache]
"""
import argparse
import copy
import hashlib
import html
import json
import os
import posixpath
import re
import sys
import time
from collections import OrderedDict, deque
from urllib.parse import unquote, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, Comment

SITE = "https://qualitadesign.com.br"
HOSTS = {"qualitadesign.com.br", "www.qualitadesign.com.br"}
GFONT_HOSTS = {"fonts.googleapis.com", "fonts.gstatic.com"}

# Tudo publicado/alterado a partir desta data no sitemap é spam injetado.
CUTOFF = "2022-01-01"
SITEMAPS = ["wp-sitemap-posts-page-1.xml", "wp-sitemap-posts-post-1.xml",
            "wp-sitemap-posts-portfolio-1.xml"]

# Links que já estavam quebrados (404) no site original -> destino correto.
LINK_FIXES = {"/forest-highway-with-green-leaves": "/marcas-possuem-historia.htm"}
SPAM_HOSTS = {"lafamilia.miami"}
# Trechos do site original removidos a pedido (seletores CSS) e scripts que ficam sem uso.
REMOVE_ELEMENTS = ["section#parallax-video"]      # vídeo de fundo "Time Square" (YouTube)
REMOVE_SCRIPT_IDS = {"mbytplayer-js"}             # player do YouTube usado só por esse vídeo
# Âncoras que não existem no site original -> seção correta (a seta "mouse" do banner).
ANCHOR_FIXES = {"mission": "projects"}
# Caminhos internos do WordPress que não existem num site estático.
JUNK_PATH = re.compile(r"^/(wp-admin|wp-json|wp-login|xmlrpc\.php|feed|comments/feed|"
                       r"author|category|tag|portfolio-category|page)(/|$)|/feed/?$")

# Assinaturas do malware de redirecionamento injetado no WordPress (página "Redirecting..." com
# script que coleta dados do navegador e redireciona o visitante).
MALWARE_SIG = re.compile(r"Redirecting\.\.\.|adBlockingDetected|refererBrowser|timezoneBrowser|"
                         r"google-ad-bottom-outer|document\.write\(`<a id=")

# Ajustes de comportamento que o site original não tinha (gravado em assets/js/static-fixes.js).
STATIC_FIXES_JS = """/* Ajustes do site estático. */
(function ($) {
  "use strict";
  /* iOS/Safari: o link do card do portfólio fica dentro de um overlay que só aparece no :hover,
     então o primeiro toque só "passa o mouse" e não dispara o clique (o texto aparece, mas a barra
     vermelha e o painel do projeto não). Num toque sem arrastar, dispara o clique do tema na hora. */
  var start = null;
  $(document).on("touchstart", ".portfolio-item a", function (e) {
    var t = e.originalEvent.touches[0];
    start = { x: t.clientX, y: t.clientY };
  });
  $(document).on("touchend", ".portfolio-item a", function (e) {
    if (!start) { return; }
    var t = e.originalEvent.changedTouches[0];
    var moved = Math.abs(t.clientX - start.x) > 10 || Math.abs(t.clientY - start.y) > 10;
    start = null;
    if (moved) { return; }                 /* foi rolagem, não toque */
    e.preventDefault();                    /* cancela o hover/clique sintético do navegador */
    $(this).trigger("click");              /* abre o painel como no desktop */
  });
})(jQuery);
"""

# Ajustes de layout pedidos (gravado em assets/css/static-fixes.css, carregado após o CSS do tema).
STATIC_FIXES_CSS = """/* Ajustes do site estático. */

/* Depoimentos ("Opinião"): fotos primeiro, texto depois; fonte menor (era 25px). */
.testimonial-carousel .block-media { margin-bottom: 30px; }
.testimonial-carousel .carousel-text .single-box { margin-bottom: 0; }
.testimonial-carousel .carousel-text .single-box p { font-size: 21px; line-height: 1.55em; }
@media (max-width: 767px) {
  .testimonial-carousel .block-media { margin-bottom: 20px; }
  .testimonial-carousel .carousel-text .single-box p { font-size: 17px; line-height: 1.6em; }
}
"""

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
FONT_EXT = {".woff", ".woff2", ".ttf", ".eot", ".otf"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp", ".avif"}
MEDIA_EXT = {".mp4", ".webm", ".mp3", ".pdf"}
ASSET_EXT = FONT_EXT | IMG_EXT | MEDIA_EXT | {".css", ".js"}
SHORTPIXEL = re.compile(r"^https?://sp-ao\.shortpixel\.ai/client/[^/]+/(https?://.+)$")
CSS_URL = re.compile(r"""url\(\s*(['"]?)([^'")]+)\1\s*\)""")
CSS_IMPORT = re.compile(r"""@import\s+(?:url\()?\s*['"]([^'"]+)['"]\s*\)?""")
SITE_URL_IN_TEXT = re.compile(r"(?:https?:)?(?:\\/|/){2}(?:www\.)?qualitadesign\.com\.br[^\s\"'<>)]*")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- rede
class Fetcher:
    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA

    def get(self, url):
        """Retorna (status, url_final, bytes, content_type) com cache em disco."""
        key = hashlib.sha1(url.encode()).hexdigest()
        path = os.path.join(self.cache_dir, key)
        if os.path.exists(path + ".meta"):
            with open(path + ".meta") as f:
                status, final, ctype = f.read().split("\n")[:3]
            with open(path, "rb") as f:
                return int(status), final, f.read(), ctype
        r = None
        for attempt in range(6):
            try:
                r = self.s.get(url, timeout=60)
                if r.status_code < 500:
                    break
                log(f"  HTTP {r.status_code} em {url}, tentando de novo...")
            except requests.RequestException as e:
                log("  ERRO", url, e)
            time.sleep(2 ** attempt)
        if r is None or r.status_code >= 500:
            # o servidor original oscila com 503: não guarda o erro no cache
            return (r.status_code if r is not None else 0), url, b"", ""
        with open(path, "wb") as f:
            f.write(r.content)
        with open(path + ".meta", "w") as f:
            f.write(f"{r.status_code}\n{r.url}\n{r.headers.get('content-type', '')}")
        return r.status_code, r.url, r.content, r.headers.get("content-type", "")


# --------------------------------------------------------------------------- URLs
def norm(url, base=SITE + "/"):
    """Resolve, decodifica entidades e remove o proxy do ShortPixel."""
    url = html.unescape(url.strip())
    if url.startswith("//"):
        url = "https:" + url
    url = urljoin(base, url)
    m = SHORTPIXEL.match(url)
    return m.group(1) if m else url


def host(url):
    return (urlsplit(url).hostname or "").lower()


def is_internal(url):
    return host(url) in HOSTS


def ext_of(url):
    return posixpath.splitext(unquote(urlsplit(url).path))[1].lower()


def page_key(url):
    p = unquote(urlsplit(url).path).rstrip("/") or "/"
    return LINK_FIXES.get(p, p)


def page_file(key):
    if key == "/":
        return "index.html"
    k = key.lstrip("/")
    k = re.sub(r"\.html?$", "", k)
    return k + ".html"


def parse_srcset(val):
    """Divide um srcset em [(url, descritor)]. URLs podem conter vírgulas (ShortPixel)."""
    out = []
    for m in re.finditer(r"\s*(\S+?)(?:,(?=\s|$)|(\s+[^,]*)(?:,|$)|$)", val):
        url, desc = m.group(1).rstrip(","), (m.group(2) or "").strip()
        if url:
            out.append((url, desc))
    return out


def strip_injected(text, where):
    """Fragmentos AJAX nunca contêm um documento HTML completo: o que vier antes do
    último </html> foi injetado pelo malware."""
    if "</html>" in text.lower():
        idx = text.lower().rfind("</html>") + len("</html>")
        log(f"  ALERTA: conteúdo injetado (malware) removido de {where}")
        text = text[idx:]
    return text


def rel(from_file, to_file):
    return posixpath.relpath(to_file, posixpath.dirname(from_file) or ".")


# --------------------------------------------------------------------------- assets
class Assets:
    def __init__(self, out, fetcher):
        self.out, self.f = out, fetcher
        self.map = {}          # url sem query/fragmento -> caminho local (relativo à raiz)
        self.used = {}         # caminho local -> url
        self.missing = []
        self.by_hash = {}      # sha1 do conteúdo -> caminho local (evita arquivos duplicados)

    @staticmethod
    def folder(url, ctype=""):
        e = ext_of(url)
        path = urlsplit(url).path.lower()
        if e == ".css" or "text/css" in ctype:
            return "assets/css"
        if e == ".js" or "javascript" in ctype:
            return "assets/js"
        if e in FONT_EXT or "font" in ctype:
            return "assets/fonts"
        if e == ".svg" and ("font" in path):
            return "assets/fonts"
        if e in MEDIA_EXT:
            return "assets/media"
        return "assets/images"

    def _name(self, url, folder, hint):
        base = hint or posixpath.basename(unquote(urlsplit(url).path)) or "file"
        base = re.sub(r"[^A-Za-z0-9._-]+", "-", base)
        stem, e = posixpath.splitext(base)
        cand = f"{folder}/{stem}{e}"
        if cand in self.used and self.used[cand] != url:
            # mesmo nome de arquivo vindo de pastas diferentes (ex.: duas cópias do FontAwesome)
            parent = posixpath.basename(posixpath.dirname(urlsplit(url).path))
            cand = f"{folder}/{stem}-{hashlib.sha1(url.encode()).hexdigest()[:6]}{e}"
        return cand

    def local(self, url, hint=None, css_only=False):
        """Baixa o asset (se preciso) e devolve (caminho_local, fragmento) ou None."""
        sp = urlsplit(url)
        frag = sp.fragment
        key = sp._replace(fragment="").geturl()
        # Só Google Fonts precisa da query string; ?ver=, ?xs1351 etc. são descartados.
        if sp.hostname not in GFONT_HOSTS:
            key = sp._replace(query="", fragment="").geturl()
        if key in self.map:
            return self.map[key], frag
        status, final, data, ctype = self.f.get(key)
        if status != 200 or not data:
            self.missing.append((status, key))
            self.map[key] = None
            return None, frag
        folder = self.folder(key, ctype)
        if host(key) == "fonts.googleapis.com":
            folder = "assets/css"
            hint = hint or "google-fonts.css"
        if hint and not posixpath.splitext(hint)[1]:
            hint += posixpath.splitext(urlsplit(key).path)[1] or (".css" if folder.endswith("css") else "")
        digest = hashlib.sha1(data).hexdigest()
        if digest in self.by_hash and folder not in ("assets/css", "assets/js"):
            self.map[key] = self.by_hash[digest]
            return self.map[key], frag
        path = self._name(key, folder, hint)
        self.map[key] = path
        self.used[path] = key
        self.by_hash[digest] = path
        if folder == "assets/css":
            data = self.process_css(data.decode("utf-8", "replace"), final, path).encode("utf-8")
        elif folder == "assets/js":
            data = self.process_js(data.decode("utf-8", "replace"), path).encode("utf-8")
        dst = os.path.join(self.out, path)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(data)
        return path, frag

    def ref(self, url, from_file, hint=None):
        """Caminho relativo de from_file até o asset, ou None se não baixou."""
        path, frag = self.local(url, hint)
        if not path:
            return None
        return rel(from_file, path) + (f"#{frag}" if frag else "")

    def process_css(self, css, css_url, css_file):
        def repl_url(m):
            raw = m.group(2).strip()
            if raw.startswith(("data:", "#", "about:")):
                return m.group(0)
            u = norm(raw, css_url)
            if not (is_internal(u) or host(u) in GFONT_HOSTS):
                return m.group(0)
            r = self.ref(u, css_file)
            return f"url('{r}')" if r else "none"

        def repl_import(m):
            u = norm(m.group(1), css_url)
            r = self.ref(u, css_file)
            return f"@import url('{r}')" if r else ""

        css = CSS_IMPORT.sub(repl_import, css)
        css = CSS_URL.sub(repl_url, css)
        css = re.sub(r"/\*#\s*sourceMappingURL=[^*]*\*/", "", css)
        return css

    def process_js(self, js, js_file):
        js = re.sub(r"^//[#@]\s*source(Mapping)?URL=.*$", "", js, flags=re.M)
        if "sena-main" in js_file:
            # painel do portfólio: carrega o fragmento estático (data-ajax) em vez de "?ajax=1"
            js = js.replace('var url=$that.attr("href")+"?ajax=1";',
                            'var url=$that.attr("data-ajax")||$that.attr("href");')
        if "vc_grid" in js_file:
            # a grade do WPBakery passa a ler um HTML estático pré-renderizado (ver prerender_vc_grids)
            js = js.replace('type:"POST",dataType:"html"', 'type:"GET",dataType:"html"')
        return js


# --------------------------------------------------------------------------- site
class Site:
    def __init__(self, out, cache):
        self.out = out
        self.f = Fetcher(cache)
        self.assets = Assets(out, self.f)
        self.allowed = set()            # chaves de página legítimas (sitemap < CUTOFF)
        self.pages = OrderedDict()      # chave -> (url, html)
        self.legit_posts = []           # [{url,title,date,category,excerpt,article_html}]
        self.report = {"blocked_links": set(), "removed_cards": 0}

    # ---------------------------------------------------------------- descoberta
    def load_sitemaps(self):
        self.allowed.add("/")
        for sm in SITEMAPS:
            _, _, data, _ = self.f.get(f"{SITE}/{sm}")
            for loc, mod in re.findall(r"<loc>([^<]+)</loc>(?:<lastmod>([^<]+)</lastmod>)?",
                                       data.decode("utf-8", "replace")):
                if mod and mod[:10] < CUTOFF:
                    self.allowed.add(page_key(loc))
        log(f"{len(self.allowed)} páginas legítimas no sitemap (anteriores a {CUTOFF})")

    def crawl(self):
        q = deque(["/"])
        seen = set(q)
        while q:
            key = q.popleft()
            status, final, data, ctype = self.f.get(SITE + (key if key != "/" else "/"))
            if status != 200 or "html" not in ctype:
                log(f"  ignorada {key}: HTTP {status}")
                continue
            text = data.decode("utf-8", "replace")
            self.pages[key] = (final, text)
            log(f"  página {key} -> {page_file(key)}")
            for href in re.findall(r'href=["\']([^"\']+)["\']', text):
                u = norm(href, final)
                if not is_internal(u) or ext_of(u) in ASSET_EXT:
                    continue
                k = page_key(u)
                if k in self.allowed and k not in seen:
                    seen.add(k)
                    q.append(k)

    def collect_legit_posts(self):
        """Percorre /news/page/N e guarda os posts legítimos (o spam ocupa as primeiras páginas)."""
        n, seen = 1, set()
        while True:
            url = f"{SITE}/news" + (f"/page/{n}" if n > 1 else "")
            status, final, data, _ = self.f.get(url)
            if status != 200:
                break
            soup = BeautifulSoup(data, "html5lib")
            for art in soup.select("article.blog-post"):
                a = art.select_one("h3 a")
                if not a:
                    continue
                k = page_key(norm(a["href"]))
                if k in self.allowed and k not in seen:
                    seen.add(k)
                    cat = art.select_one(".blog-category a")
                    img = art.select_one("img.wp-post-image") or art.find("img")
                    self.legit_posts.append({
                        "key": k, "url": SITE + k, "title": a.get_text(strip=True),
                        "date": (art.select_one(".blog-date") or soup.new_tag("x")).get_text(strip=True),
                        "category": cat.get_text(strip=True) if cat else "",
                        "excerpt": (art.select_one(".post-content p") or soup.new_tag("x")).get_text(" ", strip=True),
                        "article": str(art),
                        "image": img.get("src") if img else None,
                        "image_srcset": img.get("srcset") if img else None,
                    })
            if not soup.select_one(".post-nav .btn-prev"):
                break
            n += 1
        log(f"{len(self.legit_posts)} posts legítimos: {[p['key'] for p in self.legit_posts]}")
        if self.legit_posts:
            # garante que os posts legítimos sejam exportados mesmo se só aparecem em páginas paginadas
            for p in self.legit_posts:
                if p["key"] not in self.pages:
                    status, final, data, _ = self.f.get(p["url"])
                    if status == 200:
                        self.pages[p["key"]] = (final, data.decode("utf-8", "replace"))

    # ---------------------------------------------------------------- limpeza
    def is_spam_link(self, href):
        u = norm(href)
        if host(u) in SPAM_HOSTS:
            return True
        if is_internal(u) and ext_of(u) not in ASSET_EXT:
            k = page_key(u)
            return k not in self.pages and not JUNK_PATH.search(k)
        return False

    def remove_spam_cards(self, soup):
        """Remove cards/listagens de posts que apontam para páginas de spam."""
        for sel, title_sel in (("article.blog-post", "h3 a"), (".recent-post", "a.post-title"),
                               (".nav-next", "a"), (".nav-previous", "a"),
                               (".blog-home > div", ".blog-text h4 a")):
            for card in soup.select(sel):
                a = card.select_one(title_sel)
                if a and a.get("href") and self.is_spam_link(a["href"]):
                    card.decompose()
                    self.report["removed_cards"] += 1

    def rebuild_lists(self, soup, key, templates):
        posts = self.legit_posts
        # home: seção "Artigos" com 3 cards
        row = soup.select_one(".blog-home")
        if row is not None and templates.get("home") is not None:
            for p in posts[:3]:
                if row.find("a", href=lambda h: h and page_key(norm(h)) == p["key"]):
                    continue
                c = copy.copy(templates["home"])
                cat = c.select_one(".blog-category")
                if cat:
                    cat.string = p["category"]
                spans = c.select(".blog-about > span")
                if len(spans) >= 2:
                    spans[0].decompose()   # autor (vazio): evita o "•" solto antes da data
                    spans[1].string = p["date"]
                # imagem de destaque, como o tema renderiza: a tag da categoria fica sobre a foto
                holder = cat.parent if cat is not None else None
                if p.get("image") and holder is not None and not holder.select_one(".blog-img-link"):
                    link = soup.new_tag("a", href=p["url"], attrs={"class": "blog-img-link"})
                    im = soup.new_tag("img", src=p["image"], alt=p["title"], attrs={"class": "blog-img"})
                    if p.get("image_srcset"):
                        im["srcset"] = p["image_srcset"]
                        im["sizes"] = "(max-width: 767px) 100vw, 360px"
                    link.append(im)
                    holder.insert(0, link)
                a = c.select_one(".blog-text h4 a")
                a["href"], a.string = p["url"], p["title"]
                wrapper = c.select_one(".blog-wrapper")
                for child in list(wrapper.find_all(recursive=False)):
                    if not ({"blog-about", "blog-text"} & set(child.get("class") or [])):
                        child.decompose()
                for t in list(wrapper.find_all(string=True, recursive=False)):
                    t.extract()
                ex = soup.new_tag("p")
                ex.string = p["excerpt"]
                c.select_one(".blog-wrapper").append(ex)
                row.append(c)
        # sidebar: "Postagens Recentes"
        box = soup.select_one(".widget_recent_entries2 .col-md-12")
        if box is not None and templates.get("recent") is not None:
            for p in posts:
                if box.find("a", href=lambda h: h and page_key(norm(h)) == p["key"]):
                    continue
                c = copy.copy(templates["recent"])
                a = c.select_one("a.post-title")
                a["href"], a.string = p["url"], p["title"]
                d = c.select_one(".recent-post-info p")
                if d:
                    d.string = p["date"]
                box.append(c)
        # widget "Categorias": contagens refletem só os posts legítimos
        counts = {}
        for p in posts:
            counts[p["category"].lower()] = counts.get(p["category"].lower(), 0) + 1
        for li in soup.select(".widget_categories li.cat-item"):
            a = li.find("a")
            name = (a.get_text(strip=True) if a else re.sub(r"\s*\(\d+\)\s*$", "", li.get_text(strip=True)))
            n = counts.get(name.lower(), 0)
            if not n:
                li.decompose()
                continue
            for t in li.find_all(string=re.compile(r"\(\d+\)")):
                t.replace_with(re.sub(r"\(\d+\)", f"({n})", t))
            for sp in li.find_all("span"):
                if sp.get_text(strip=True).isdigit():
                    sp.string = str(n)
        # /news: lista completa dos posts legítimos, sem paginação
        if key == "/news":
            col = soup.select_one(".col-md-8.res-margin")
            nav = soup.select_one(".post-nav")
            if nav:
                nav.find_parent("div", class_="row").decompose()
            if col is not None:
                for p in posts:
                    if not col.find("a", href=lambda h: h and page_key(norm(h)) == p["key"]):
                        col.append(BeautifulSoup(p["article"], "html5lib").select_one("article"))

    def clean_wordpress(self, soup):
        head = soup.head
        junk_rel = {"edituri", "wlwmanifest", "shortlink", "pingback", "profile",
                    "dns-prefetch", "preconnect", "canonical", "https://api.w.org/", "prev", "next"}
        for link in soup.find_all("link"):
            rels = {r.lower() for r in (link.get("rel") or [])}
            if rels & junk_rel or ("alternate" in rels):
                link.decompose()
        for meta in soup.find_all("meta"):
            if (meta.get("name") or "").lower() in {"generator", "framework"}:
                meta.decompose()
        for s in soup.find_all("script"):
            sid, src, txt = s.get("id", ""), s.get("src", ""), s.string or ""
            if (sid in {"wp-emoji-settings", "comment-reply-js", "sena-main-js-extra"} | REMOVE_SCRIPT_IDS
                    or "simple-likes" in sid
                    or s.get("type") == "speculationrules" or "sbiajaxurl" in txt
                    or MALWARE_SIG.search(txt)
                    or "wp-emoji" in src or "comment-reply" in src
                    or "wpEmojiSettings" in txt or "wp-emoji" in txt):
                s.decompose()
                continue
            if txt:
                txt = re.sub(r"^//[#@]\s*source(Mapping)?URL=.*$", "", txt, flags=re.M)
                txt = re.sub(r',?\s*"security"\s*:\s*"[^"]*"', "", txt)
                txt = re.sub(r'"nonces"\s*:\s*\{[^}]*\}', '"nonces":{}', txt)
                txt = re.sub(r'"ajax_url"\s*:\s*"[^"]*"', '"ajax_url":""', txt)
                s.string = txt.strip() + "\n"
        for st in soup.find_all("style"):
            if st.get("id") in {"wp-emoji-styles", "wp-emoji-styles-inline-css"} or "wp-smiley" in (st.string or ""):
                st.decompose()
        # recursos que dependem do PHP/AJAX do WordPress
        for sel in REMOVE_ELEMENTS + [".search-wrapper", ".widget_search", "li.post-like", ".sl-wrapper",
                    "#respond", ".comment-respond", "#comments"]:
            for el in soup.select(sel):
                el.decompose()
        for a in soup.select("a.navbar-search"):
            (a.parent if a.parent.name == "li" else a).decompose()
        for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
            c.extract()
        if head is not None:
            for t in head.find_all(string=True):
                pass

    def prerender_vc_grids(self, soup, key, out_file):
        """Grades do WPBakery (vc_grid) carregam os itens via admin-ajax.php.
        Busca essa resposta uma vez, salva como HTML estático e aponta a grade para ele."""
        for g in soup.select("[data-vc-grid-settings]"):
            st = json.loads(g["data-vc-grid-settings"])
            data = [("action", "vc_get_vc_grid_data"), ("vc_action", "vc_get_vc_grid_data"),
                    ("tag", st.get("tag", "")), ("vc_post_id", g.get("data-vc-post-id", "")),
                    ("_vcnonce", g.get("data-vc-public-nonce", ""))]

            def flat(prefix, v):
                if isinstance(v, dict):
                    for k, x in v.items():
                        flat(f"{prefix}[{k}]", x)
                else:
                    data.append((prefix, "" if v is None else str(v)))
            flat("data", st)
            r = self.f.s.post(f"{SITE}/wp-admin/admin-ajax.php", data=data, timeout=60,
                              headers={"X-Requested-With": "XMLHttpRequest"})
            if r.status_code != 200 or "vc_grid" not in r.text:
                log(f"  aviso: não consegui pré-renderizar a grade em {key} (HTTP {r.status_code})")
                continue
            frag = BeautifulSoup(strip_injected(r.text, f"grade de {key}"), "html.parser")
            self.clean_wordpress(frag)
            self.remove_spam_cards(frag)
            self.rewrite(frag, key, out_file)   # caminhos relativos à página que recebe o HTML
            name = re.sub(r"[^A-Za-z0-9-]+", "-", st.get("shortcode_id", "grid"))
            path = f"assets/data/vc-grid-{name}.html"
            os.makedirs(os.path.join(self.out, "assets/data"), exist_ok=True)
            with open(os.path.join(self.out, path), "w", encoding="utf-8") as fh:
                fh.write(str(frag))
            g["data-vc-request"] = rel(out_file, path)
            del g["data-vc-public-nonce"]
            log(f"  grade pré-renderizada: {path} ({len(frag.select('.vc_grid-item'))} itens)")

    def prerender_portfolio(self):
        """O tema abre os projetos num painel (#portfolio-details) carregando "<link>?ajax=1",
        que no WordPress devolve só o bloco do projeto. Gera esses blocos como HTML estático."""
        self.portfolio_frags = {}
        for key in list(self.pages):
            if not key.startswith("/portfolio/"):
                continue
            status, final, data, _ = self.f.get(SITE + key + "?ajax=1")
            if status != 200 or b"single-portfolio" not in data:
                log(f"  aviso: fragmento do portfólio {key} indisponível (HTTP {status})")
                continue
            frag = BeautifulSoup(strip_injected(data.decode("utf-8", "replace"), f"painel {key}"), "html.parser")
            self.clean_wordpress(frag)
            self.remove_spam_cards(frag)
            # caminhos relativos à raiz: o painel existe em index.html e projects.html
            self.rewrite(frag, key, "index.html")
            path = f"assets/data/portfolio/{key.rsplit('/', 1)[1]}.html"
            os.makedirs(os.path.join(self.out, "assets/data/portfolio"), exist_ok=True)
            with open(os.path.join(self.out, path), "w", encoding="utf-8") as fh:
                fh.write(re.sub(r"\n\s*\n+", "\n", str(frag)).strip() + "\n")
            self.portfolio_frags[page_file(key)] = path
            n_img = len(frag.find_all("img"))
            log(f"  painel do portfólio: {path} ({n_img} imagens)" + ("  <- sem conteúdo no site original" if not n_img else ""))

    def link_portfolio_frags(self, soup, out_file):
        item = soup.select_one(".portfolio-item")
        if item is not None and soup.body is not None:
            path = "assets/js/static-fixes.js"
            dst = os.path.join(self.out, path)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8") as fh:
                fh.write(STATIC_FIXES_JS)
            soup.body.append(soup.new_tag("script", src=rel(out_file, path), defer=""))
        if item is not None and soup.find(id="portfolio-details") is None:
            # o /projects original não tem o painel, então o clique não abria nada: cria como na home
            sec = item.find_parent("section")
            if sec is not None:
                sec.insert_after(soup.new_tag("section", id="portfolio-details"))
        for a in soup.select(".portfolio-item a[href]"):
            target = posixpath.normpath(posixpath.join(posixpath.dirname(out_file), a["href"].split("#")[0]))
            frag = self.portfolio_frags.get(target)
            if frag:
                if posixpath.dirname(out_file):
                    log(f"  aviso: painel do portfólio em subpasta ({out_file}); caminhos podem quebrar")
                a["data-ajax"] = rel(out_file, frag)

    def customize(self, soup, out_file):
        """Mudanças de layout pedidas para o site estático (não existem no original)."""
        # Depoimentos: o carrossel de fotos (.block-media) passa a vir antes das citações (.block-text).
        for car in soup.select(".testimonial-carousel"):
            text, media = car.select_one(":scope > .block-text"), car.select_one(":scope > .block-media")
            if text is not None and media is not None:
                text.insert_before(media.extract())
        # CSS de ajustes, depois do CSS do tema
        path = "assets/css/static-fixes.css"
        dst = os.path.join(self.out, path)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(STATIC_FIXES_CSS)
        css_links = soup.head.find_all("link", rel="stylesheet") if soup.head else []
        tag = soup.new_tag("link", rel="stylesheet", href=rel(out_file, path), media="all")
        if css_links:
            css_links[-1].insert_after(tag)
        elif soup.head is not None:
            soup.head.append(tag)

    # ---------------------------------------------------------------- reescrita
    def rewrite(self, soup, key, out_file):
        a_ = self.assets

        def page_href(u):
            k = page_key(u)
            frag = urlsplit(u).fragment
            if k in self.pages:
                target = page_file(k)
                r = rel(out_file, target) if target != out_file else ""
                return (r or (target if not frag else "")) + (f"#{frag}" if frag else "")
            return None

        own_ids = {t["id"] for t in soup.find_all(id=True)}
        for a in list(soup.find_all("a", href=True)):
            if a.parent is None:
                continue
            href = a["href"].strip()
            if href.startswith("#") and len(href) > 1:
                frag = ANCHOR_FIXES.get(href[1:], href[1:])
                if frag in own_ids:
                    a["href"] = "#" + frag
                elif frag in self.home_ids and key != "/":
                    # menu "one page" usado em páginas internas: aponta para a seção da home
                    a["href"] = rel(out_file, "index.html") + "#" + frag
                continue
            if href.startswith(("#", "mailto:", "tel:", "javascript:")) or not href:
                continue
            u = norm(href, SITE + key)
            if host(u) in SPAM_HOSTS:
                self.report["blocked_links"].add(u)
                a.unwrap()
                continue
            if not is_internal(u):
                continue
            if ext_of(u) in ASSET_EXT:
                r = a_.ref(u, out_file)
                if r:
                    a["href"] = r
                continue
            r = page_href(u)
            if r is not None:
                a["href"] = r
            else:
                # destino inexistente no site estático (spam, arquivo de autor/tag, 404):
                # mantém o texto, remove o link
                self.report["blocked_links"].add(page_key(u))
                a.unwrap()

        for form in soup.find_all("form"):
            log(f"  aviso: formulário restante em {key}: action={form.get('action')}")

        for link in soup.find_all("link", href=True):
            u = norm(link["href"], SITE + key)
            if is_internal(u) or host(u) in GFONT_HOSTS:
                hint = None
                lid = link.get("id", "")
                if "stylesheet" in (link.get("rel") or []):
                    hint = re.sub(r"-css$", "", lid) or ("style" if "autoptimize" in u else None)
                r = a_.ref(u, out_file, hint)
                if r:
                    link["href"] = r
                else:
                    link.decompose()

        for s in soup.find_all("script", src=True):
            u = norm(s["src"], SITE + key)
            if is_internal(u):
                hint = None
                if "autoptimize" in u and s.get("id"):
                    hint = re.sub(r"-js$", "", s["id"])
                r = a_.ref(u, out_file, hint)
                if r:
                    s["src"] = r
                else:
                    s.decompose()

        url_attr = re.compile(r"^(?:https?:)?//")
        for tag in soup.find_all(True):
            for attr, val in list(tag.attrs.items()):
                if not isinstance(val, str) or (tag.name in {"a", "link", "script"} and attr in {"href", "src"}):
                    continue
                if attr == "style":
                    tag[attr] = self.rewrite_css_text(val, key, out_file)
                elif "srcset" in attr:
                    parts = []
                    for src, desc in parse_srcset(val):
                        r = a_.ref(norm(src, SITE + key), out_file)
                        if r:
                            parts.append(f"{r} {desc}".strip())
                    if parts:
                        tag[attr] = ", ".join(parts)
                    else:
                        del tag[attr]
                elif url_attr.match(val.strip()) or val.startswith("/wp-content"):
                    u = norm(val, SITE + key)
                    if is_internal(u) or host(u) in GFONT_HOSTS:
                        if ext_of(u) in ASSET_EXT:
                            r = a_.ref(u, out_file)
                            if r:
                                tag[attr] = r
                        else:
                            r = page_href(u)
                            tag[attr] = r if r is not None else "index.html"

        for st in soup.find_all("style"):
            if st.string:
                st.string = self.rewrite_css_text(st.string, key, out_file)

        for s in soup.find_all("script", src=False):
            if s.string and SITE_URL_IN_TEXT.search(s.string):
                s.string = self.rewrite_script_text(s.string, out_file)

    def rewrite_css_text(self, css, key, out_file):
        def repl(m):
            raw = m.group(2).strip()
            if raw.startswith(("data:", "#")):
                return m.group(0)
            u = norm(raw, SITE + key)
            if not is_internal(u):
                return m.group(0)
            r = self.assets.ref(u, out_file)
            return f"url('{r}')" if r else "none"
        return CSS_URL.sub(repl, css)

    def rewrite_script_text(self, txt, out_file):
        def repl(m):
            raw = m.group(0)
            u = norm(raw.replace("\\/", "/"))
            if ext_of(u) in ASSET_EXT:
                r = self.assets.ref(u, out_file)
            else:
                k = page_key(u)
                r = rel(out_file, page_file(k)) if k in self.pages else "."
                if k == "/":
                    r = posixpath.dirname(rel(out_file, "index.html")) or "."
                # theme_default_path: aponta para a pasta de assets
                if "/wp-content/themes/" in u:
                    r = posixpath.dirname(rel(out_file, "assets/index")) or "."
            r = r or ""
            return r.replace("/", "\\/") if "\\/" in raw else r
        return SITE_URL_IN_TEXT.sub(repl, txt)

    # ---------------------------------------------------------------- build
    def build(self):
        self.load_sitemaps()
        self.crawl()
        self.collect_legit_posts()
        self.prerender_portfolio()
        templates = {}
        self.home_ids = {t["id"] for t in BeautifulSoup(self.pages["/"][1], "html5lib").find_all(id=True)}
        for key, (url, text) in self.pages.items():
            soup = BeautifulSoup(text, "html5lib")
            if "home" not in templates:
                c = soup.select_one(".blog-home > div")
                if c is not None:
                    templates["home"] = copy.copy(c)
            if "recent" not in templates:
                c = soup.select_one(".widget_recent_entries2 .recent-post")
                if c is not None:
                    templates["recent"] = copy.copy(c)
        for key, (url, text) in self.pages.items():
            out_file = page_file(key)
            log(f"processando {key} -> {out_file}")
            soup = BeautifulSoup(text, "html5lib")
            self.remove_spam_cards(soup)
            self.rebuild_lists(soup, key, templates)
            self.clean_wordpress(soup)
            self.prerender_vc_grids(soup, key, out_file)
            self.rewrite(soup, key, out_file)
            self.link_portfolio_frags(soup, out_file)
            self.customize(soup, out_file)
            dst = os.path.join(self.out, out_file)
            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
            out = str(soup)
            out = re.sub(r"\n\s*\n+", "\n", out)
            with open(dst, "w", encoding="utf-8") as fh:
                fh.write(out)
        infected = []
        for d, _, files in os.walk(self.out):
            if "/.git" in d or d.endswith("/tools"):
                continue
            for fn in files:
                if fn.endswith((".html", ".js", ".css")):
                    with open(os.path.join(d, fn), encoding="utf-8", errors="replace") as fh:
                        if MALWARE_SIG.search(fh.read()):
                            infected.append(os.path.relpath(os.path.join(d, fn), self.out))
        if infected:
            log(f"\nERRO: assinatura de malware encontrada em {infected}. Não publique este build.")
            sys.exit(1)
        log("\n=== resumo ===")
        log(f"páginas: {len(self.pages)}; assets: {sum(1 for v in self.assets.map.values() if v)}")
        log(f"cards de spam removidos: {self.report['removed_cards']}")
        log(f"links removidos (destino inexistente/spam): {sorted(self.report['blocked_links'])}")
        for st, u in self.assets.missing:
            log(f"  asset indisponível (HTTP {st}): {u}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=".", help="pasta de saída (raiz do site)")
    ap.add_argument("--cache", default=os.path.join(os.path.expanduser("~"), ".cache", "qd-static"),
                    help="cache de downloads (permite rodar de novo sem rebaixar)")
    args = ap.parse_args()
    Site(os.path.abspath(args.out), args.cache).build()


if __name__ == "__main__":
    main()
