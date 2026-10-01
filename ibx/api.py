import os, json, time, urllib.request, urllib.error
BASE = os.environ["SIGMA_BASE_URL"]
_tok = None
def token():
    global _tok
    if _tok: return _tok
    d = ("grant_type=client_credentials&client_id=%s&client_secret=%s" % (os.environ["SIGMA_CLIENT_ID"], os.environ["SIGMA_CLIENT_SECRET"])).encode()
    r = urllib.request.urlopen(urllib.request.Request(BASE + "/v2/auth/token", data=d))
    _tok = json.load(r)["access_token"]; return _tok
def call(method, path, body=None, raw=False):
    h = {"Authorization": "Bearer " + token(), "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode(); h["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        r = urllib.request.urlopen(req, timeout=300)
        t = r.read()
        return (r.status, t) if raw else (r.status, json.loads(t) if t else {})
    except urllib.error.HTTPError as e:
        t = e.read().decode()
        try: return e.code, json.loads(t)
        except Exception: return e.code, t
def export(wb, element, fmt="csv", wait=240):
    s, r = call("POST", "/v2/workbooks/%s/export" % wb, {"elementId": element, "format": {"type": fmt}} if element else {"format": {"type": fmt}})
    if s != 200: return s, r
    qid = r["queryId"]; t0 = time.time()
    while time.time() - t0 < wait:
        s, b = call("GET", "/v2/query/%s/download" % qid, raw=True)
        if s == 200: return 200, b
        time.sleep(3)
    return 408, "timeout"

def shot(wb, outdir, page=None, name="p", wait=300):
    import pathlib
    pathlib.Path(outdir).mkdir(parents=True, exist_ok=True)
    body = {"format": {"type": "png"}}
    if page: body["pageId"] = page
    s, r = call("POST", "/v2/workbooks/%s/export" % wb, body)
    if s != 200: return s, r
    qid = r["queryId"]; t0 = time.time()
    while time.time() - t0 < wait:
        s, b = call("GET", "/v2/query/%s/download" % qid, raw=True)
        if s == 200 and b:
            p = "%s/%s.png" % (outdir, name); open(p, "wb").write(b); return 200, p
        time.sleep(4)
    return 408, "timeout"
