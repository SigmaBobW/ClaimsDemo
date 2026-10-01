import json, sys, copy, api
body = json.load(open('spec.json'))
def try_(mod, label):
    b = copy.deepcopy(body); mod(b)
    s, r = api.call("POST", "/v2/workbooks/spec/verify", b)
    print(label, s, (r.get("message") if isinstance(r, dict) else r)[:200] if s != 200 else "OK")
def el(b, i): return next(e for e in b["document"]["elements"] if e["id"] == i)
