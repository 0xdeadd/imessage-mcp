import json, sys, urllib.request
URL="http://127.0.0.1:8471/mcp"
def post(body, sid=None):
    h={'content-type':'application/json','accept':'application/json, text/event-stream'}
    if sid: h['mcp-session-id']=sid
    req=urllib.request.Request(URL,data=json.dumps(body).encode(),headers=h,method='POST')
    try:
        with urllib.request.urlopen(req,timeout=20) as r:
            raw=r.read().decode(); return r.headers.get('mcp-session-id'), raw
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode()[:300]}"
def parse(raw):
    out=[]
    for line in raw.splitlines():
        if line.startswith('data:'):
            try: out.append(json.loads(line[5:].strip()))
            except Exception: pass
    if not out:
        try: out.append(json.loads(raw))
        except Exception: pass
    return out
sid,raw=post({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"halo-probe","version":"0"}}})
post({"jsonrpc":"2.0","method":"notifications/initialized"},sid)
tool=sys.argv[1]; args=json.loads(sys.argv[2])
_,raw=post({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":tool,"arguments":args}},sid)
for m in parse(raw):
    r=m.get('result') or m.get('error')
    if isinstance(r,dict) and 'content' in r:
        for c in r['content']: print(c.get('text',''))
    else: print(json.dumps(r)[:800])
