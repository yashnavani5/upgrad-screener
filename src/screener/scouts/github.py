"""GitHub repository search (education topics/keywords, stars > N, pushed in the last 12 months).
The owner (org or user) is the candidate; stars and last push are recorded."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from screener.scouts.base import BaseScout, DiscoveryItem


class GithubScout(BaseScout):
    name = "github"
    config_key = "github"

    async def discover(self, limit: int) -> list[DiscoveryItem]:
        token = self.ctx.cfg.settings.github_token
        rpm = 28 if token else float(self.conf.get("rpm", 9))
        self.ctx.fetcher.limiter.configure("provider:github", rpm)
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token.get_secret_value()}"
        since = (datetime.now(timezone.utc) - timedelta(days=30 * int(self.conf.get("pushed_within_months", 12)))).date()
        min_stars = int(self.conf.get("min_stars", 50))
        best: dict[str, dict] = {}
        for q in self.conf.get("queries", []):
            params = {"q": f"{q} stars:>{min_stars} pushed:>={since.isoformat()}", "sort": "stars",
                      "order": "desc", "per_page": min(100, max(limit, 30))}
            try:
                data = await self.ctx.fetcher.get_json(self.conf["endpoint"], params=params, headers=headers, provider="github")
            except Exception as exc:
                self.note(f"'{q}': {type(exc).__name__}: {str(exc)[:150]}")
                continue
            for repo in data.get("items", []):
                owner = repo.get("owner") or {}
                login = owner.get("login")
                if login and repo.get("stargazers_count", 0) > best.get(login, {}).get("stargazers_count", -1):
                    best[login] = repo
        repos = sorted(best.values(), key=lambda r: r.get("stargazers_count", 0), reverse=True)
        self.note(f"{len(repos)} distinct owners")
        items = []
        for repo in repos[:limit]:
            owner = repo["owner"]
            is_user = owner.get("type") == "User"
            items.append(DiscoveryItem(
                scout=self.name, raw_company_name=owner["login"],
                raw_person_name=owner["login"] if is_user else None,
                company_url=repo.get("homepage") or (None if is_user else owner.get("html_url")),
                person_url=owner.get("html_url") if is_user else None,
                source_url=repo["html_url"], snippet=repo.get("description"),
                extra={"repo": repo.get("full_name"), "stars": repo.get("stargazers_count"),
                       "pushed_at": repo.get("pushed_at"), "topics": repo.get("topics", []),
                       "owner_type": owner.get("type")},
            ))
        return items
