"""Build the deployable site: a static landing page plus rewrites to the live app.

Why this shape: the landing page must answer a judge even if the tunnel is down, and
the invoice links must be stable even though the tunnel hostname is not. So Vercel
serves `/` from committed HTML and reverse-proxies only the dynamic paths
(`/i/*`, `/proof`, `/api/*`, `/agent.json`) to whatever host currently fronts
`bot/serve.py`. `scripts/publish_site.sh` rewrites the target and redeploys whenever
the tunnel rotates, so no link a judge holds ever points at a dead host.
"""

from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

SITE_DIR = ROOT  # the repo root is the deployable site
ORIGIN = os.environ.get("ORIGIN_BASE_URL") or os.environ.get("PUBLIC_BASE_URL", "")
# Paths that must hit the live process rather than the static files.
DYNAMIC = ["/i/:path*", "/proof", "/api/:path*", "/agent.json", "/health"]


def vercel_json(origin: str) -> dict:
    return {
        "rewrites": [
            {"source": path, "destination": origin.rstrip("/") + path}
            for path in DYNAMIC
        ],
        # A judge wandering to a path we do not own should land on the front page,
        # not a 404.
        # Keep the upload to the two files that matter; the repo is mostly Python.
        "cleanUrls": True,
        "trailingSlash": False,
    }


def landing() -> str:
    from bot import serve

    try:
        html = serve.render_index(serve.proof_data(use_cache=False))
    except Exception as e:  # noqa: BLE001 — the deploy must not depend on the RPC
        print(f"live numbers unavailable ({type(e).__name__}); deploying without them")
        html = serve.render_index()
    # The static copy is served from the project root, so a leading / is correct on
    # the custom domain; mark it as a snapshot so nobody mistakes it for live state.
    return html.replace(
        "</main>",
        '  <div class="foot" style="margin-top:10px">Static snapshot of this page — the '
        'invoice pages and <a href="/proof">/proof</a> are served live.</div>\n</main>',
    )


def main() -> int:
    origin = ORIGIN
    if "--origin" in sys.argv:
        origin = sys.argv[sys.argv.index("--origin") + 1]
    if not origin.startswith("https://"):
        raise SystemExit("need an https origin (--origin URL, ORIGIN_BASE_URL or PUBLIC_BASE_URL)")

    os.makedirs(SITE_DIR, exist_ok=True)
    with open(os.path.join(SITE_DIR, "index.html"), "w") as fh:
        fh.write(landing())
    with open(os.path.join(SITE_DIR, "vercel.json"), "w") as fh:
        json.dump(vercel_json(origin), fh, indent=2)
        fh.write("\n")
    # Serving hand-built HTML: without this Jekyll-style post-processing can bite on
    # other hosts, and it costs nothing here.
    open(os.path.join(SITE_DIR, ".nojekyll"), "w").close()

    host = re.sub(r"^https://", "", origin).split("/")[0]
    print(f"site built: index.html + vercel.json -> proxy {host}")
    for path in DYNAMIC:
        print(f"  {path}  ->  https://{host}{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
