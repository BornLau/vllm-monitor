"""Durable, bounded history tiles with exact range aggregates at 3-second resolution."""
import base64
import hashlib
import json
import math
import sqlite3
import threading
import time
import zlib
from array import array
from contextlib import closing
from pathlib import Path

_locks = [threading.Lock() for _ in range(64)]
_FIELDS = {'times':'q', 'sums':'d', 'counts':'I', 'idle':'I'}


def summarize(results, existing=None):
    """Prefix sums retain all observations; range queries only need two lookups."""
    merged = {json.dumps(item['metric'], sort_keys=True): item for item in existing or []}
    for raw in results:
        key = json.dumps(raw['metric'], sort_keys=True)
        item = merged.setdefault(key, dict(metric=raw['metric'], times=array('q'),
                                          sums=array('d',[0]), counts=array('I',[0]), idle=array('I',[0])))
        for point in raw.get('values', []):
            t, v = int(float(point[0])), float(point[1])
            if not math.isfinite(v) or v < 0 and v != -1:
                continue
            if item['times'] and t <= item['times'][-1]:
                raise ValueError('历史采样时间重复或乱序')
            total, count, idle = (v, int(point[2]), int(point[3])) if len(point)==4 else (v if v>=0 else 0, int(v>=0), int(v==-1))
            item['times'].append(t)
            item['sums'].append(item['sums'][-1] + total)
            item['counts'].append(item['counts'][-1] + count)
            item['idle'].append(item['idle'][-1] + idle)
    return list(merged.values())


def encode(results):
    # Store arrays as little-endian bytes, avoiding millions of JSON numbers.
    import sys
    rows=[]
    for item in results:
        row={'metric':item['metric']}
        for field, kind in _FIELDS.items():
            values=array(kind,item[field])
            if sys.byteorder != 'little': values.byteswap()
            row[field]=base64.b64encode(values.tobytes()).decode('ascii')
        rows.append(row)
    return zlib.compress(json.dumps(rows,separators=(',',':')).encode(),level=1)


def decode(payload):
    import sys
    rows=json.loads(zlib.decompress(payload))
    for row in rows:
        for field,kind in _FIELDS.items():
            values=array(kind)
            values.frombytes(base64.b64decode(row[field]))
            if sys.byteorder != 'little': values.byteswap()
            row[field]=values
    return rows


class HistoryCache:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS history (key TEXT PRIMARY KEY, until INTEGER, updated REAL, payload BLOB)')

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def read(self, source, start, end, fetch, resolution=3):
        key = hashlib.sha256(f'v3:{source}:{start}:{resolution}'.encode()).hexdigest()
        with _locks[int(key[:8], 16) % len(_locks)]:
            now = time.time()
            with closing(self.connect()) as db:
                row = db.execute('SELECT until,updated,payload FROM history WHERE key=?', (key,)).fetchone()
            # Rebuild after a day to admit backfills/corrections; never save failures.
            until, result = (row[0], decode(row[2])) if row and now-row[1] < 86400 else (start-resolution, [])
            if until >= end:
                return result
            result = summarize(fetch(until+resolution, end), result)
            payload = encode(result)
            with closing(self.connect()) as db, db:
                db.execute('INSERT OR REPLACE INTO history VALUES (?,?,?,?)', (key, end, now, payload))
                db.execute('DELETE FROM history WHERE key IN (SELECT key FROM history ORDER BY updated DESC LIMIT -1 OFFSET 512)')
            return result
