/* Discovery page: renders normalized SerpApi results safely with DOM text nodes. */
(function () {
  "use strict";
  var form = document.getElementById("discover-form");
  var destinationInput = document.getElementById("destination");
  var categoryInput = document.getElementById("category");
  var submit = document.getElementById("discover-submit");
  var results = document.getElementById("discover-results");
  var dialog = document.getElementById("offline-dialog");
  var closeDialog = document.getElementById("offline-close");

  function el(tag, attrs) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      if (key === "text") node.textContent = attrs[key];
      else if (key === "class") node.className = attrs[key];
      else node.setAttribute(key, attrs[key]);
    });
    for (var i = 2; i < arguments.length; i++) {
      var child = arguments[i];
      if (child == null) continue;
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return node;
  }

  function externalLink(url, label, className) {
    var link = el("a", { href: url, target: "_blank", rel: "noopener noreferrer", class: className || "btn", text: label });
    return link;
  }

  function showMessage(title, message, isError) {
    results.replaceChildren(el("div", { class: "panel" + (isError ? " error" : "") },
      el("h2", { text: title }), el("p", { text: message })));
  }

  function card(item) {
    var visual = item.thumbnail
      ? el("img", { class: "place-image", src: item.thumbnail, alt: "", loading: "lazy", referrerpolicy: "no-referrer" })
      : el("div", { class: "place-image place-image-empty", "aria-hidden": "true" }, el("span", { text: "✦" }));
    var details = el("div", { class: "place-details" });
    details.appendChild(el("span", { class: "place-category", text: item.category || "Place" }));
    details.appendChild(el("h2", { class: "place-title", text: item.title }));
    if (item.rating != null) {
      var ratingText = "★ " + Number(item.rating).toFixed(1);
      if (item.reviews != null) ratingText += " · " + Number(item.reviews).toLocaleString() + " reviews";
      details.appendChild(el("p", { class: "place-rating", text: ratingText }));
    }
    if (item.address) details.appendChild(el("p", { class: "place-address", text: item.address }));
    if (item.price) details.appendChild(el("p", { class: "place-meta", text: item.price }));
    if (item.hours) details.appendChild(el("p", { class: "place-meta", text: item.hours }));
    if (item.description) details.appendChild(el("p", { class: "place-description", text: item.description }));
    var actions = el("div", { class: "place-actions" });
    actions.appendChild(externalLink(item.website || item.maps_url, item.website ? "Visit website ↗" : "View on Google Maps ↗", "btn place-open"));
    var book = el("button", { type: "button", class: "go place-book", text: "Book" });
    book.addEventListener("click", function () {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else window.alert("Agent is offline. Booking assistance is planned for a future update.");
    });
    actions.appendChild(book);
    return el("article", { class: "place-card" }, visual, details, actions);
  }

  function search() {
    var destination = destinationInput.value.trim();
    var category = categoryInput.value;
    if (!destination) {
      destinationInput.setAttribute("aria-invalid", "true");
      destinationInput.focus();
      showMessage("Where would you like to explore?", "Enter an Indian city or destination to search.", true);
      return;
    }
    destinationInput.removeAttribute("aria-invalid");
    submit.disabled = true;
    submit.textContent = "Searching…";
    showMessage("Searching live results", "Checking Google Maps results for " + category + " in " + destination + "…", false);
    var params = new URLSearchParams({ destination: destination, category: category });
    window.history.replaceState(null, "", "/discover?" + params.toString());
    fetch("/api/discover?" + params.toString())
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (body) {
          if (!response.ok) throw new Error(body && body.error && body.error.message || "Could not load results. Please try again.");
          return body;
        });
      })
      .then(function (data) {
        if (!data.results || !data.results.length) {
          showMessage("No places found", "Try a nearby city, a shorter destination name, or another category.", false);
          return;
        }
        var heading = el("div", { class: "discover-results-head" },
          el("div", {}, el("h2", { text: data.category + " in " + data.destination }),
            el("p", { class: "facts", text: data.count + " results · Live SerpApi / Google Maps search" })));
        var grid = el("div", { class: "place-grid" });
        data.results.forEach(function (item) { grid.appendChild(card(item)); });
        results.replaceChildren(heading, grid,
          el("p", { class: "fine", text: "Use View on Google Maps or Visit website to confirm current details. The Book button is a future feature and does not make a reservation." }));
      })
      .catch(function (error) {
        showMessage("We couldn't load places", error.message || "Check that the server is running and SERPAPI_API_KEY is set in .env.", true);
      })
      .finally(function () {
        submit.disabled = false;
        submit.textContent = "Search places";
      });
  }

  form.addEventListener("submit", function (event) { event.preventDefault(); search(); });
  closeDialog.addEventListener("click", function () { dialog.close(); });
  dialog.addEventListener("click", function (event) { if (event.target === dialog) dialog.close(); });

  var params = new URLSearchParams(window.location.search);
  if (params.get("destination")) destinationInput.value = params.get("destination").slice(0, 100);
  if (params.get("category")) {
    var requested = params.get("category");
    if (Array.prototype.some.call(categoryInput.options, function (option) { return option.value === requested; })) categoryInput.value = requested;
  }
  if (destinationInput.value.trim()) search();
})();
