"""Collect exact follower counts from ordinary public LinkedIn HTML.

Runs without a browser, cookies, credentials, proxies or CAPTCHA services.
An access denial stops that request; no evasion or login workaround is used.
"""
from datetime import datetime, timezone
from html.parser import HTMLParser
import argparse
import html
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

COMPANIES = {
    "databricks": ("Databricks", "https://www.linkedin.com/company/databricks/"),
    "snowflake": ("Snowflake", "https://www.linkedin.com/company/snowflake-computing/"),
}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def exact_integer(value):
    if isinstance(value, bool):
        raise ValueError("Boolean follower count")
    if isinstance(value, int):
        count = value
    elif isinstance(value, str) and re.fullmatch(r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)", value):
        count = int(value.replace(",", ""))
    else:
        raise ValueError("Not an exact follower count")
    if not 0 < count < 2**53:
        raise ValueError("Invalid follower count")
    return count


class PublicPage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.descriptions = []
        self.schemas = []
        self._in_schema = False
        self._script = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and (attrs.get("name", "").lower() == "description" or attrs.get("property", "").lower() == "og:description"):
            self.descriptions.append(attrs.get("content", ""))
        if tag == "script":
            self._in_schema = attrs.get("type", "").lower() == "application/ld+json"
            self._script = []

    def handle_data(self, text):
        if self._in_schema:
            self._script.append(text)

    def handle_endtag(self, tag):
        if tag == "script" and self._in_schema:
            try:
                self.schemas.append(json.loads("".join(self._script)))
            except ValueError:
                pass
            self._in_schema = False


def schema_nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from schema_nodes(item)
    elif isinstance(value, dict):
        yield value
        if "@graph" in value:
            yield from schema_nodes(value["@graph"])


def parse_count(markup, company):
    name, url = COMPANIES[company]
    page = PublicPage()
    page.feed(markup)
    candidates = []
    for schema in page.schemas:
        for node in schema_nodes(schema):
            types = node.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if not any(t in types for t in ("Organization", "Corporation")) or str(node.get("name", "")).strip().casefold() != name.casefold():
                continue
            for key in ("url", "@id"):
                value = node.get(key)
                if isinstance(value, str) and "linkedin.com/company/" in value and value.rstrip("/") != url.rstrip("/"):
                    raise ValueError("Company URL mismatch")
            statistics = node.get("interactionStatistic", [])
            if isinstance(statistics, dict):
                statistics = [statistics]
            for stat in statistics:
                action = stat.get("interactionType", "")
                if isinstance(action, dict):
                    action = action.get("@type", action.get("@id", ""))
                if str(action).rstrip("/").split("/")[-1] == "FollowAction":
                    candidates.append(exact_integer(stat.get("userInteractionCount")))
    for description in page.descriptions:
        # Bind the count to the target company's own description, rather than
        # matching arbitrary sidebar, job, employee or post engagement numbers.
        pattern = rf"^\s*{re.escape(name)}\s*\|\s*([0-9][0-9,]*)\s+followers\b"
        match = re.search(pattern, html.unescape(description), re.IGNORECASE)
        if match:
            candidates.append(exact_integer(match.group(1)))
    if not candidates:
        raise ValueError("Exact own-company follower count absent from public HTML")
    if len(set(candidates)) != 1:
        raise ValueError("Conflicting counts in the same public page")
    return candidates[0]


