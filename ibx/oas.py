import json,sys
d=json.load(open('/tmp/claude-0/openapi.json'))
S=d['components']['schemas']
def find(o,pred,path=''):
    if isinstance(o,dict):
        if pred(o): yield o
        for k,v in o.items(): yield from find(v,pred,path+'/'+k)
    elif isinstance(o,list):
        for i,v in enumerate(o): yield from find(v,pred,path+'[%d]'%i)
def resolve(o):
    if isinstance(o,dict) and '$ref' in o:
        n=o['$ref'].split('/')[-1]; return resolve(S[n])
    return o
def compact(o,depth=0,maxd=4):
    o=resolve(o)
    if not isinstance(o,dict): return str(o)
    if 'allOf' in o:
        out={}
        for x in o['allOf']:
            r=compact(x,depth,maxd)
            if isinstance(r,dict): out.update(r)
            else: return r
        return out
    if 'oneOf' in o:
        return ['|'.join(map(lambda x: json.dumps(compact(x,depth+1,maxd)) if depth<maxd else '..', o['oneOf']))] if depth<maxd else 'oneOf..'
    t=o.get('type')
    if 'enum' in o: return 'enum'+json.dumps(o['enum'])
    if t=='object' or 'properties' in o:
        if depth>=maxd: return 'obj'
        req=set(o.get('required',[]))
        return {k+('*' if k in req else ''):compact(v,depth+1,maxd) for k,v in o.get('properties',{}).items()}
    if t=='array': return [compact(o.get('items',{}),depth+1,maxd)]
    return t or '?'
if __name__=='__main__':
    kind=sys.argv[1]; maxd=int(sys.argv[2]) if len(sys.argv)>2 else 3
    for o in find(S,lambda o:o.get('properties',{}).get('kind',{}).get('enum')==[kind]):
        p=dict(o['properties']); p.pop('source',None)
        print(json.dumps(compact({'type':'object','properties':p,'required':o.get('required',[])},0,maxd),indent=1)[:6000]); break

def element(kind,maxd=3):
    for o in find(S,lambda o: 'allOf' in o and any(isinstance(x,dict) and x.get('properties',{}).get('kind',{}).get('enum')==[kind] for x in o['allOf'])):
        parts=[]
        for x in o['allOf']:
            x=dict(x)
            if 'properties' in x:
                p=dict(x['properties']); p.pop('source',None); x['properties']=p
            parts.append(compact(x,0,maxd))
        return parts
