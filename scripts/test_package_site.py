"""Guard the boundary between the public PWA and local-only project files."""
import json
from pathlib import Path
import tempfile
import unittest
import package_site


class PublicAssetBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / 'ios-pwa'
        self.base.mkdir()
        self.assets = ['index.html', 'bridge.html', 'manifest.webmanifest', 'sw.js']
        for name in self.assets:
            (self.base / name).write_text('fixture')
        self.manifest(self.assets)

    def manifest(self, values):
        (self.base / 'public-assets.json').write_text(json.dumps(values))

    def test_only_inventory_assets_are_selected(self):
        (self.base / 'not-public.txt').write_text('local fixture')
        self.assertEqual({name for name, _ in package_site.ios_public_assets(self.root)}, set(self.assets))

    def test_private_key_or_test_file_cannot_be_published(self):
        for name in ['private.p12', 'secret.env', 'tests/fixture.mjs', 'server.py']:
            with self.subTest(name=name):
                p=self.base/name; p.parent.mkdir(exist_ok=True); p.write_text('fixture')
                self.manifest(self.assets+[name])
                with self.assertRaises(ValueError): package_site.ios_public_assets(self.root)

    def test_path_escape_or_alias_is_rejected(self):
        for name in ['../outside.txt', '/tmp/a', './index.html', 'folder/../index.html', 'folder\\a', 'a:b', '.hidden']:
            with self.subTest(name=name):
                self.manifest(self.assets+[name])
                with self.assertRaises(ValueError): package_site.ios_public_assets(self.root)

    def test_symlink_to_private_project_data_is_rejected(self):
        outside=self.root/'private.txt'; outside.write_text('fixture')
        (self.base/'link.txt').symlink_to(outside)
        self.manifest(self.assets+['link.txt'])
        with self.assertRaises(ValueError): package_site.ios_public_assets(self.root)

    def test_missing_or_incomplete_runtime_is_rejected(self):
        for values in [[], ['index.html'], self.assets+['missing.mjs']]:
            with self.subTest(values=values):
                self.manifest(values)
                with self.assertRaises(ValueError): package_site.ios_public_assets(self.root)

    def test_ambiguous_inventory_is_rejected(self):
        for values in [self.assets+['index.html'], {}, self.assets+[{}]]:
            with self.subTest(values=values):
                self.manifest(values)
                with self.assertRaises(ValueError): package_site.ios_public_assets(self.root)


if __name__ == '__main__': unittest.main()
