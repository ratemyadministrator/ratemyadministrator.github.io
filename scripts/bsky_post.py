"""Publish the next queued post in social/queue.json to Bluesky.

Run by .github/workflows/bluesky.yml. It needs the secrets BSKY_HANDLE and BSKY_APP_PASSWORD.
Also follows up to 5 handles a run from social/follow.json (results in social/followed.json).
Reposts up to 2 a run from social/repost.json and saves fresh candidates from followed
accounts to social/candidates.json for the daily operator to choose from.
Collects replies/mentions into social/mentions.json and posts answers queued in
social/replies.json (up to 5 a run).
Queue format: a list of {"id": "...", "text": "...", "kind": "...", "not_before": "YYYY-MM-DD" (optional),
"priority": true (optional, skips variety rules), "links": [{"text", "url"}] (optional: makes those
words in the text a link, so long URLs don't count toward the 300 characters)}. Kinds: editorial, solidarity, news, win, spotlight,
know-your-rights, site, question. Up to two posts a day, rotating kinds; solidarity at most once per 3 days.
URLs in the text become clickable links. Each run publishes at most one post. Published posts
move to social/posted.json along with their Bluesky link.
"""
import datetime, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

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
    # Hashtags (e.g. #HigherEd) become tag facets so they are clickable and searchable.
    for m in re.finditer(rb"(?:^|(?<=\s))#([A-Za-z][A-Za-z0-9_]{0,63})", raw):
        facets.append({"index": {"byteStart": m.start(), "byteEnd": m.end()},
                       "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": m.group(1).decode()}]})
    return facets


def get(method, params, token):
    req = urllib.request.Request(API + method + "?" + urllib.parse.urlencode(params),
                                 headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def upload_video(s, path):
    """Upload an MP4 for a native Bluesky video post. Uses Bluesky's video service (which
    processes the video and makes the thumbnail); falls back to a plain blob upload."""
    data = open(path, "rb").read()
    pds = next((x["serviceEndpoint"] for x in (s.get("didDoc") or {}).get("service", []) if x.get("id") == "#atproto_pds"), "https://bsky.social")
    try:
        aud = "did:web:" + urllib.parse.urlparse(pds).hostname
        tok = get("com.atproto.server.getServiceAuth", {"aud": aud, "lxm": "com.atproto.repo.uploadBlob",
                  "exp": int(datetime.datetime.now().timestamp()) + 1800}, s["accessJwt"])["token"]
        q = urllib.parse.urlencode({"did": s["did"], "name": os.path.basename(path)})
        req = urllib.request.Request("https://video.bsky.app/xrpc/app.bsky.video.uploadVideo?" + q, data=data,
                                     headers={"Content-Type": "video/mp4", "Authorization": "Bearer " + tok})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                job = json.load(r)
        except urllib.error.HTTPError as e:  # 409 = this exact file was uploaded before; the body still has the job
            if e.code != 409:
                raise
            job = json.load(e)
        job_id = job.get("jobId") or job.get("jobStatus", {}).get("jobId")
        for _ in range(120):
            st = json.load(urllib.request.urlopen("https://video.bsky.app/xrpc/app.bsky.video.getJobStatus?jobId=" + job_id, timeout=30))["jobStatus"]
            if st.get("blob"):
                return st["blob"]
            if st.get("state") == "JOB_STATE_FAILED":
                raise RuntimeError(st.get("error") or "video processing failed")
            time.sleep(3)
        raise RuntimeError("video processing timed out")
    except Exception as e:
        print("Video service upload failed, using plain blob upload:", e)
        req = urllib.request.Request(API + "com.atproto.repo.uploadBlob", data=data,
                                     headers={"Content-Type": "video/mp4", "Authorization": "Bearer " + s["accessJwt"]})
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)["blob"]


def replace_old(s, url):
    """Delete an earlier post of ours that a new post replaces, only if nobody has engaged with it."""
    rkey = url.rstrip("/").rsplit("/", 1)[1]
    uri = f"at://{s['did']}/app.bsky.feed.post/{rkey}"
    try:
        old = get("app.bsky.feed.getPosts", {"uris": uri}, s["accessJwt"])["posts"]
    except Exception as e:
        print("Couldn't check the old post; leaving it up:", e); return False
    if not old:
        print("Old post not found (already gone)."); return True
    o = old[0]; n = sum(o.get(k, 0) for k in ("likeCount", "repostCount", "replyCount", "quoteCount"))
    if n:
        print(f"Old post has {n} likes/reposts/replies/quotes; leaving it up."); return False
    call("com.atproto.repo.deleteRecord", {"repo": s["did"], "collection": "app.bsky.feed.post", "rkey": rkey}, s["accessJwt"])
    print("Deleted the old post it replaces:", url); return True


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
    new_followers, engagers = [], []
    for n in notes:
        if n["uri"] in seen or n["reason"] not in counts:
            continue
        counts[n["reason"]] += 1
        if n["reason"] == "follow":
            new_followers.append(n["author"]["handle"])
        elif n["reason"] in ("like", "repost"):
            engagers.append(f'{n["reason"]}: {n["author"]["handle"]}')
        seen.add(n["uri"])
    stats["history"].append({"at": datetime.datetime.now(datetime.timezone.utc).isoformat()[:16],
                             "followers": prof.get("followersCount", 0), "following": prof.get("followsCount", 0),
                             "posts": prof.get("postsCount", 0), "new": counts, "new_followers": new_followers,
                             "liked_or_shared_by": engagers})
    stats["history"] = stats["history"][-120:]
    stats["seen"] = list(seen)[-1000:]
    json.dump(stats, open(STATS, "w"), indent=2)
    print("followers", prof.get("followersCount", 0), "new", counts)


