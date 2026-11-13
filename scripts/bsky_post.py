"""Publish the next queued post in social/queue.json to Bluesky.

Run by .github/workflows/bluesky.yml. It needs the secrets BSKY_HANDLE and BSKY_APP_PASSWORD.
Also follows up to 5 handles a run from social/follow.json (results in social/followed.json).
Reposts up to 2 a run from social/repost.json and saves fresh candidates from followed
accounts to social/candidates.json for the daily operator to choose from.
Collects replies/mentions into social/mentions.json and posts answers queued in
social/replies.json (up to 5 a run).
Queue format: a list of {"id": "...", "text": "...", "not_before": "YYYY-MM-DD" (optional)}.
URLs in the text become clickable links. Each run publishes at most one post. Published posts
move to social/posted.json along with their Bluesky link.
"""
import datetime, json, os, re, sys, urllib.parse, urllib.request

API = "https://bsky.social/xrpc/"
QUEUE, POSTED = "social/queue.json", "social/posted.json"
FOLLOW, FOLLOWED = "social/follow.json", "social/followed.json"
FOLLOWS_PER_RUN = 5
CANDIDATES, REPOST, REPOSTED = "social/candidates.json", "social/repost.json", "social/reposted.json"
REPOSTS_PER_RUN = 2
MENTIONS, REPLIES, REPLIED = "social/mentions.json", "social/replies.json", "social/replied.json"
REPLIES_PER_RUN = 5
STATS = "social/stats.json"


