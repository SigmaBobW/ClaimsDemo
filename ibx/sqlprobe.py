import json, api, sys, csv, io
CONN="9e79f38b-a310-405c-aad9-72f762ac6ff1"
FOLDER="81de80ef-c360-4ee3-9aba-2f2dc31888ce"
def run(sql, cols):
    spec={"name":"zz sqlprobe","folderId":FOLDER,"document":{"schemaVersion":1,"kind":"workbook",
     "elements":[{"id":"t","kind":"table","name":"T","source":{"kind":"sql","connectionId":CONN,"statement":sql},
       "columns":[{"id":"c%d"%i,"name":c,"formula":"[Custom SQL/%s]"%c} for i,c in enumerate(cols)]}],
     "pages":[{"id":"p","name":"p"}],
     "layout":'<?xml version="1.0" encoding="utf-8"?><Page type="grid" gridTemplateColumns="repeat(24, 1fr)" gridTemplateRows="auto" id="p"><Element elementId="t" gridColumn="1 / 25" gridRow="1 / 20"/></Page>'}}
    s,r=api.call("POST","/v2/workbooks/spec",spec)
    if s!=200: return s,r
    wb=r["workbookId"]
    s,b=api.export(wb,"t")
    api.call("DELETE","/v2/files/"+wb)
    return s,b
if __name__=="__main__":
    sql=sys.argv[1]; cols=sys.argv[2].split(',')
    s,b=run(sql,cols)
    print(s); print(b.decode() if s==200 else b)
