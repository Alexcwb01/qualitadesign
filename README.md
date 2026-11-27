# Qualitá Design — site estático

Versão estática (HTML + CSS + JS puros) de https://qualitadesign.com.br/, gerada a partir do WordPress original.

## Estrutura

```
index.html                     home (one-page: Portfólio, Conheça, Opinião, Artigos)
about.html, projects.html, reviews.html, news.html
marcas-possuem-historia.html, biblioteca.html          posts do blog
marca-inspiram-confianca.html, nossa-historia-em-logotipos.html
portfolio/*.html               páginas de cada projeto
assets/css  assets/js  assets/images  assets/fonts
assets/data                    HTML pré-renderizado do que o WordPress carregava por AJAX:
                               grade de logotipos e painéis dos projetos do portfólio (assets/data/portfolio)
tools/build_static.py          script que baixa e converte o site
tools/verify_site.py           verificação de links, assets e restos do WordPress
```

Basta publicar a pasta em qualquer hospedagem estática (GitHub Pages, Netlify, Apache, Nginx…).
Para testar localmente: `python3 -m http.server` e abra http://localhost:8000.

## Regenerar

```bash
pip install requests beautifulsoup4 html5lib
python3 tools/build_static.py --out .
python3 tools/verify_site.py .
```

## Observações

* **O WordPress original está comprometido.** Desde 2025 foram injetados ~80 posts de spam
  (cassinos, "lorem ipsum", "test-gnd-…"), páginas com nomes hexadecimais e usuários
  `serverHost`/`serverHostx`. O script só exporta conteúdo publicado antes de 2022 (`CUTOFF`)
  e remove cards, links e contagens ligados ao spam.
* Removidos por dependerem do PHP do WordPress: busca, botões "curtir" (simple-likes) e comentários.
* Links que já estavam quebrados no site original foram corrigidos: o slide "Marcas possuem história"
  aponta para o post correspondente, a seta do banner aponta para `#projects`, e o menu das páginas
  internas aponta para as seções da home. O logo "Batista & Tawil" (link 404) ficou sem link.
* Ao clicar num projeto do portfólio, o painel abre com as mesmas imagens e textos do original
  (agora também em projects.html, onde no original o clique não abria nada). O projeto
  "Vicentini&Martins" já está vazio no site original (só título e categorias).
* O vídeo de fundo da seção "Time Square" continua vindo do YouTube (conteúdo externo, não do WordPress).
