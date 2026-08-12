/* Canvas sermon archive — client behaviour.
 *
 * Three independent pieces, each a no-op when its markup is absent:
 *   theme()      light/dark, remembered
 *   player()     YouTube IFrame API: click a timestamp to seek, active cue tracks playback
 *   finder()     in-page occurrence search with timestamps, driven by ?q= or the input
 */

(function () {
  'use strict';

  /* ------------------------------------------------------------------ theme */

  function theme() {
    var KEY = 'canvas-theme';
    var saved = null;
    try { saved = localStorage.getItem(KEY); } catch (e) { /* private mode */ }
    var mq = window.matchMedia('(prefers-color-scheme: dark)');
    apply(saved || (mq.matches ? 'dark' : 'light'));

    function apply(mode) {
      document.documentElement.setAttribute('data-theme', mode);
      var btn = document.getElementById('theme-toggle');
      if (btn) {
        btn.textContent = mode === 'dark' ? 'light' : 'dark';
        btn.setAttribute('aria-label', 'Switch to ' + btn.textContent + ' theme');
      }
    }

    var btn = document.getElementById('theme-toggle');
    if (btn) {
      btn.addEventListener('click', function () {
        var next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        apply(next);
        try { localStorage.setItem(KEY, next); } catch (e) { /* ignore */ }
      });
    }
  }

  /* ----------------------------------------------------------------- player */

  var player = null;      // YT.Player once ready
  var cues = [];          // [{el, start}] ascending by start
  var activeIdx = -1;
  var follow = true;      // auto-scroll the transcript with playback

  function initPlayer() {
    var mount = document.getElementById('player');
    if (!mount) return;
    var videoId = mount.getAttribute('data-video');

    cues = [].slice.call(document.querySelectorAll('.cue')).map(function (el) {
      return { el: el, start: parseFloat(el.getAttribute('data-t')) || 0 };
    });

    // Delegate: the gutter timestamp is the seek control.
    document.addEventListener('click', function (ev) {
      var seek = ev.target.closest ? ev.target.closest('.seek') : null;
      if (!seek) return;
      var cue = seek.closest('.cue');
      if (!cue) return;
      ev.preventDefault();
      var t = parseFloat(cue.getAttribute('data-t')) || 0;
      if (player && player.seekTo) {
        player.seekTo(t, true);
        if (player.playVideo) player.playVideo();
        setActive(cues.indexOf(cues.filter(function (c) { return c.el === cue; })[0]), false);
        mount.scrollIntoView({ block: 'nearest' });
      } else {
        // API not up yet (or blocked): fall back to YouTube itself.
        window.open('https://www.youtube.com/watch?v=' + videoId + '&t=' + Math.floor(t) + 's', '_blank');
      }
    });

    var tag = document.createElement('script');
    tag.src = 'https://www.youtube.com/iframe_api';
    document.head.appendChild(tag);

    window.onYouTubeIframeAPIReady = function () {
      player = new YT.Player('player', {
        videoId: videoId,
        playerVars: { rel: 0, modestbranding: 1, playsinline: 1 },
        events: { onStateChange: onState }
      });
    };

    var timer = null;
    function onState(ev) {
      if (ev.data === YT.PlayerState.PLAYING) {
        if (!timer) timer = setInterval(tick, 500);
      } else if (timer) {
        clearInterval(timer);
        timer = null;
      }
    }

    function tick() {
      if (!player || !player.getCurrentTime) return;
      var t = player.getCurrentTime();
      // cues are sorted; walk from the current position rather than rescanning.
      var i = activeIdx >= 0 ? activeIdx : 0;
      while (i + 1 < cues.length && cues[i + 1].start <= t) i++;
      while (i > 0 && cues[i].start > t) i--;
      if (i !== activeIdx) setActive(i, follow);
    }

    // Let the reader scroll away without being yanked back.
    var scrollLock = null;
    window.addEventListener('scroll', function () {
      if (scrollLock) return;
      follow = false;
      var btn = document.getElementById('follow-toggle');
      if (btn) btn.setAttribute('aria-pressed', 'false');
    }, { passive: true });

    var followBtn = document.getElementById('follow-toggle');
    if (followBtn) {
      followBtn.addEventListener('click', function () {
        follow = !follow;
        followBtn.setAttribute('aria-pressed', String(follow));
        if (follow && activeIdx >= 0) scrollTo(cues[activeIdx].el);
      });
    }

    function scrollTo(el) {
      scrollLock = true;
      el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      setTimeout(function () { scrollLock = null; }, 700);
    }

    function setActive(i, doScroll) {
      if (i < 0 || i >= cues.length) return;
      if (activeIdx >= 0 && cues[activeIdx]) cues[activeIdx].el.classList.remove('is-active');
      activeIdx = i;
      cues[i].el.classList.add('is-active');
      if (doScroll) scrollTo(cues[i].el);
    }

    window.__seekTo = function (t) {
      if (player && player.seekTo) { player.seekTo(t, true); player.playVideo(); }
    };
  }

  /* ----------------------------------------------------------------- finder */

  function finder() {
    var input = document.getElementById('find');
    var saids = [].slice.call(document.querySelectorAll('.cue > .said'));
    if (!input || !saids.length) return;

    var countEl = document.getElementById('find-count');
    var originals = saids.map(function (p) { return p.textContent; });
    var hits = [];
    var cursor = -1;

    function clear() {
      saids.forEach(function (p, i) {
        if (p.querySelector('mark')) p.textContent = originals[i];
        p.parentNode.classList.remove('is-hit');
      });
      hits = [];
      cursor = -1;
    }

    function run(term) {
      clear();
      term = (term || '').trim();
      if (term.length < 2) {
        countEl.textContent = '';
        return;
      }
      var needle = term.toLowerCase();
      saids.forEach(function (p, i) {
        var hay = originals[i];
        var low = hay.toLowerCase();
        if (low.indexOf(needle) === -1) return;

        var frag = document.createDocumentFragment();
        var pos = 0;
        for (;;) {
          var at = low.indexOf(needle, pos);
          if (at === -1) break;
          if (at > pos) frag.appendChild(document.createTextNode(hay.slice(pos, at)));
          var m = document.createElement('mark');
          m.textContent = hay.slice(at, at + needle.length);
          frag.appendChild(m);
          hits.push(m);
          pos = at + needle.length;
        }
        if (pos < hay.length) frag.appendChild(document.createTextNode(hay.slice(pos)));
        p.textContent = '';
        p.appendChild(frag);
        p.parentNode.classList.add('is-hit');
      });

      countEl.textContent = hits.length
        ? hits.length + (hits.length === 1 ? ' hit' : ' hits')
        : 'no hits';
      if (hits.length) step(0);
    }

    function step(i) {
      if (!hits.length) return;
      if (cursor >= 0 && hits[cursor]) hits[cursor].classList.remove('on');
      cursor = (i + hits.length) % hits.length;
      var m = hits[cursor];
      m.classList.add('on');
      m.scrollIntoView({ block: 'center', behavior: 'smooth' });
      countEl.textContent = (cursor + 1) + ' / ' + hits.length;
    }

    var debounce = null;
    input.addEventListener('input', function () {
      clearTimeout(debounce);
      debounce = setTimeout(function () { run(input.value); }, 140);
    });
    input.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter') { ev.preventDefault(); step(cursor + (ev.shiftKey ? -1 : 1)); }
      if (ev.key === 'Escape') { input.value = ''; run(''); input.blur(); }
    });

    var prev = document.getElementById('find-prev');
    var next = document.getElementById('find-next');
    if (prev) prev.addEventListener('click', function () { step(cursor - 1); });
    if (next) next.addEventListener('click', function () { step(cursor + 1); });

    // Arriving from a search result: ?q=grace highlights straight away.
    var q = new URLSearchParams(location.search).get('q');
    if (q) { input.value = q; run(q); }

    document.addEventListener('keydown', function (ev) {
      if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
      var tag = (ev.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea') return;
      if (ev.key === 'f' || ev.key === '/') { ev.preventDefault(); input.focus(); input.select(); }
      if (ev.key === 'n') step(cursor + 1);
      if (ev.key === 'N') step(cursor - 1);
    });
  }

  /* ------------------------------------------------------- copy a passage */

  function copiers() {
    var cites = document.querySelectorAll('.cue');
    if (!cites.length) return;
    var meta = document.getElementById('cite-meta');
    if (!meta) return;
    var title = meta.getAttribute('data-title');
    var date = meta.getAttribute('data-date');
    var vid = meta.getAttribute('data-video');

    cites.forEach(function (cue) {
      var btn = document.createElement('button');
      btn.className = 'cue-copy';
      btn.type = 'button';
      btn.textContent = 'copy';
      btn.title = 'Copy this passage with citation';
      btn.addEventListener('click', function () {
        var t = Math.floor(parseFloat(cue.getAttribute('data-t')) || 0);
        var said = cue.querySelector('.said');
        var stamp = cue.querySelector('.seek');
        var text = '"' + (said ? said.textContent.trim() : '') + '"\n\n'
          + title + ' (' + date + '), ' + (stamp ? stamp.textContent.trim() : '') + '\n'
          + 'https://www.youtube.com/watch?v=' + vid + '&t=' + t + 's';
        navigator.clipboard.writeText(text).then(function () {
          btn.textContent = 'copied';
          setTimeout(function () { btn.textContent = 'copy'; }, 1400);
        }, function () {
          btn.textContent = 'failed';
          setTimeout(function () { btn.textContent = 'copy'; }, 1400);
        });
      });
      cue.appendChild(btn);
    });
  }

  /* ------------------------------------------------------- global shortcuts */

  function shortcuts() {
    document.addEventListener('keydown', function (ev) {
      if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
      var tag = (ev.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea') return;
      // '/' on pages without an in-page finder goes to global search.
      if (ev.key === '/' && !document.getElementById('find')) {
        var box = document.querySelector('.pagefind-ui__search-input');
        if (box) { ev.preventDefault(); box.focus(); }
        else if (!location.pathname.match(/\/search\/?$/)) {
          ev.preventDefault();
          location.href = document.body.getAttribute('data-root') + 'search/';
        }
      }
    });
  }

  function ready(fn) {
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  ready(function () {
    theme();
    initPlayer();
    finder();
    copiers();
    shortcuts();
  });
})();
