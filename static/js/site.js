/* AI24Times 클라이언트 스크립트. 모든 페이지가 같이 쓴다.
 * 본문은 이 스크립트 없이도 다 읽혀야 한다. 여기 있는 것은 전부 덧붙이는 기능이고,
 * 각 블록은 해당 요소가 있을 때만 동작한다. */
(function () {
  "use strict";

  var root = document.documentElement;
  var THEME_KEY = "ai24-theme";
  var prefix = root.getAttribute("data-prefix") || "";   // 한국어 "", 영어 "/en"

  function storeSet(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* 사생활 보호 모드 등 */ }
  }

  /* ---------- 테마 전환 ----------
   * 처음 값은 <head> 인라인 스크립트가 이미 적용했다. 여기서는 버튼만 처리한다. */
  var themeToggle = document.getElementById("theme-toggle");
  if (themeToggle) {
    themeToggle.addEventListener("click", function () {
      var current = root.getAttribute("data-theme");
      if (!current) {
        current = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
      }
      var next = current === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      storeSet(THEME_KEY, next);
    });
  }

  /* ---------- 현재 페이지 링크 복사 ----------
   * 클립보드를 못 쓰는 환경(오래된 브라우저, http 페이지)에서는 버튼을 숨긴 채로 둔다.
   * 눌러도 아무 일이 없는 버튼보다 없는 편이 낫다. */
  var copyBtn = document.getElementById("copy-link");
  var copyTip = document.getElementById("copy-tip");
  if (copyBtn && navigator.clipboard && navigator.clipboard.writeText) {
    copyBtn.hidden = false;
    var copyTimer = null;
    copyBtn.addEventListener("click", function () {
      navigator.clipboard.writeText(location.href).then(function () {
        // 글자를 이 순간에 넣어야 aria-live 가 읽어 준다(미리 있던 글자는 변화로 치지 않는다).
        copyTip.textContent = copyBtn.getAttribute("data-copied");
        copyTip.classList.add("is-on");
        copyBtn.classList.add("is-done");
        clearTimeout(copyTimer);
        copyTimer = setTimeout(function () {
          copyTip.classList.remove("is-on");
          copyBtn.classList.remove("is-done");
          copyTip.textContent = "";
        }, 1600);
      }).catch(function () { /* 권한 거부 등: 조용히 넘어간다 */ });
    });
  }

  /* ---------- 언어 전환 ----------
   * 링크 자체는 같은 페이지의 다른 언어판을 가리킨다(스크립트 없이도 동작).
   * 여기서는 보던 위치(#a3)와 검색어(?q=)를 함께 넘겨 준다. */
  var langLink = document.querySelector("[data-lang-switch]");
  if (langLink) {
    langLink.addEventListener("click", function () {
      langLink.href = langLink.href.split(/[?#]/)[0] + location.search + location.hash;
    });
  }

  /* ---------- 홈 배너: 최근 브리핑 넘겨 보기 ----------
   * 스크립트가 없으면 첫 장만 보인다. 자동으로 넘기지는 않는다(읽는 중에 바뀌면 불편하다). */
  var carousel = document.querySelector("[data-carousel]");
  if (carousel) {
    var slides = carousel.querySelectorAll("[data-slide]");
    var dots = carousel.querySelectorAll("[data-dots] button");
    var current = 0;
    if (slides.length > 1) {
      var show = function (i) {
        current = (i + slides.length) % slides.length;
        slides.forEach(function (s, n) { s.hidden = n !== current; });
        dots.forEach(function (d, n) {
          if (n === current) d.setAttribute("aria-current", "true");
          else d.removeAttribute("aria-current");
        });
      };
      carousel.querySelectorAll("[data-prev], [data-next], [data-dots]").forEach(function (el) { el.hidden = false; });
      carousel.querySelector("[data-prev]").addEventListener("click", function () { show(current - 1); });
      carousel.querySelector("[data-next]").addEventListener("click", function () { show(current + 1); });
      dots.forEach(function (d, n) { d.addEventListener("click", function () { show(n); }); });
      carousel.addEventListener("keydown", function (e) {
        if (e.key === "ArrowLeft") show(current - 1);
        if (e.key === "ArrowRight") show(current + 1);
      });
    }
  }

  /* ---------- 용어 패널 ---------- */
  var panel = document.getElementById("term-panel");
  var backdrop = document.getElementById("term-backdrop");
  var glossaryEl = document.getElementById("glossary-data");
  if (panel && backdrop && glossaryEl) {
    var glossary = {};
    try { glossary = JSON.parse(glossaryEl.textContent); } catch (e) { /* 패널만 안 열린다 */ }

    var titleEl = document.getElementById("term-panel-title");
    var enEl = document.getElementById("term-panel-en");
    var bodyEl = document.getElementById("term-panel-body");
    var moreEl = document.getElementById("term-panel-more");
    var closeBtn = document.getElementById("term-panel-close");
    var opener = null;

    var openTerm = function (slug, trigger) {
      var t = glossary[slug];
      if (!t) return;
      titleEl.textContent = t.name;
      enEl.textContent = t.alt || "";   // 다른 언어 이름 (한국어판이면 영어, 영어판이면 한국어)
      bodyEl.textContent = t.body;
      moreEl.href = prefix + "/glossary.html#" + slug;
      opener = trigger;
      panel.hidden = false;
      backdrop.hidden = false;
      root.style.overflow = "hidden";
      closeBtn.focus();
    };
    var closeTerm = function () {
      if (panel.hidden) return;
      panel.hidden = true;
      backdrop.hidden = true;
      root.style.overflow = "";
      if (opener) opener.focus();
      opener = null;
    };

    document.addEventListener("click", function (e) {
      var btn = e.target.closest(".term[data-term]");
      if (btn) openTerm(btn.getAttribute("data-term"), btn);
    });
    closeBtn.addEventListener("click", closeTerm);
    backdrop.addEventListener("click", closeTerm);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeTerm();
    });
  }

  /* ---------- 목차: 지금 읽는 기사 표시 ---------- */
  var tocLinks = document.querySelectorAll(".toc-list a[href^='#']");
  if (tocLinks.length && "IntersectionObserver" in window) {
    var linkById = {};
    tocLinks.forEach(function (a) { linkById[a.getAttribute("href").slice(1)] = a; });
    var active = null;
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var link = linkById[entry.target.id];
        if (!link || link === active) return;
        if (active) { active.classList.remove("is-active"); active.removeAttribute("aria-current"); }
        link.classList.add("is-active");
        link.setAttribute("aria-current", "true");
        active = link;
      });
    }, { rootMargin: "-30% 0px -60% 0px" }); // 화면 위쪽 1/3 지점을 지나는 기사를 "읽는 중"으로 본다
    Object.keys(linkById).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) observer.observe(el);
    });
  }

  /* ---------- 지난 기사 검색 ----------
   * /search-index.json 을 처음 검색할 때 한 번만 받아서 브라우저에서 거른다.
   * 상단 바 검색창은 /archive/?q=... 로 보내고, 여기서 그 값을 받아 바로 검색한다. */
  var searchInput = document.getElementById("search-input");
  var results = document.getElementById("search-results");
  var status = document.getElementById("search-status");
  if (searchInput && results && status) {
    var index = null;
    var loading = null;
    var MAX_RESULTS = 30;
    // 안내 문구는 페이지 언어에 맞춰 템플릿이 data-* 로 넣어 둔다. {q}, {n} 을 바꿔 쓴다.
    var msg = function (name, vars) {
      return (searchInput.getAttribute("data-" + name) || "").replace(/\{(\w+)\}/g, function (_, k) { return vars[k]; });
    };

    var loadIndex = function () {
      if (!loading) {
        loading = fetch(prefix + "/search-index.json")
          .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
          .then(function (data) { index = data; })
          .catch(function () { status.textContent = msg("error", {}); });
      }
      return loading;
    };

    var el = function (tag, cls, text, href) {
      var node = document.createElement(tag);
      node.className = cls;
      node.textContent = text;
      if (href) node.href = href;
      return node;
    };

    var render = function (q) {
      results.textContent = "";
      if (!q) { status.textContent = ""; return; }
      var words = q.toLowerCase().split(/\s+/).filter(Boolean);
      var hits = index.filter(function (item) {
        var hay = (item.title + " " + item.title_original + " " + item.source + " " + item.topic + " " + item.summary).toLowerCase();
        return words.every(function (w) { return hay.indexOf(w) !== -1; });
      });
      status.textContent = hits.length
        ? msg("found", { q: q, n: hits.length }) + (hits.length > MAX_RESULTS ? msg("more", { n: MAX_RESULTS }) : "")
        : msg("none", { q: q });
      hits.slice(0, MAX_RESULTS).forEach(function (item) {
        var li = document.createElement("li");
        li.append(el("a", "d", item.date, item.page), el("a", "h", item.title, item.page), el("span", "s", item.source));
        results.appendChild(li);
      });
    };

    var run = function (q) {
      loadIndex().then(function () { if (index) render(q); });
    };

    var timer = null;
    searchInput.addEventListener("input", function () {
      clearTimeout(timer);
      var q = searchInput.value.trim();
      timer = setTimeout(function () { run(q); }, 120);
    });

    var fromBar = new URLSearchParams(location.search).get("q");
    if (fromBar) {
      searchInput.value = fromBar;
      var barInput = document.getElementById("bar-q");
      if (barInput) barInput.value = fromBar;
      run(fromBar.trim());
    }
  }
})();
