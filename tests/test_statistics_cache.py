import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from test_app import app


class HistoryCacheTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)/'history.sqlite3'
        self.cache = app.HistoryCache(self.path)

    def test_extends_existing_tile_and_survives_reopening(self):
        calls=[]
        def fetch(start,end):
            calls.append((start,end))
            return [dict(metric={'model':'m'},values=[[t,str(t)] for t in range(start,end+1,3)])]
        self.cache.read('source',0,6,fetch)
        result=app.HistoryCache(self.path).read('source',0,9,fetch)
        self.assertEqual(calls,[(0,6),(9,9)])
        self.assertEqual(list(result[0]['times']),[0,3,6,9])
        self.cache.read('source',0,3,fetch)
        self.assertEqual(len(calls),2)

    def test_failures_are_not_cached_and_sources_are_isolated(self):
        with self.assertRaises(ValueError):
            self.cache.read('a',0,6,lambda *_: (_ for _ in ()).throw(ValueError('failed')))
        calls=[]
        def fetch(*args): calls.append(args); return []
        self.cache.read('a',0,6,fetch)
        self.cache.read('b',0,6,fetch)
        self.assertEqual(len(calls),2)

    def test_concurrent_identical_requests_only_fetch_once(self):
        calls=[]
        def fetch(*args): calls.append(args); return []
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _: self.cache.read('a',0,6,fetch), range(8)))
        self.assertEqual(results,[[]]*8)
        self.assertEqual(len(calls),1)

    def test_old_tiles_are_rebuilt_for_backfills(self):
        calls=[]
        def fetch(*args): calls.append(args); return []
        with patch('statistics_cache.time.time',return_value=100000):
            self.cache.read('a',0,6,fetch)
        with patch('statistics_cache.time.time',return_value=186401):
            self.cache.read('a',0,6,fetch)
        self.assertEqual(calls,[(0,6),(0,6)])