GAP_HOURS = 10  # morning and evening runs can each post: up to two posts a day
KIND_CAPS = {"solidarity": 3}  # at most one post of this kind per N days (unless "priority": true)


def pick_order(due, history, now):
    """Order due posts for variety: drop kinds over their cap, then prefer a kind other than the
    last one posted. Within that, keep queue order (oldest not_before first)."""
    live = [h for h in history if not h.get("deleted_at")]
    last_kind = live[-1].get("kind") if live else None
    ok = []
    for p in due:
        days = KIND_CAPS.get(p.get("kind"))
        if days and not p.get("priority"):
            since = (now - datetime.timedelta(days=days)).isoformat()
            if any(h.get("kind") == p.get("kind") and h.get("posted_at", "") > since for h in live):
                continue
        ok.append(p)
    ok.sort(key=lambda p: (bool(last_kind) and p.get("kind") == last_kind and not p.get("priority"), p.get("not_before", "")))
    return ok


def main():
    handle, pw = os.environ.get("BSKY_HANDLE"), os.environ.get("BSKY_APP_PASSWORD")
    if not handle or not pw:
        print("Bluesky secrets not set; nothing to do.")
        return
    s = call("com.atproto.server.createSession", {"identifier": handle, "password": pw})
    if os.environ.get("STATS_ONLY", "").lower() == "true":  # fresh numbers for the daily report; no posting or following
        record_stats(s)
        return
    follow_some(s)
    repost_some(s)
    gather_mentions(s)
    record_stats(s)
    send_replies(s)
    gather_candidates(s)
    queue = json.load(open(QUEUE)) if os.path.exists(QUEUE) else []
    today = datetime.date.today().isoformat()
    post_id = os.environ.get("POST_ID", "").strip()
    if post_id:  # manual run: publish this queued post now, skipping the date and once-a-day checks
        due = [p for p in queue if p.get("id") == post_id]
        if not due:
            sys.exit(f"No queued post with id {post_id}.")
    else:
        due = [p for p in queue if p.get("not_before", "") <= today]
        if not due:
            print("No post due today.")
            return
        now = datetime.datetime.now(datetime.timezone.utc)
        history = load(POSTED)
        recent = (now - datetime.timedelta(hours=GAP_HOURS)).isoformat()
        if any(p.get("posted_at", "") > recent for p in history):
            print(f"Posted within the last {GAP_HOURS} hours; next post waits.")
            return
        due = pick_order(due, history, now)
        if not due:
            print("Only capped kinds are due right now; waiting.")
            return
    post = due[0]
    text = post["text"].strip()
    if len(text) > 300:
        sys.exit(f"Post {post['id']} is {len(text)} characters; Bluesky allows 300.")
    record = {"$type": "app.bsky.feed.post", "text": text, "langs": ["en"],
              "createdAt": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")}
    facets = link_facets(text)
    raw = text.encode()
    for ln in post.get("links", []):  # {"text": words in the post, "url": where they link}: keeps long URLs out of the 300 characters
        i = raw.find(ln["text"].encode())
        if i >= 0:
            facets.append({"index": {"byteStart": i, "byteEnd": i + len(ln["text"].encode())},
                           "features": [{"$type": "app.bsky.richtext.facet#link", "uri": ln["url"]}]})
    if facets:
        record["facets"] = facets
    if post.get("video"):  # native video: plays in the feed with a thumbnail; the link in the text goes to the live page
        w, h = post.get("aspect", [1920, 1080])
        record["embed"] = {"$type": "app.bsky.embed.video", "video": upload_video(s, post["video"]),
                           "alt": post.get("alt", "")[:1000], "aspectRatio": {"width": w, "height": h}}
    r = call("com.atproto.repo.createRecord",
             {"repo": s["did"], "collection": "app.bsky.feed.post", "record": record}, s["accessJwt"])
    rkey = r["uri"].rsplit("/", 1)[1]
    url = f"https://bsky.app/profile/{s['handle']}/post/{rkey}"
    queue.remove(post)
    posted = json.load(open(POSTED)) if os.path.exists(POSTED) else []
    if post.get("replaces"):  # new post goes up first, then the unengaged old one comes down
        if replace_old(s, post["replaces"]):
            for p in posted:
                if p.get("url") == post["replaces"]:
                    p["deleted_at"] = record["createdAt"]; p["replaced_by"] = url
    posted.append({**post, "posted_at": record["createdAt"], "url": url})
    json.dump(queue, open(QUEUE, "w"), indent=2)
    json.dump(posted, open(POSTED, "w"), indent=2)
    print("Posted:", url)


if __name__ == "__main__":
    main()
