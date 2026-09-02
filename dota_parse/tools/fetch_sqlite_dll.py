import hashlib, io, os, re, ssl, sys, urllib.request, zipfile

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'dota_parse', 'sqlite3.dll')
OUT = os.path.abspath(OUT)
os.makedirs(os.path.dirname(OUT), exist_ok=True)

CTX = ssl._create_unverified_context()

def get(url, binary=False):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=120, context=CTX) as r:
        data = r.read()
    return data

html = get('https://www.sqlite.org/download.html').decode('utf-8', 'replace')
# The page embeds the real file paths as plain text / js strings, e.g.
# '2026/sqlite-dll-win-x64-3530400.zip' — hrefs point at hp1.html, so match
# any occurrence of the file path anywhere on the page.
links = sorted(set(re.findall(r'[\w./-]*sqlite-dll-win-x64-\d+\.zip', html)))
print('candidate links:', links)
if not links:
    sys.exit('no dll link found')
# newest = highest build number suffix
def buildnum(u):
    m = re.search(r'sqlite-dll-win-x64-(\d+)\.zip', u)
    return int(m.group(1)) if m else 0
best = max(links, key=buildnum)
if not best.startswith('http'):
    best = 'https://www.sqlite.org/' + best.lstrip('/')
print('downloading', best)
data = get(best)

# Checksum advertised on the download page: PRODUCT,<ver>,<path>,<size>,<sha3-256>
want = None
for m in re.finditer(r'PRODUCT,[^,\n]*,' + re.escape(best.replace('https://www.sqlite.org/', '')) + r',(\d+),([0-9a-fA-F]+)', html):
    want = (int(m.group(1)), m.group(2).lower())
if want:
    print('advertised size/sha3:', want)
    got = (len(data), hashlib.sha3_256(data).hexdigest())
    print('actual     size/sha3:', got)
    if want != got:
        sys.exit('checksum mismatch - aborting')
else:
    print('warning: no advertised checksum found, skipping verification')

zf = zipfile.ZipFile(io.BytesIO(data))
names = zf.namelist()
print('zip contents:', names)
dll = [n for n in names if n.lower().endswith('sqlite3.dll')]
if not dll:
    sys.exit('sqlite3.dll not inside zip')
with open(OUT, 'wb') as f:
    f.write(zf.read(dll[0]))
print('wrote', OUT, os.path.getsize(OUT), 'bytes')
