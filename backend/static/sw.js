/* Minimal service worker: krävs för att appen ska vara installerbar.
   Ingen cachning — allt går rakt mot nätet (levande data, liten app).
   Skulle offline-stöd önskas senare byggs det här. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(clients.claim()));
self.addEventListener("fetch", () => {
  /* tomt = webbläsarens vanliga nätverksväg används */
});
