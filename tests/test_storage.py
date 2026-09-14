import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dashboard'))
from storage import ConflictError, ModelStore


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'models.sqlite3'
        self.store = ModelStore(self.path)
        self.store.initialize(lambda: [])

    def tearDown(self):
        self.temp.cleanup()

    def add(self):
        return self.store.save({'name': 'GLM', 'url': 'http://glm:8080', 'api_key': 'secret-key'})

    def test_persistence_key_masking_and_permissions(self):
        model = self.add()
        reopened = ModelStore(self.path)
        reopened.initialize(lambda: self.fail('must not import again'))
        listed = reopened.list_public()['models'][0]
        self.assertEqual(listed['id'], model['id'])
        self.assertTrue(listed['has_api_key'])
        self.assertNotIn('secret-key', json.dumps(listed))
        self.assertEqual(reopened.snapshot()[1][0][0]['nodes'][0]['_api_key'], 'secret-key')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_edit_retains_rotates_and_clears_key(self):
        model = self.add()
        node_id = self.store.snapshot()[1][0][0]['nodes'][0]['id']
        for key, expected, clear in [('', 'secret-key', False), ('new-key', 'new-key', False), ('', '', True)]:
            model = self.store.save({'name': 'Renamed', 'url': 'http://glm:8080/v1', 'api_key': key, 'clear_key': clear, 'version': model['version']}, model['id'])
            node = self.store.snapshot()[1][0][0]['nodes'][0]
            self.assertEqual(node['_api_key'], expected)
            self.assertEqual(node['_metrics_key'], expected)
            self.assertEqual(node['id'], node_id)

    def test_url_change_rotates_metric_identity(self):
        model = self.add()
        old = self.store.snapshot()[1][0][0]['nodes'][0]['id']
        self.store.save({'name': 'GLM', 'url': 'https://new.example/api/v1', 'version': model['version']}, model['id'])
        node = self.store.snapshot()[1][0][0]['nodes'][0]
        self.assertNotEqual(old, node['id'])
        self.assertEqual(node['metrics_url'], 'https://new.example/api/metrics')

    def test_delete_stays_deleted_after_restart(self):
        model = self.add()
        self.store.delete(model['id'], model['version'])
        reopened = ModelStore(self.path)
        reopened.initialize(lambda: self.fail('deleted models must not be reimported'))
        self.assertEqual(reopened.list_public()['models'], [])

    def test_stale_edit_and_delete_cannot_overwrite(self):
        model = self.add()
        self.store.save({'name': 'new', 'url': model['url'], 'version': model['version']}, model['id'])
        with self.assertRaises(ConflictError):
            self.store.save({'name': 'old', 'url': model['url'], 'version': model['version']}, model['id'])
        with self.assertRaises(ConflictError):
            self.store.delete(model['id'], model['version'])
        self.assertEqual(self.store.list_public()['models'][0]['name'], 'new')

    def test_validation_is_atomic(self):
        revision = self.store.snapshot()[0]
        for url in ['file:///tmp/a', 'http://user:key@host', 'http://host:abc', 'http://host?q=secret', 'http://host/a\nb']:
            with self.assertRaises(ValueError):
                self.store.save({'name': 'bad', 'url': url})
        with self.assertRaises(ValueError):
            self.store.save({'name': 'bad', 'url': 'http://host', 'api_key': 'key\nheader'})
        self.assertEqual(self.store.snapshot(), (revision, []))

    def test_legacy_secrets_imported_once_and_not_exposed(self):
        path = Path(self.temp.name) / 'legacy.sqlite3'
        store = ModelStore(path)
        project = {'id': 'model-1', 'name': 'GLM', 'deployment': 'standard', 'nodes': [{'id': 'endpoint', 'role': 'engine', 'api_base_url': 'http://glm/v1', 'metrics_url': 'http://glm/metrics', 'api_key_env': 'LEGACY_KEY', 'metrics_key_env': 'LEGACY_KEY'}]}
        with patch.dict(os.environ, {'LEGACY_KEY': 'legacy-secret'}):
            store.initialize(lambda: [project])
        private = store.snapshot()[1][0][0]
        self.assertEqual(private['nodes'][0]['_api_key'], 'legacy-secret')
        self.assertNotIn('api_key_env', private['nodes'][0])
        self.assertNotIn('legacy-secret', json.dumps(store.list_public()))
        self.assertTrue(store.list_public()['models'][0]['editable'])
