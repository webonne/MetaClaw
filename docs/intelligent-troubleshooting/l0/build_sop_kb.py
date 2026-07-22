import zipfile, re, json
import xml.etree.ElementTree as ET
from collections import defaultdict

NS='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'; ns={'m':NS[1:-1]}
z=zipfile.ZipFile('f.xlsx')
ss=[]
for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si',ns):
    ss.append(''.join(t.text or '' for t in si.iter(NS+'t')))
def colrow(ref):
    m=re.match(r'([A-Z]+)(\d+)',ref); return m.group(1),int(m.group(2))
def load(i):
    sh=ET.fromstring(z.read(f'xl/worksheets/sheet{i}.xml')); rows={}
    for c in sh.iter(NS+'c'):
        ref=c.get('r'); t=c.get('t'); v=c.find('m:v',ns); val=''
        if v is not None:
            val=v.text
            if t=='s': val=ss[int(val)]
        col,r=colrow(ref); rows.setdefault(r,{})[col]=(val or '').strip()
    return rows

JWT=re.compile(r'eyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}')
SIMPLE=re.compile(r'(simpleToken[\'"]?\s*[:=]\s*[\'"]?)[0-9a-f]{16,}')
def mask(s):
    if not s: return s
    s=JWT.sub('<BEARER_TOKEN>', s)
    s=SIMPLE.sub(r'\1<TOKEN>', s)
    return s

CONTACT=['联系','电话','紧急联系','@','通知']
WRITE=['重启','回滚','部署','重新启动','切换','扩容','重推','重新触发','触发','同步','放通','删除','drop','delete','restart','kill','关闭']
READ=['检查','查看','查询','确认','db.','find','status','df ','free ','getindexes','profile','观察']
def classify(step):
    s=step.lower()
    if any(k in step for k in CONTACT): return 'human_contact'
    if any(k in step for k in WRITE) or 'systemctl restart' in s or 'curl' in s: return 'manual_write'
    if any(k in step for k in READ): return 'auto_readonly'
    return 'manual_unknown'
def split_steps(n):
    if not n: return []
    n=mask(n)
    parts=re.split(r'\s*\d+\s*[、/）\)]\s*|\n{1,}', n)
    parts=[p.strip(' \n\t') for p in parts if p and p.strip(' \n\t')]
    return parts if parts else [n.strip()]

def ffill(rows, cols, start):
    last={c:'' for c in cols}; out={}
    for r in sorted(rows):
        if r<start: continue
        cells=rows[r]
        for c in cols:
            if cells.get(c): last[c]=cells[c]
        out[r]={**cells}
        for c in cols:
            if not out[r].get(c): out[r][c]=last[c]
    return out

def build(sheet, cfg, start):
    rows=load(sheet); ff=ffill(rows, cfg['ffill'], start)
    ent={}
    for r in sorted(ff):
        cells=ff[r]
        code=cells.get(cfg['code'],'').strip()
        cause=cells.get(cfg['cause'],'').strip()
        rec=cells.get(cfg['rec'],'').strip()
        # skip completely empty scenario rows
        if not any([code, cause, rec]): continue
        if not code: code='UNCODED@%s#%d'%(cfg.get('svc_default','?'),r)
        e=ent.get(code)
        if not e:
            e={'system':'CSDP','service':cells.get('A','') or cfg.get('svc_default',''),
               'module':cells.get('B',''),'function':cells.get('C',''),
               'scenario':cells.get(cfg['scenario'],''),'error_code':code,
               'level':cells.get(cfg['level'],''),'type':cells.get(cfg.get('type',''),'') if cfg.get('type') else '',
               'causes':[], 'recovery_steps':[], 'log_signature':'',
               'evidence_dql':[], 'anomaly_criteria':None, 'owner_team':None,
               'origin':'seed','status':'candidate'}
            ent[code]=e
        if cause and cause not in e['causes']: e['causes'].append(cause)
        for stp in split_steps(rec):
            if stp and stp not in [x['text'] for x in e['recovery_steps']]:
                e['recovery_steps'].append({'text':stp,'action_type':classify(stp)})
        log=cells.get(cfg.get('log',''),'') if cfg.get('log') else ''
        if log and not e['log_signature']: e['log_signature']=mask(log)[:600]
    return ent

