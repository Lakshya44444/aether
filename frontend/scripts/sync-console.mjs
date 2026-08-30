// The operator console is plain HTML/CSS/JS served by the gateway from dashboard/.
// The Next.js static export can only ship what is in public/, so it is copied there at
// build time rather than kept as a second committed copy -- the two had already gone
// identical-but-separate, which is the state right before they diverge.
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const from = join(here, "..", "..", "dashboard");
const to = join(here, "..", "public");

mkdirSync(to, { recursive: true });
for (const file of ["console.html", "app.js", "styles.css"]) {
  copyFileSync(join(from, file), join(to, file));
}
console.log(`synced console from dashboard/ -> public/`);
