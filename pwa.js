(function () {
  "use strict";

  var status = document.getElementById("connection-status");
  var newsList = document.getElementById("news-list");
  var newsUpdated = document.getElementById("news-updated");
  var installButton = document.getElementById("install-app");
  var installHelp = document.getElementById("install-help");
  var installHelpCopy = document.getElementById("install-help-copy");
  var hubLink = document.getElementById("open-hub");
  var hubNote = document.getElementById("hub-config-note");
  var deferredInstallPrompt = null;
  var config = window.QCL_PWA_CONFIG || {};

  function setConnectionStatus() {
    var online = navigator.onLine;
    status.classList.toggle("is-online", online);
    status.classList.toggle("is-offline", !online);
    status.lastElementChild.textContent = online ? "Online" : "Offline · saved news";
  }

  function safeHttpsUrl(value) {
    try {
      var url = new URL(value);
      if (url.protocol === "https:" && !url.username && !url.password) {
        return url.href;
      }
    } catch (error) {
      return "";
    }
    return "";
  }

  function configureHubLink() {
    var url = safeHttpsUrl(config.hubUrl || "");
    if (url) {
      hubLink.href = url;
      hubLink.target = "_blank";
      hubLink.rel = "noopener noreferrer";
      hubLink.removeAttribute("aria-disabled");
      hubNote.hidden = true;
    } else {
      hubLink.removeAttribute("href");
      hubLink.setAttribute("aria-disabled", "true");
      hubNote.textContent = "To connect the full hub, add its live HTTPS address to qcl-pwa-config.js.";
    }
  }

  function makeNewsCard(post) {
    var article = document.createElement("article");
    article.className = "news-card";

    var accent = document.createElement("span");
    accent.className = "news-accent";
    accent.setAttribute("aria-hidden", "true");

    var content = document.createElement("div");
    var date = document.createElement("p");
    date.className = "news-date";
    date.textContent = String(post.date || "QCL NEWS");
    content.appendChild(date);

    var title = document.createElement("h3");
    title.textContent = String(post.title || "League update");
    content.appendChild(title);

    var body = document.createElement("p");
    body.textContent = String(post.body || "");
    content.appendChild(body);

    var url = safeHttpsUrl(post.url || "");
    if (url) {
      var link = document.createElement("a");
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "Read more";
      content.appendChild(link);
    }

    article.appendChild(accent);
    article.appendChild(content);
    return article;
  }

  function renderNews(payload) {
    var posts = payload && Array.isArray(payload.posts) ? payload.posts : [];
    posts = posts.filter(function (post) {
      return post && typeof post === "object" && post.title && post.body;
    });
    posts.sort(function (left, right) {
      return String(right.date || "").localeCompare(String(left.date || ""));
    });

    newsList.replaceChildren();
    if (!posts.length) {
      var empty = document.createElement("p");
      empty.className = "empty-news";
      empty.textContent = "No league announcements are posted yet. New QCL updates will show here.";
      newsList.appendChild(empty);
    } else {
      posts.slice(0, 8).forEach(function (post) {
        newsList.appendChild(makeNewsCard(post));
      });
    }
    newsUpdated.textContent = "LATEST";
  }

  function loadNews() {
    var newsUrl = new URL("./qcl_news.json", window.location.href);
    fetch(newsUrl.href, { cache: "no-cache" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("News is temporarily unavailable.");
        }
        return response.json();
      })
      .then(renderNews)
      .catch(function () {
        newsList.replaceChildren();
        var message = document.createElement("p");
        message.className = "empty-news";
        message.textContent = "QCL news could not refresh. If you are offline, reconnect to load the latest updates.";
        newsList.appendChild(message);
        newsUpdated.textContent = navigator.onLine ? "UPDATE UNAVAILABLE" : "OFFLINE";
      });
  }

  var isStandalone = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  var isIos = /iphone|ipad|ipod/i.test(window.navigator.userAgent) ||
    (window.navigator.platform === "MacIntel" && window.navigator.maxTouchPoints > 1);

  if (isStandalone) {
    installHelp.hidden = true;
  } else if (isIos) {
    installHelp.open = true;
    installHelpCopy.textContent = "In Safari, tap Share, then choose “Add to Home Screen.”";
  }

  installButton.addEventListener("click", function () {
    if (!deferredInstallPrompt) {
      installHelp.open = true;
      installHelp.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }

    deferredInstallPrompt.prompt();
    deferredInstallPrompt.userChoice.then(function () {
      deferredInstallPrompt = null;
      installButton.hidden = true;
    });
  });

  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    deferredInstallPrompt = event;
    installButton.hidden = false;
  });

  window.addEventListener("appinstalled", function () {
    installButton.hidden = true;
    installHelp.hidden = true;
  });

  window.addEventListener("online", setConnectionStatus);
  window.addEventListener("offline", setConnectionStatus);
  setConnectionStatus();
  configureHubLink();
  loadNews();

  if ("serviceWorker" in navigator && window.location.protocol !== "file:") {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("./service-worker.js", { scope: "./" }).catch(function () {
        status.lastElementChild.textContent = navigator.onLine ? "Online" : "Offline";
      });
    });
  }
})();
