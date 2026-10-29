/* AI24Times 클라이언트 스크립트. 모든 페이지가 같이 쓴다.
 * 본문은 이 스크립트 없이도 다 읽혀야 한다. 여기 있는 것은 전부 덧붙이는 기능이고,
 * 각 블록은 해당 요소가 있을 때만 동작한다. */
(function () {
  "use strict";

  var root = document.documentElement;
  var THEME_KEY = "ai24-theme";

  function storeGet(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }
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

    function openTerm(slug, trigger) {
      var t = glossary[slug];
      if (!t) return;
      titleEl.textContent = t.name;
      enEl.textContent = t.en || "";
      bodyEl.textContent = t.body;
      moreEl.href = "/glossary.html#" + slug;
      opener = trigger;
      panel.hidden = false;
      backdrop.hidden = false;
      root.style.overflow = "hidden";
      closeBtn.focus();
    }
    function closeTerm() {
      if (panel.hidden) return;
      panel.hidden = true;
      backdrop.hidden = true;
      root.style.overflow = "";
      if (opener) opener.focus();
      opener = null;
    }

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
    }, { rootMargin: "-30% 0px -60% 0px" }); // 화면 위쪽 1/3 지점을 지나는 카드를 "읽는 중"으로 본다
    Object.keys(linkById).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) observer.observe(el);
    });
  }

  /* ---------- 지난 호 검색 ----------
   * /search-index.json 을 처음 입력할 때 한 번만 받아서 브라우저에서 거른다. */
  var searchInput = document.getElementById("search-input");
  var results = document.getElementById("search-results");
  var status = document.getElementById("search-status");
  if (searchInput && results && status) {
    var index = null;
    var loading = null;
    var MAX_RESULTS = 30;

    function loadIndex() {
      if (!loading) {
        loading = fetch("/search-index.json")
          .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
          .then(function (data) { index = data; })
          .catch(function () { status.textContent = "검색 색인을 불러오지 못했습니다."; });
      }
      return loading;
    }

    function render(q) {
      results.textContent = "";
      if (!q) { status.textContent = ""; return; }
      var words = q.toLowerCase().split(/\s+/).filter(Boolean);
      var hits = index.filter(function (item) {
        var hay = (item.title + " " + item.title_original + " " + item.source + " " + item.topic + " " + item.summary).toLowerCase();
        return words.every(function (w) { return hay.indexOf(w) !== -1; });
      });
      status.textContent = hits.length
        ? hits.length + "건 찾음" + (hits.length > MAX_RESULTS ? " (처음 " + MAX_RESULTS + "건만 표시)" : "")
        : "찾는 기사가 없습니다.";
      hits.slice(0, MAX_RESULTS).forEach(function (item) {
        var li = document.createElement("li");
        li.className = "row";
        var date = document.createElement("a");
        date.className = "row-date";
        date.href = item.page;
        date.textContent = item.date;
        var title = document.createElement("a");
        title.className = "row-title";
        title.href = item.page;
        title.textContent = item.title;
        var source = document.createElement("span");
        source.className = "row-source";
        source.textContent = item.source;
        li.append(date, title, source);
        results.appendChild(li);
      });
    }

    var timer = null;
    searchInput.addEventListener("input", function () {
      clearTimeout(timer);
      var q = searchInput.value.trim();
      timer = setTimeout(function () {
        loadIndex().then(function () { if (index) render(q); });
      }, 120);
    });
  }
})();
