"use strict";

var CACHE_NAME = "qcl-pwa-v1";
var APP_SCOPE = self.registration.scope;
var APP_ROOT = new URL("./", APP_SCOPE);
var APP_FILES = [
  "./",
  "./index.html",
  "./pwa.css",
  "./pwa.js",
  "./qcl-pwa-config.js",
  "./manifest.webmanifest",
  "./icons/qcl-mark.svg",
  "./icons/qcl-icon-180.png",
  "./icons/qcl-icon-192.png",
  "./icons/qcl-icon-512.png",
  "./qcl_news.json"
].map(function (path) {
  return new URL(path, APP_SCOPE).href;
});
var NEWS_URL = new URL("./qcl_news.json", APP_SCOPE).href;

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(function (cache) {
        return cache.addAll(APP_FILES);
      })
      .then(function () {
        return self.skipWaiting();
      })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (names) {
        return Promise.all(names.map(function (name) {
          if (name.indexOf("qcl-pwa-") === 0 && name !== CACHE_NAME) {
            return caches.delete(name);
          }
          return Promise.resolve(false);
        }));
      })
      .then(function () {
        return self.clients.claim();
      })
  );
});

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") {
    return;
  }

  var url = new URL(request.url);
  if (url.origin !== self.location.origin || !url.href.startsWith(APP_ROOT.href)) {
    return;
  }

  if (url.href === NEWS_URL) {
    event.respondWith(
      fetch(request)
        .then(function (response) {
          if (response.ok) {
            var copy = response.clone();
            caches.open(CACHE_NAME).then(function (cache) {
              cache.put(request, copy);
            });
          }
          return response;
        })
        .catch(function () {
          return caches.match(request);
        })
    );
    return;
  }

  if (APP_FILES.indexOf(url.href) !== -1) {
    event.respondWith(
      caches.match(request).then(function (cached) {
        if (cached) {
          return cached;
        }
        return fetch(request).then(function (response) {
          if (response.ok) {
            var copy = response.clone();
            caches.open(CACHE_NAME).then(function (cache) {
              cache.put(request, copy);
            });
          }
          return response;
        });
      })
    );
    return;
  }

  if (request.mode === "navigate" && url.href === APP_ROOT.href) {
    event.respondWith(
      fetch(request).catch(function () {
        return caches.match(new URL("./index.html", APP_SCOPE).href);
      })
    );
  }
});
