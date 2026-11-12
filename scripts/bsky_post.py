"""Publish the next queued post in social/queue.json to Bluesky.

Run by .github/workflows/bluesky.yml. It needs the secrets BSKY_HANDLE and BSKY_APP_PASSWORD.
Queue format: a list of {"id": "...", "text": "...", "not_before": "YYYY-MM-DD" (optional)}.
URLs in the text become clickable links. Each run publishes at most one post. Published posts
move to social/posted.json along with their Bluesky link.
"""
import datetime, json, os, re, sys, urllib.request

API = "https://bsky.social/xrpc/"
QUEUE, POSTED = "social/queue.json", "social/posted.json"


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


def main():
    handle, pw = os.environ.get("BSKY_HANDLE"), os.environ.get("BSKY_APP_PASSWORD")
    if not handle or not pw:
        print("Bluesky secrets not set; nothing to do.")
        return
    queue = json.load(open(QUEUE)) if os.path.exists(QUEUE) else []
    today = datetime.date.today().isoformat()
    due = [p for p in queue if p.get("not_before", "") <= today]
    if not due:
        print("Queue empty.")
        return
    post = due[0]
    text = post["text"].strip()
    if len(text) > 300:
        sys.exit(f"Post {post['id']} is {len(text)} characters; Bluesky allows 300.")
    s = call("com.atproto.server.createSession", {"identifier": handle, "password": pw})
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
