import sys, api
wb = open('workbook_id.txt').read().strip()
for pg in sys.argv[1:]:
    s, r = api.shot(wb, 'shots', page=pg, name=pg, wait=420)
    print(pg, s, r)