def call(method, body, token=None):
    req = urllib.request.Request(API + method, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def link_facets(text):
    facets, raw = [], text.encode()
    for m in re.finditer(rb"https?://[^\s)]+[^\s).,!?]", raw):
        facets.append({"index": {"byteStart": m.start(), "byteEnd": m.end()},
                       "features": [{"$type": "app.bsky.richtext.facet#link", "uri": m.group().decode()}]})
    return facets


def get(method, params, token):
    req = urllib.request.Request(API + method + "?" + urllib.parse.urlencode(params),
                                 headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def follow_some(s):
    """Follow up to FOLLOWS_PER_RUN handles from social/follow.json (a list of handles)."""
    todo = json.load(open(FOLLOW)) if os.path.exists(FOLLOW) else []
    done = json.load(open(FOLLOWED)) if os.path.exists(FOLLOWED) else []
    for handle in todo[:FOLLOWS_PER_RUN]:
        entry = {"handle": handle, "at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        try:
            did = get("com.atproto.identity.resolveHandle", {"handle": handle}, s["accessJwt"])["did"]
            call("com.atproto.repo.createRecord", {"repo": s["did"], "collection": "app.bsky.graph.follow",
                 "record": {"$type": "app.bsky.graph.follow", "subject": did, "createdAt": entry["at"]}}, s["accessJwt"])
            entry["result"] = "followed"
        except Exception as e:  # handle gone or renamed: record and move on
            entry["result"] = f"skipped: {e}"
        print(handle, entry["result"])
        done.append(entry)
        todo.remove(handle)
    json.dump(todo, open(FOLLOW, "w"), indent=2)
    json.dump(done, open(FOLLOWED, "w"), indent=2)


def load(path):
    return json.load(open(path)) if os.path.exists(path) else []


def gather_candidates(s):
    """Save recent original posts (no replies or reposts, last 4 days) from followed accounts,
    so the daily operator can choose which ones to share."""
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=4)).isoformat()
    shared = {r["uri"] for r in load(REPOSTED)}
    out = []
    for f in load(FOLLOWED):
        if f.get("result") != "followed":
            continue
        try:
            feed = get("app.bsky.feed.getAuthorFeed",
                       {"actor": f["handle"], "limit": 15, "filter": "posts_no_replies"}, s["accessJwt"])["feed"]
        except Exception as e:
            print("feed failed", f["handle"], e)
            continue
        for item in feed:
            post = item["post"]
            if item.get("reason") or post["uri"] in shared:
                continue  # skip their reposts and anything already shared
            created = post["record"].get("createdAt", "")
            if created < since:
                continue
            out.append({"uri": post["uri"], "cid": post["cid"], "author": post["author"]["handle"],
                        "created": created, "text": post["record"].get("text", "")[:500],
                        "likes": post.get("likeCount", 0), "reposts": post.get("repostCount", 0),
                        "url": f"https://bsky.app/profile/{post['author']['handle']}/post/{post['uri'].rsplit('/', 1)[1]}"})
    out.sort(key=lambda c: c["created"], reverse=True)
    json.dump(out, open(CANDIDATES, "w"), indent=2)
    print(len(out), "repost candidates saved")


def repost_some(s):
    """Repost up to REPOSTS_PER_RUN items from social/repost.json (each {"uri", "cid"}, as in candidates.json)."""
    todo, done = load(REPOST), load(REPOSTED)
    shared = {r["uri"] for r in done}
    for item in todo[:REPOSTS_PER_RUN]:
        todo.remove(item)
        if item["uri"] in shared:
            continue
        now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            call("com.atproto.repo.createRecord", {"repo": s["did"], "collection": "app.bsky.feed.repost",
                 "record": {"$type": "app.bsky.feed.repost", "subject": {"uri": item["uri"], "cid": item["cid"]},
                            "createdAt": now}}, s["accessJwt"])
            result = "reposted"
        except Exception as e:
            result = f"failed: {e}"
        print(item["uri"], result)
        done.append({**item, "at": now, "result": result})
    json.dump(todo, open(REPOST, "w"), indent=2)
    json.dump(done, open(REPOSTED, "w"), indent=2)


def gather_mentions(s):
    """Append new replies, mentions and quote-posts of our account to social/mentions.json
    (newest last, last 300 kept) for the daily operator to answer."""
    seen = load(MENTIONS)
    known = {m["uri"] for m in seen}
    try:
        notes = get("app.bsky.notification.listNotifications", {"limit": 50}, s["accessJwt"])["notifications"]
    except Exception as e:
        print("notifications failed", e)
        return
    new = []
    for n in notes:
        if n["reason"] not in ("reply", "mention", "quote") or n["uri"] in known:
            continue
        rec = n.get("record", {})
        root = (rec.get("reply") or {}).get("root") or {"uri": n["uri"], "cid": n["cid"]}
        new.append({"uri": n["uri"], "cid": n["cid"], "reason": n["reason"], "author": n["author"]["handle"],
                    "text": rec.get("text", "")[:1000], "at": n.get("indexedAt", ""),
                    "root_uri": root["uri"], "root_cid": root["cid"], "answered": False,
                    "url": f"https://bsky.app/profile/{n['author']['handle']}/post/{n['uri'].rsplit('/', 1)[1]}"})
    new.sort(key=lambda m: m["at"])
    json.dump((seen + new)[-300:], open(MENTIONS, "w"), indent=2)
    print(len(new), "new replies/mentions")


def send_replies(s):
    """Post up to REPLIES_PER_RUN replies from social/replies.json:
    {"to_uri", "to_cid", "root_uri", "root_cid", "text"} (copy uri/cid/root_* from mentions.json)."""
    todo, done = load(REPLIES), load(REPLIED)
    for item in todo[:REPLIES_PER_RUN]:
        todo.remove(item)
        text = item["text"].strip()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        if len(text) > 300:
            done.append({**item, "at": now, "result": f"skipped: {len(text)} characters"})
            continue
        record = {"$type": "app.bsky.feed.post", "text": text, "langs": ["en"], "createdAt": now,
                  "reply": {"root": {"uri": item["root_uri"], "cid": item["root_cid"]},
                            "parent": {"uri": item["to_uri"], "cid": item["to_cid"]}}}
        facets = link_facets(text)
        if facets:
            record["facets"] = facets
        try:
            call("com.atproto.repo.createRecord", {"repo": s["did"], "collection": "app.bsky.feed.post", "record": record}, s["accessJwt"])
            result = "replied"
        except Exception as e:
            result = f"failed: {e}"
        print("reply", item["to_uri"], result)
        done.append({**item, "at": now, "result": result})
    mentions = load(MENTIONS)
    answered = {d["to_uri"] for d in done if d.get("result") == "replied"}
    for m in mentions:
        if m["uri"] in answered:
            m["answered"] = True
    json.dump(mentions, open(MENTIONS, "w"), indent=2)
    json.dump(todo, open(REPLIES, "w"), indent=2)
    json.dump(done, open(REPLIED, "w"), indent=2)


def record_stats(s):
    """Keep running totals for the daily report: followers, and new follows, reposts, likes and
    quotes of our posts since the previous run. Stores only counts and handles of new followers
    (public information), never message content."""
    stats = json.load(open(STATS)) if os.path.exists(STATS) else {"history": [], "seen": []}
    try:
        prof = get("app.bsky.actor.getProfile", {"actor": s["did"]}, s["accessJwt"])
        notes = get("app.bsky.notification.listNotifications", {"limit": 100}, s["accessJwt"])["notifications"]
    except Exception as e:
        print("stats failed", e)
        return
    seen = set(stats.get("seen", []))
    counts = {"follow": 0, "repost": 0, "like": 0, "quote": 0, "reply": 0, "mention": 0}
    new_followers = []
    for n in notes:
        if n["uri"] in seen or n["reason"] not in counts:
            continue
        counts[n["reason"]] += 1
        if n["reason"] == "follow":
            new_followers.append(n["author"]["handle"])
        seen.add(n["uri"])
    stats["history"].append({"at": datetime.datetime.now(datetime.timezone.utc).isoformat()[:16],
                             "followers": prof.get("followersCount", 0), "following": prof.get("followsCount", 0),
                             "posts": prof.get("postsCount", 0), "new": counts, "new_followers": new_followers})
    stats["history"] = stats["history"][-120:]
    stats["seen"] = list(seen)[-1000:]
    json.dump(stats, open(STATS, "w"), indent=2)
    print("followers", prof.get("followersCount", 0), "new", counts)


def main():
    handle, pw = os.environ.get("BSKY_HANDLE"), os.environ.get("BSKY_APP_PASSWORD")
    if not handle or not pw:
        print("Bluesky secrets not set; nothing to do.")
        return
    s = call("com.atproto.server.createSession", {"identifier": handle, "password": pw})
    follow_some(s)
    repost_some(s)
    gather_mentions(s)
    record_stats(s)
    send_replies(s)
    gather_candidates(s)
    queue = json.load(open(QUEUE)) if os.path.exists(QUEUE) else []
    today = datetime.date.today().isoformat()
    due = [p for p in queue if p.get("not_before", "") <= today]
    if not due:
        print("No post due today.")
        return
    recent = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=20)).isoformat()
    if any(p.get("posted_at", "") > recent for p in load(POSTED)):
        print("Already posted in the last 20 hours; next post waits.")
        return
    post = due[0]
    text = post["text"].strip()
    if len(text) > 300:
        sys.exit(f"Post {post['id']} is {len(text)} characters; Bluesky allows 300.")
    record = {"$type": "app.bsky.feed.post", "text": text, "langs": ["en"],
              "createdAt": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")}
    facets = link_facets(text)
    if facets:
        record["facets"] = facets
    r = call("com.atproto.repo.createRecord",
             {"repo": s["did"], "collection": "app.bsky.feed.post", "record": record}, s["accessJwt"])
    rkey = r["uri"].rsplit("/", 1)[1]
    url = f"https://bsky.app/profile/{s['handle']}/post/{rkey}"
    queue.remove(post)
    posted = json.load(open(POSTED)) if os.path.exists(POSTED) else []
    posted.append({**post, "posted_at": record["createdAt"], "url": url})
    json.dump(queue, open(QUEUE, "w"), indent=2)
    json.dump(posted, open(POSTED, "w"), indent=2)
    print("Posted:", url)


if __name__ == "__main__":
    main()
