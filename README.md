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

Para testar localmente: `python3 -m http.server` e abra http://localhost:8000.

## Publicar

O que vai para o servidor: os arquivos `*.html`, `*.htm`, `.htaccess`, e as pastas `portfolio/` e `assets/`.
**Não** envie `tools/`, `README.md` nem `.git/`.

### Opção A: hospedagem atual (Apache, cPanel/FTP)

1. Faça backup completo do WordPress (arquivos + banco) e guarde **fora** do servidor. Ele contém
   malware: não reinstale, use só para recuperar algum arquivo se precisar.
2. Apague **tudo** em `public_html` (inclusive arquivos ocultos como `.htaccess`) e o banco MySQL do WordPress.
3. Troque as senhas da hospedagem, do FTP e do painel.
4. Envie os arquivos listados acima para `public_html`. Ative "mostrar arquivos ocultos" no
   gerenciador de arquivos/FTP para o `.htaccess` ir junto.

O `.htaccess` (testado no Apache 2.4):
* força HTTPS;
* redireciona com 301 os endereços antigos (`/about`, `/about/`, `/portfolio/brand-minute`,
  `/marcas-possuem-historia.htm`...) para as páginas `.html`, em um único salto;
* responde **410 (removido)** para `wp-admin`, `wp-login.php`, `xmlrpc.php`, `wp-content`, `wp-json`, feeds,
  páginas de autor/categoria/tag e os posts de spam, para o Google tirá-los da busca;
* usa `404.html` como página de erro; esconde `tools/` e `README.md` se forem enviados por engano;
* liga compressão gzip e cache de imagens/fontes.

### Opção B: GitHub Pages

1. *Settings → Pages → Branch: `main` / `(root)`*. O site fica em `https://alexcwb01.github.io/qualitadesign/`.
2. Para usar o domínio: adicione o arquivo `CNAME` com `www.qualitadesign.com.br` (só nesse momento,
   pois ele faz o endereço github.io redirecionar para o domínio) e configure no DNS (Registro.br):
   * `www` → CNAME → `alexcwb01.github.io`
   * `qualitadesign.com.br` (raiz) → registros A: `185.199.108.153`, `185.199.109.153`,
     `185.199.110.153`, `185.199.111.153`
   * Depois marque *Enforce HTTPS* em *Settings → Pages*.
3. O GitHub Pages ignora o `.htaccess`: lá, `/about` e `/portfolio/...` já abrem as páginas `.html`
   automaticamente, e os arquivos `marcas-possuem-historia.htm` e `biblioteca.htm` redirecionam os
   endereços antigos dos posts. Os endereços de spam recebem 404.

## Regenerar (histórico)

> **Atenção:** desde 27/11/2026 o WordPress original serve malware em todas as páginas, então o script
> não tem mais de onde baixar e não deve ser rodado. **Este repositório é a fonte oficial do site:**
> as alterações são feitas direto nos arquivos daqui.

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
* Em 27/11/2026 o WordPress passou a injetar um **script malicioso de redirecionamento**
  (página "Redirecting...") nas respostas AJAX. O build remove esse conteúdo e **para com erro**
  se encontrar qualquer assinatura dele no site gerado (`MALWARE_SIG`).
* Removidos por dependerem do PHP do WordPress: busca, botões "curtir" (simple-likes) e comentários.
* Links que já estavam quebrados no site original foram corrigidos: o slide "Marcas possuem história"
  aponta para o post correspondente, a seta do banner aponta para `#projects`, e o menu das páginas
  internas aponta para as seções da home. O logo "Batista & Tawil" (link 404) ficou sem link.
* Ao clicar num projeto do portfólio, o painel abre com as mesmas imagens e textos do original
  (agora também em projects.html, onde no original o clique não abria nada). O projeto
  "Vicentini&Martins" já está vazio no site original (só título e categorias).
* A seção com o vídeo de fundo "Time Square" (YouTube) foi removida a pedido (`REMOVE_ELEMENTS`).
