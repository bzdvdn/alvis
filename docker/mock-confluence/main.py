"""Minimal Confluence REST mock for developing Winnow locally.

Implements the two endpoints the ConfluenceSource adapter talks to:

  GET /rest/api/content?spaceKey=<key>&type=page&limit=<n>
      -> { "results": [ { "id", "title", "_links": {"webui"}, "version": {"number"} } ] }

  GET /rest/api/content/<id>?expand=body.storage
      -> { "body": { "storage": { "value": "<html>" } } }
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

SPACE_KEY = "TEAM"

PAGES: dict[str, dict] = {
    "100": {
        "title": "Winnow Overview",
        "version": 3,
        "html": (
            "<h1>Winnow Overview</h1><p>Winnow is a no-code knowledge "
            "ingestion engine for corporate knowledge bases.</p>"
            "<h2>Sources</h2><p>It ingests from Confluence, GitLab and S3.</p>"
            "<h2>Pipeline</h2><p>Source to artifact to extraction to chunking "
            "to embedding to index.</p>"
        ),
    },
    "101": {
        "title": "Chunking Strategy Guide",
        "version": 1,
        "html": (
            "<h1>Chunking Strategy Guide</h1><p>Documents are split into "
            "overlapping chunks.</p>"
            "<h2>Token Budget</h2><p>Chunks respect a token budget with "
            "paragraph boundaries.</p>"
            "<h2>Overlap</h2><p>Context is carried between consecutive "
            "chunks via overlap.</p>"
        ),
    },
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if re.fullmatch(r"/rest/api/content", path):
            results = [
                {
                    "id": page_id,
                    "title": page["title"],
                    "_links": {"webui": f"/pages/viewpage.action?pageId={page_id}"},
                    "version": {"number": page["version"]},
                }
                for page_id, page in PAGES.items()
            ]
            self._json({"results": results, "size": len(results)})
            return

        match = re.fullmatch(r"/rest/api/content/(\d+)", path)
        if match:
            page = PAGES.get(match.group(1))
            if page is None:
                self._json({"message": "Not Found"}, status=404)
                return
            if "expand=body.storage" in self.path:
                self._json(
                    {
                        "id": match.group(1),
                        "title": page["title"],
                        "version": {"number": page["version"]},
                        "_links": {"webui": f"/pages/viewpage.action?pageId={match.group(1)}"},
                        "body": {"storage": {"value": page["html"]}},
                    }
                )
                return
            self._json({"message": "unsupported expand"}, status=400)
            return

        self._json({"message": f"no mock route for {path}"}, status=404)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # noqa: D401
        pass


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 8090), Handler)
    print("mock-confluence listening on :8090", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()