def fetch_company(company):
    _, url = COMPANIES[company]
    request = urllib.request.Request(url, headers={
        "User-Agent": "FollowerRace/1.0 (+https://github.com/Hyunsoo-Ryan-Lee/linkedin-follower-race)",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            final_url = response.geturl()
            if "/authwall" in final_url or "/login" in final_url or "/checkpoint" in final_url:
                return {"state": "blocked", "httpStatus": response.status, "message": "LinkedIn requires login", "sourceUrl": url}
            if not final_url.startswith(url):
                return {"state": "error", "httpStatus": response.status, "message": "Unexpected company redirect", "sourceUrl": url}
            markup = response.read(5_000_001)
            if len(markup) > 5_000_000:
                raise ValueError("Public page exceeds size limit")
            count = parse_count(markup.decode("utf-8", errors="replace"), company)
            return {"state": "ok", "httpStatus": response.status, "count": count, "sourceUrl": url}
    except urllib.error.HTTPError as error:
        return {"state": "blocked" if error.code in (401, 403, 429, 999) else "error", "httpStatus": error.code, "message": f"LinkedIn returned HTTP {error.code}", "sourceUrl": url}
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        return {"state": "error", "message": str(error)[:240], "sourceUrl": url}


def collect(data, fetch=fetch_company):
    request_id = os.environ.get("DASHBOARD_REQUEST_ID", "").strip().lower()
    if request_id and not re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", request_id):
        raise ValueError("Invalid dashboard request identifier")
    previous_collection = data.get("collection", {})
    started = now()
    results = {company: fetch(company) for company in COMPANIES}
    complete = all(result["state"] == "ok" for result in results.values())
    state = "ok" if complete else "blocked" if any(r["state"] == "blocked" for r in results.values()) else "error"
    completed = now()
    event = os.environ.get("GITHUB_EVENT_NAME", "local")
    last_scheduled_attempt = started if event == "schedule" else previous_collection.get("lastScheduledAttemptAt")
    last_scheduled_success = completed if event == "schedule" and complete else previous_collection.get("lastScheduledSuccessAt")
    if complete:
        data["observations"].append({"observedAt": completed, **{company: exact_integer(result["count"]) for company, result in results.items()}, **({"requestId": request_id} if request_id else {})})
        data["observations"] = data["observations"][-25920:]
    data["collection"] = {
        "state": state,
        "source": "github-actions-public-html",
        "event": event,
        "requestId": request_id or None,
        "lastScheduledAttemptAt": last_scheduled_attempt,
        "lastScheduledSuccessAt": last_scheduled_success,
        "runUrl": f"https://github.com/Hyunsoo-Ryan-Lee/linkedin-follower-race/actions/runs/{os.environ['GITHUB_RUN_ID']}" if os.environ.get("GITHUB_RUN_ID", "").isdigit() else None,
        "attemptedAt": started,
        "completedAt": completed,
        "lastSuccessAt": data["observations"][-1]["observedAt"] if data["observations"] else None,
        "companies": results,
    }
    if request_id:
        requests = [r for r in data.get("requests", []) if r.get("id") != request_id]
        requests.append({"id": request_id, "state": state, "completedAt": completed, "observedAt": completed if complete else None, "runUrl": data["collection"]["runUrl"]})
        data["requests"] = requests[-50:]
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("data.json"))
    args = parser.parse_args()
    data = json.loads(args.data.read_text())
    if data.get("schemaVersion") != 1 or not isinstance(data.get("observations"), list):
        parser.error("Invalid existing observation history")
    updated = collect(data)
    temporary = args.data.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(args.data)
    status = updated["collection"]
    print(json.dumps(status, ensure_ascii=False, indent=2))
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        lines = ["## LinkedIn public-page collection", "", f"Result: **{status['state']}**", f"Trigger: **{status['event']}**", f"Last successful scheduled collection: **{status['lastScheduledSuccessAt'] or 'Not verified yet'}**", "", "| Company | Result | HTTP | Exact followers |", "|---|---|---|---|"]
        for company, result in status["companies"].items():
            lines.append(f"| {company} | {result['state']} | {result.get('httpStatus', '—')} | {result.get('count', '—')} |")
        if status["state"] != "ok":
            lines += ["", "No new paired observation was written. Previous verified observations are retained. A successful workflow/deployment does not imply a successful LinkedIn collection."]
        with open(summary_path, "a") as summary:
            summary.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
