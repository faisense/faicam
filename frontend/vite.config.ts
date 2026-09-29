import preact from "@preact/preset-vite";
import { defineConfig, type Plugin } from "vite";

// faicam serves the built pages itself (src/faicam/server.py), so the
// build goes straight into the Python package and ships in the wheel.
//
// `npm run dev` serves the pages with instant reload and passes everything else to
// a running faicam (FAICAM_URL, http://localhost:7380 by default).
const faicam = process.env.FAICAM_URL ?? "http://localhost:7380";

export default defineConfig({
  plugins: [preact(), cleanUrls()],
  build: {
    outDir: "../src/faicam/static",
    emptyOutDir: true,
    target: "es2020",
    rollupOptions: {
      input: { index: "index.html", stats: "stats.html", viewer: "viewer.html" },
    },
  },
  server: {
    proxy: { "/api": faicam, "/live": faicam, "/snapshot": faicam },
  },
});

/** Serve /stats like faicam does (as stats.html) while developing. */
function cleanUrls(): Plugin {
  return {
    name: "faicam-clean-urls",
    configureServer(server) {
      server.middlewares.use((req, _res, next) => {
        if (req.url === "/stats" || req.url?.startsWith("/stats?")) {
          req.url = req.url.replace("/stats", "/stats.html");
        }
        next();
      });
    },
  };
}
