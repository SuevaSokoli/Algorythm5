// Classic (non-module) script loaded in <head>: applies the saved theme before first paint,
// then wires the light / dark / auto switch. Self-contained, defines no globals.
(function () {
  "use strict";
  var KEY = "algorythm5-theme";
  var root = document.documentElement;

  function read() {
    try {
      return localStorage.getItem(KEY) || "system";
    } catch (e) {
      return "system";
    }
  }

  function apply(choice) {
    if (choice === "light" || choice === "dark") {
      root.setAttribute("data-theme", choice);
    } else {
      root.removeAttribute("data-theme");
    }
  }

  function save(choice) {
    try {
      localStorage.setItem(KEY, choice);
    } catch (e) {
      /* storage unavailable (private mode): the choice lasts for this page only */
    }
  }

  apply(read());

  document.addEventListener("DOMContentLoaded", function () {
    var buttons = document.querySelectorAll("[data-theme-choice]");
    function sync(choice) {
      buttons.forEach(function (button) {
        button.setAttribute("aria-pressed", String(button.getAttribute("data-theme-choice") === choice));
      });
    }
    buttons.forEach(function (button) {
      button.addEventListener("click", function () {
        var choice = button.getAttribute("data-theme-choice");
        apply(choice);
        save(choice);
        sync(choice);
      });
    });
    sync(read());
  });
})();
