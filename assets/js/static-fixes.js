/* Ajustes do site estático. */
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
