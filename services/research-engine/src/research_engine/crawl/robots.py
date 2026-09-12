"""robots.txt as RFC 9309 defines it: the most specific rule wins.

`urllib.robotparser` applies the first matching rule, so `Allow: /` followed by `Disallow: /admin`
would let it into /admin. This parser follows the RFC instead: the longest matching pattern decides,
`Allow` wins a tie, `*` and `$` are honoured, a group for our product token replaces the `*` group,
and robots.txt itself is always fetchable.

Fetch outcomes (RFC 9309 section 2.3.1) are decided by the engine: a 4xx means no rules, while a
5xx, a 429 or a network failure means everything is disallowed until robots.txt can be read.
"""

import contextlib
import re
from dataclasses import dataclass, field
from typing import Final

MAX_ROBOTS_BYTES: Final = 500 * 1024
MAX_CRAWL_DELAY_S: Final = 30.0


@dataclass(frozen=True, slots=True)
class Robots:
    rules: tuple[tuple[bool, str], ...] = ()
    sitemaps: tuple[str, ...] = ()
    crawl_delay_s: float | None = None
    _compiled: dict[str, re.Pattern[str]] = field(default_factory=dict, compare=False, repr=False)

    def allowed(self, path_and_query: str) -> bool:
        if path_and_query == "/robots.txt":
            return True
        best: tuple[int, bool] | None = None
        for allow, pattern in self.rules:
            if self._matcher(pattern).match(path_and_query) is None:
                continue
            if best is None or len(pattern) > best[0] or (len(pattern) == best[0] and allow):
                best = (len(pattern), allow)
        return best is None or best[1]

    def _matcher(self, pattern: str) -> re.Pattern[str]:
        compiled = self._compiled.get(pattern)
        if compiled is None:
            anchored = pattern.endswith("$")
            body = re.escape(pattern.removesuffix("$")).replace(r"\*", ".*")
            compiled = re.compile(body + ("$" if anchored else ""))
            self._compiled[pattern] = compiled
        return compiled


ALLOW_ALL: Final = Robots()
DISALLOW_ALL: Final = Robots(rules=((False, "/"),))


def parse(text: str, product_token: str) -> Robots:
    """The rules that apply to `product_token`, plus every sitemap and our crawl delay."""
    token = product_token.lower()
    groups: list[tuple[set[str], list[tuple[bool, str]], list[float]]] = []
    sitemaps: list[str] = []
    in_agents = False
    for raw_line in text[:MAX_ROBOTS_BYTES].splitlines():
        line = raw_line.split("#", 1)[0].strip()
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "sitemap":
            if value:
                sitemaps.append(value)
            continue
        if key == "user-agent":
            if not in_agents:
                groups.append((set(), [], []))
                in_agents = True
            groups[-1][0].add(value.lower())
            continue
        in_agents = False
        if not groups:
            continue  # rules before any user-agent line belong to nobody
        if key in ("allow", "disallow") and value:
            groups[-1][1].append((key == "allow", value if value.startswith("/") else "/" + value))
        elif key == "crawl-delay":
            with contextlib.suppress(ValueError):
                groups[-1][2].append(float(value))
    mine = [group for group in groups if token in group[0]] or [
        group for group in groups if "*" in group[0]
    ]
    delays = [delay for group in mine for delay in group[2] if delay >= 0]
    return Robots(
        rules=tuple(rule for group in mine for rule in group[1]),
        sitemaps=tuple(sitemaps),
        crawl_delay_s=min(delays[0], MAX_CRAWL_DELAY_S) if delays else None,
    )