# Sheet1: A svc B mod C func D scenario E level ... K type L cause M code N rec O loc P log
s1=build(1, {'ffill':['A','B','C'],'code':'M','cause':'L','rec':'N','scenario':'D','level':'E','type':'K','log':'P','svc_default':'csdp'}, 3)
# Sheet2: A svc B mod C func ... F scenario E level ... M code L cause N rec
s2=build(2, {'ffill':['A','B','C'],'code':'M','cause':'L','rec':'N','scenario':'F','level':'E','svc_default':'csdp'}, 2)

# merge (sheet1 richer wins for meta; union causes/recovery)
kb={}
for src in (s2, s1):
    for code,e in src.items():
        if code in kb:
            k=kb[code]
            for c in e['causes']:
                if c not in k['causes']: k['causes'].append(c)
            for st in e['recovery_steps']:
                if st['text'] not in [x['text'] for x in k['recovery_steps']]: k['recovery_steps'].append(st)
            for f in ('scenario','level','type','log_signature'):
                if not k.get(f) and e.get(f): k[f]=e[f]
        else:
            kb[code]=e

# completeness + automatable
def finalize(e):
    steps=e['recovery_steps']; ats=[s['action_type'] for s in steps]
    has_rec=len(steps)>0
    real_code = not e['error_code'].startswith('UNCODED')
    contact_only = has_rec and all(a=='human_contact' for a in ats)
    has_exec = any(a in ('manual_write','auto_readonly') for a in ats)
    e['completeness']={
        'has_code':real_code,'has_recovery':has_rec,
        'has_evidence_dql':False,'has_anomaly_criteria':False,
        'contact_only':contact_only,
        'automatable_candidate': bool(real_code and has_exec and not contact_only)
    }
    e['cause']=' / '.join(e['causes']); del e['causes']
    return e
kb={c:finalize(e) for c,e in kb.items()}

entries=list(kb.values())
json.dump(entries, open('/home/user/MetaClaw/docs/intelligent-troubleshooting/l0/sop_kb.json','w'), ensure_ascii=False, indent=1)

# ---- inventory stats ----
real=[e for e in entries if e['completeness']['has_code']]
def pct(a,b): return f"{a} ({(a*100//b) if b else 0}%)"
tot=len(real)
with_rec=[e for e in real if e['completeness']['has_recovery']]
contact_only=[e for e in with_rec if e['completeness']['contact_only']]
auto_cand=[e for e in real if e['completeness']['automatable_candidate']]
with_log=[e for e in real if e['log_signature']]
by_level=defaultdict(int)
for e in real: by_level[e['level'] or '—']+=1
# backlog: P0/P1 with recovery, automatable candidate, missing evidence_dql (all) -> ready to activate first
backlog=sorted([e for e in auto_cand if e['level'] in ('P0','P1')], key=lambda e:(e['level'], e['error_code']))

print("TOTAL_UNIQUE_CODES", tot)
print("WITH_RECOVERY", pct(len(with_rec),tot))
print("CONTACT_ONLY", pct(len(contact_only),len(with_rec) or 1), "of those-with-recovery")
print("AUTOMATABLE_CANDIDATE", pct(len(auto_cand),tot))
print("WITH_STRUCTURED_LOG", pct(len(with_log),tot))
print("BY_LEVEL", dict(by_level))
print("BACKLOG_P0P1_ACTIVATE_FIRST", len(backlog))
json.dump({'total':tot,'with_recovery':len(with_rec),'contact_only':len(contact_only),
           'automatable_candidate':len(auto_cand),'with_log':len(with_log),
           'by_level':dict(by_level),'backlog_first':[e['error_code'] for e in backlog][:25]},
          open('/tmp/inv.json','w'), ensure_ascii=False)
