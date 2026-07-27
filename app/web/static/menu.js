/* Cortina de escalas da consulta aberta (regra 13.1).
 *
 * Único JavaScript do sistema, e é nosso: sem biblioteca, sem CDN — a rede da
 * OM pode não ter internet. Se este arquivo não carregar, o <noscript> do
 * template devolve a lista de escalas ao topo da página; a consulta é aberta e
 * não pode depender de script para mostrar as outras escalas.
 *
 * Escolher uma escala RECARREGA a página (é um link, não uma aba de JS), então
 * a cortina precisa lembrar que estava aberta — senão ela se fecha sozinha a
 * cada clique, que é o contrário do que se pede de um menu.
 */
(function () {
  "use strict";

  var CHAVE = "escala:cortina-escalas";
  var raiz = document.documentElement;

  function lembrado() {
    // localStorage pode estourar (modo privado, file://). Sem memória a
    // cortina só funciona nesta página — nunca vira erro na tela.
    try { return window.localStorage.getItem(CHAVE) === "aberta"; } catch (e) { return false; }
  }

  function lembrar(aberta) {
    try { window.localStorage.setItem(CHAVE, aberta ? "aberta" : "fechada"); } catch (e) { /* segue */ }
  }

  // ANTES do primeiro traço na tela: por isso este arquivo é carregado no
  // <head> sem `defer`. Aplicado aqui, o menu já nasce aberto e não pisca
  // fechado-e-abre a cada troca de mês ou de escala.
  if (lembrado()) { raiz.classList.add("cortina-aberta"); }

  document.addEventListener("DOMContentLoaded", function () {
    var puxador = document.getElementById("puxador-escalas");
    if (!puxador || !document.getElementById("menu-escalas")) { return; }
    var seta = puxador.querySelector(".seta-puxador");

    function mostrar(aberta, guardar) {
      raiz.classList.toggle("cortina-aberta", aberta);
      // A seta aponta para onde o clique leva: fora, abre; dentro, fecha.
      // Só a seta muda — trocar o innerHTML do botão apagaria o rótulo.
      if (seta) { seta.innerHTML = aberta ? "&#9668;" : "&#9658;"; }
      puxador.setAttribute("aria-expanded", aberta ? "true" : "false");
      puxador.setAttribute(
        "aria-label", aberta ? "Fechar a lista de escalas" : "Abrir a lista de escalas");
      if (guardar) { lembrar(aberta); }
    }

    mostrar(raiz.classList.contains("cortina-aberta"), false);   // acerta seta e rótulo

    puxador.addEventListener("click", function () {
      mostrar(!raiz.classList.contains("cortina-aberta"), true);
    });

    // Esc fecha e devolve o foco ao puxador — quem abriu pelo teclado precisa
    // de um caminho de volta que não seja o mouse.
    document.addEventListener("keydown", function (evento) {
      if (evento.key === "Escape" && raiz.classList.contains("cortina-aberta")) {
        mostrar(false, true);
        puxador.focus();
      }
    });
  });
})();
