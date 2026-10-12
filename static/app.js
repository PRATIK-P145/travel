/* Travel-Genie frontend: vanilla JS. Talks to GET /api/health and POST /api/plan. */
(function () {
  "use strict";

  var form = document.getElementById("plan-form");
  var results = document.getElementById("results");
  var submitBtn = document.getElementById("submit-btn");
  var pill = document.getElementById("mode-pill");
  var discoverLink = document.getElementById("discover-link");
  var depart = document.getElementById("departure_date");
  var ret = document.getElementById("return_date");
  var duration = document.getElementById("duration_days");
  var durationHint = document.getElementById("duration-hint");
  var lastPayload = null;
  var loadingTimer = null;

  // ---------- small DOM helper (text is always set via textContent, never innerHTML) ----------
  function h(tag, attrs) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === "class") node.className = attrs[k];
        else if (k === "text") node.textContent = attrs[k];
        else node.setAttribute(k, attrs[k]);
      });
    }
    function add(c) {
      if (c == null || c === false) return;
      if (Array.isArray(c)) return c.forEach(add);
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    for (var i = 2; i < arguments.length; i++) add(arguments[i]);
    return node;
  }

  function money(n, currency) {
    try {
      return new Intl.NumberFormat(currency === "INR" ? "en-IN" : undefined, {
        style: "currency", currency: currency, maximumFractionDigits: 0
      }).format(n);
    } catch (e) {
      return currency + " " + Math.round(n).toLocaleString();
    }
  }

  function prettyDate(iso) {
    var d = new Date(iso + "T00:00:00");
    return isNaN(d) ? iso : d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  }

  function isoDate(d) {
    var m = String(d.getMonth() + 1).padStart(2, "0");
    var day = String(d.getDate()).padStart(2, "0");
    return d.getFullYear() + "-" + m + "-" + day;
  }

  // ---------- form defaults and date logic ----------
  (function setDefaults() {
    var start = new Date();
    start.setDate(start.getDate() + 14);
    depart.value = isoDate(start);
    depart.min = isoDate(new Date());
    ret.min = depart.value;
  })();

  function syncDates() {
    ret.min = depart.value || isoDate(new Date());
    if (ret.value && depart.value) {
      var days = Math.round((new Date(ret.value) - new Date(depart.value)) / 86400000) + 1;
      if (days >= 1) duration.value = days;
      duration.disabled = true;
      durationHint.textContent = "Worked out from your dates.";
    } else {
      duration.disabled = false;
      durationHint.textContent = "Used when no return date is set.";
    }
  }
  depart.addEventListener("change", syncDates);
  ret.addEventListener("change", syncDates);

  // ---------- health pill ----------
  fetch("/api/health")
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var ai = d.integrations.gemini.configured;
      var groq = d.integrations.groq && d.integrations.groq.configured;
      var search = d.integrations.serpapi.configured;
      if (ai || groq || search) {
        pill.textContent = "Keys found: " + [ai ? "Gemini" : null, groq ? "Groq backup" : null, search ? "SerpApi" : null].filter(Boolean).join(" and ") + " (checked on first plan)";
        pill.classList.remove("demo");
      } else {
        pill.textContent = "Demo mode: no API keys set";
        pill.classList.add("demo");
      }
    })
    .catch(function () { pill.textContent = "Server not reachable"; pill.classList.add("demo"); });

  // ---------- submit ----------
  function readPayload() {
    var fd = new FormData(form);
    var payload = {
      start_city: (fd.get("start_city") || "").trim(),
      destination: (fd.get("destination") || "").trim(),
      departure_date: fd.get("departure_date"),
      travelers: Number(fd.get("travelers")),
      budget: Number(fd.get("budget")),
      currency: fd.get("currency"),
      travel_style: fd.get("travel_style"),
      interests: fd.getAll("interests"),
      accommodation: (fd.get("accommodation") || "").trim(),
      notes: (fd.get("notes") || "").trim()
    };
    if (fd.get("return_date")) payload.return_date = fd.get("return_date");
    else if (fd.get("duration_days")) payload.duration_days = Number(fd.get("duration_days"));
    return payload;
  }

  function clearInvalid() {
    form.querySelectorAll("[aria-invalid]").forEach(function (el) { el.removeAttribute("aria-invalid"); });
  }

  if (discoverLink) {
    discoverLink.addEventListener("click", function () {
      var destination = document.getElementById("destination").value.trim();
      if (destination) {
        var params = new URLSearchParams({ destination: destination, category: "tourist attractions" });
        discoverLink.href = "/discover?" + params.toString();
      } else {
        discoverLink.href = "/discover";
      }
    });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    clearInvalid();
    lastPayload = readPayload();
    requestPlan(lastPayload);
  });

  var LOADING_STEPS = [
    "Checking your trip details…",
    "Looking up places and sources…",
    "Drafting your day-by-day plan…",
    "Adding up the budget…"
  ];

  function showLoading() {
    var msg = h("span", { text: LOADING_STEPS[0] });
    results.replaceChildren(
      h("div", { class: "panel loading", role: "status" }, h("div", { class: "spinner", "aria-hidden": "true" }),
        h("div", null, h("strong", { text: "Planning your trip" }), h("br"), msg))
    );
    var i = 0;
    clearInterval(loadingTimer);
    loadingTimer = setInterval(function () { i = Math.min(i + 1, LOADING_STEPS.length - 1); msg.textContent = LOADING_STEPS[i]; }, 3500);
    results.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function stopLoading() { clearInterval(loadingTimer); submitBtn.disabled = false; submitBtn.textContent = "Plan my trip"; }

  function requestPlan(payload) {
    submitBtn.disabled = true;
    submitBtn.textContent = "Planning…";
    showLoading();
    fetch("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    })
      .then(function (resp) {
        return resp.json().catch(function () { return {}; }).then(function (body) { return { ok: resp.ok, body: body }; });
      })
      .then(function (r) {
        stopLoading();
        if (!r.ok) return showError(r.body && r.body.error);
        renderPlan(r.body);
      })
      .catch(function () {
        stopLoading();
        showError({ message: "Can't reach the Travel-Genie server. Check that it is running, then retry.", details: [] });
      });
  }

  // ---------- errors ----------
  function showError(err) {
    err = err || {};
    var details = err.details || [];
    details.forEach(function (d) {
      var el = d.field && document.getElementById(d.field);
      if (el) el.setAttribute("aria-invalid", "true");
    });
    var list = details.length ? h("ul", null, details.map(function (d) { return h("li", { text: d.message }); })) : null;
    var retry = h("button", { class: "btn", type: "button", text: "Retry" });
    retry.addEventListener("click", function () { if (lastPayload) requestPlan(lastPayload); });
    var edit = h("button", { class: "btn", type: "button", text: "Edit trip details" });
    edit.addEventListener("click", function () { form.scrollIntoView({ behavior: "smooth" }); document.getElementById("start_city").focus(); });
    results.replaceChildren(
      h("div", { class: "panel error", role: "alert" },
        h("h2", { text: "We couldn't plan that trip" }),
        h("p", { text: details.length ? "Fix these and try again:" : (err.message || "Something went wrong. Please try again.") }),
        list,
        h("div", { class: "again" }, retry, edit))
    );
    results.focus();
  }

  // ---------- rendering ----------
  function badge(text, cls) { return h("span", { class: "badge " + cls, text: text }); }

  // Explicit banner whenever neither configured AI provider produced the itinerary.
  function demoNotice(m) {
    var hint = m.ai_status === "not_configured"
      ? "No Gemini or Groq API key was found. Add GEMINI_API_KEY and/or GROQ_API_KEY to .env and restart the server."
      : "Gemini and any configured Groq backup could not produce a usable itinerary. Check the warning and server logs.";
    return h("div", { class: "notice", role: "alert" },
      h("strong", { text: "Demo mode: this itinerary was NOT generated by an AI provider." }),
      h("span", { text: " " + hint }));
  }

  function renderPlan(p) {
    var cur = p.trip.currency;
    var m = p.meta;
    var frag = document.createDocumentFragment();

    // Summary and provenance
    var live = m.itinerary_source === "gemini" || m.itinerary_source === "groq";
    var researched = m.research_source === "serpapi";
    var researchText = researched ? "Research: live Google results (SerpApi)" :
      m.research_source === "unavailable" ? "Research: unavailable, none used" : "Research: not used";
    var summary = h("div", { class: "panel" },
      h("div", { class: "summary-head" },
        h("div", null,
          h("h2", { text: p.trip.start_city + " to " + p.trip.destination }),
          h("p", { class: "facts", text:
            prettyDate(p.trip.departure_date) + " to " + prettyDate(p.trip.return_date) + ", " +
            p.trip.duration_days + (p.trip.duration_days === 1 ? " day" : " days") + ", " +
            p.trip.travelers + (p.trip.travelers === 1 ? " traveler" : " travelers") + ", " + p.trip.travel_style + " style" }))),
      h("div", { class: "badges" },
        badge(live ? "Itinerary: AI-generated (" + (m.model || (m.itinerary_source === "groq" ? "Groq" : "Gemini")) + ")" : "Itinerary: demo template, not AI", live ? "live" : "demo"),
        badge(researchText, researched ? "live" : "neutral"),
        badge("Prices: estimates, not verified", "neutral")),
      !live ? demoNotice(m) : null,
      h("p", { text: p.overview }),
      m.warnings.length ? h("ul", { class: "warnings" }, m.warnings.map(function (w) { return h("li", { text: w }); })) : null,
      h("p", { class: "disclaimer", text: m.disclaimer }));
    frag.appendChild(summary);

    // Budget
    var b = p.budget;
    var diffAbs = Math.abs(b.difference);
    var maxLine = Math.max.apply(null, b.lines.map(function (l) { return l.amount; }).concat([1]));
    var budget = h("div", { class: "panel" },
      h("h2", { text: "Estimated budget" }),
      h("div", { class: "money-cards" },
        h("div", { class: "money" }, h("div", { class: "k", text: "Estimated total" }), h("div", { class: "v", text: money(b.estimated_total, cur) }), h("div", { class: "fine", text: money(b.per_person, cur) + " per person" })),
        h("div", { class: "money" }, h("div", { class: "k", text: "Your budget" }), h("div", { class: "v", text: money(b.user_budget, cur) })),
        h("div", { class: "money " + (b.within_budget ? "under" : "over") },
          h("div", { class: "k", text: b.within_budget ? "Estimated to be under budget by" : "Estimated to be over budget by" }),
          h("div", { class: "v", text: money(diffAbs, cur) }))),
      b.lines.map(function (l) {
        var bar = h("i"); bar.style.width = Math.max(2, Math.round(l.amount / maxLine * 100)) + "%";
        return h("div", { class: "cat" }, h("span", { text: l.label }), h("div", { class: "bar", "aria-hidden": "true" }, bar), h("span", { class: "amt", text: money(l.amount, cur) }));
      }),
      h("ul", { class: "suggest" }, b.suggestions.map(function (s) { return h("li", { text: s }); })),
      h("p", { class: "fine", text: b.note }));
    frag.appendChild(budget);

    // Days
    p.days.forEach(function (d) {
      function slot(label, s) {
        return h("div", { class: "slot" },
          h("div", { class: "when", text: label }),
          h("h3", { text: s.title }),
          s.description ? h("p", { text: s.description }) : null,
          h("div", { class: "cost", text: s.est_cost ? "About " + money(s.est_cost, cur) + " for the group" : "No extra cost expected" }));
      }
      frag.appendChild(h("section", { class: "panel day", "aria-label": "Day " + d.day },
        h("div", { class: "day-head" },
          h("h2", { text: "Day " + d.day + ": " + d.title }),
          h("span", { class: "date", text: prettyDate(d.date) }),
          h("span", { class: "daycost", text: "Activities about " + money(d.est_day_cost, cur) })),
        h("div", { class: "slots" }, slot("Morning", d.morning), slot("Afternoon", d.afternoon), slot("Evening", d.evening)),
        h("div", { class: "day-foot" },
          h("dl", null, h("dt", { text: "Meal ideas" }),
            h("dd", { text: "Breakfast: " + d.meals.breakfast }), h("dd", { text: "Lunch: " + d.meals.lunch }), h("dd", { text: "Dinner: " + d.meals.dinner })),
          h("dl", null, h("dt", { text: "Getting around" }), h("dd", { text: d.transport })))));
    });

    // Stay
    frag.appendChild(h("div", { class: "panel" },
      h("h2", { text: "Where to stay" }),
      h("div", { class: "stays" }, p.accommodation.map(function (s) {
        return h("div", { class: "stay" }, h("h3", { text: s.area }), s.why ? h("p", { text: s.why }) : null,
          s.est_per_night ? h("p", { text: "Rough room rate: about " + money(s.est_per_night, cur) + " per night (estimate)" }) : null);
      }))));

    // Tips and caveats
    frag.appendChild(h("div", { class: "two-col" },
      h("div", { class: "panel" }, h("h2", { text: "Travel tips" }), h("ul", { class: "plain" }, p.tips.map(function (t) { return h("li", { text: t }); }))),
      h("div", { class: "panel" }, h("h2", { text: "Things to keep in mind" }), h("ul", { class: "plain" }, p.caveats.map(function (t) { return h("li", { text: t }); })))));

    // Sources
    var sourcesPanel = h("div", { class: "panel wide" }, h("h2", { text: "Research links" }));
    if (p.sources.length) {
      sourcesPanel.appendChild(h("ul", { class: "sources" }, p.sources.map(function (s) {
        return h("li", null,
          h("a", { href: s.url, target: "_blank", rel: "noopener noreferrer", text: s.title }),
          h("span", { class: "kind", text: s.kind === "maps" ? "Google Maps" : "Google Search" }),
          s.snippet ? h("p", { text: s.snippet }) : null);
      })));
      sourcesPanel.appendChild(h("p", { class: "fine", text: "Live results from SerpApi at the time of planning. Open them to confirm hours and prices." }));
    } else {
      sourcesPanel.appendChild(h("p", { text: "No live research links for this plan. Add a SerpApi key to include Google results." }));
    }
    frag.appendChild(sourcesPanel);

    // Actions
    var again = h("button", { class: "btn", type: "button", text: "Plan another trip" });
    again.addEventListener("click", function () { form.scrollIntoView({ behavior: "smooth" }); });
    var regen = h("button", { class: "btn", type: "button", text: "Regenerate this plan" });
    regen.addEventListener("click", function () { if (lastPayload) requestPlan(lastPayload); });
    frag.appendChild(h("div", { class: "again" }, regen, again));

    results.replaceChildren(frag);
    results.scrollIntoView({ behavior: "smooth", block: "start" });
    results.focus({ preventScroll: true });
  }
})();
