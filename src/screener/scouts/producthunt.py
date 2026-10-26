"""Product Hunt GraphQL v2: posts in education topics in the last 18 months, with maker names.
Disabled until PRODUCTHUNT_TOKEN is set. The API redacts some user data; redacted makers are skipped."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from screener.scouts.base import BaseScout, DiscoveryItem

QUERY = """
query($topic: String!, $after: String, $postedAfter: DateTime!) {
  posts(topic: $topic, postedAfter: $postedAfter, order: VOTES, first: 20, after: $after) {
    pageInfo { hasNextPage endCursor }
    edges { node { name tagline website url createdAt votesCount makers { name username } } }
  }
}
"""


class ProductHuntScout(BaseScout):
    name = "producthunt"
    config_key = "producthunt"

    def enabled(self) -> tuple[bool, str]:
        if self.ctx.cfg.settings.producthunt_token is None:
            return False, "PRODUCTHUNT_TOKEN not set (betalist runs as the free stand-in)"
        return True, "ok"

    async def discover(self, limit: int) -> list[DiscoveryItem]:
        token = self.ctx.cfg.settings.producthunt_token.get_secret_value()  # type: ignore[union-attr]
        since = (datetime.now(timezone.utc) - timedelta(days=30 * int(self.conf.get("months", 18)))).isoformat()
        out: list[DiscoveryItem] = []
        for topic in self.conf.get("topics", []):
            after = None
            while len(out) < limit:
                body = {"query": QUERY, "variables": {"topic": topic, "after": after, "postedAfter": since}}
                try:
                    data = await self.ctx.fetcher.get_json(
                        self.conf["endpoint"], method="POST", json_body=body, provider="producthunt",
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    )
                except Exception as exc:
                    self.note(f"topic {topic}: {type(exc).__name__}: {str(exc)[:150]}")
                    break
                posts = ((data.get("data") or {}).get("posts")) or {}
                for edge in posts.get("edges", []):
                    node = edge["node"]
                    makers = [m for m in node.get("makers") or [] if m.get("name") and "REDACTED" not in m["name"]]
                    for maker in makers or [None]:
                        out.append(DiscoveryItem(
                            scout=self.name, raw_company_name=node["name"],
                            raw_person_name=maker["name"] if maker else None,
                            company_url=node.get("website"), source_url=node["url"], snippet=node.get("tagline"),
                            extra={"topic": topic, "votes": node.get("votesCount"), "created_at": node.get("createdAt")},
                        ))
                info = posts.get("pageInfo") or {}
                if not info.get("hasNextPage"):
                    break
                after = info.get("endCursor")
        return out
