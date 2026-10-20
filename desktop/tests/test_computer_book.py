"""Persistent, explicitly user-managed computer registry behavior."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from computer_book import ComputerBook


class ComputerBookTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.path = self.directory / 'state' / 'computers.json'
        self.book = ComputerBook(self.path)

    def tearDown(self):
        self.temporary.cleanup()

    def add(self, name='Laboratório II', address='192.168.1.20', port=64321):
        return self.book.add(name, address, port)

    def fixture(self, count=1):
        return {'schema': 1, 'computers': [
            {'id': str(uuid.uuid4()), 'name': f'Computador {index}', 'address': f'pc-{index}.example.org',
             'port': 64321, 'created_at': '2026-10-10T09:00:00+00:00',
             'updated_at': '2026-10-10T09:00:00+00:00', 'last_seen': None}
            for index in range(count)]}

    def write_fixture(self, fixture):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(fixture), encoding='utf-8')

    def test_empty_registry_needs_no_initial_write(self):
        self.assertEqual(self.book.entries(), [])
        self.assertFalse(self.path.exists())

    def test_add_persists_only_schema_and_connection_metadata(self):
        first = self.add(name='  Laboratório II  ', address='LABORATORIO.Example.Org.')
        second = self.add(name='Sala 03', address='192.168.1.21')
        reloaded = ComputerBook(self.path).entries()
        self.assertEqual(reloaded, [first, second])
        self.assertEqual(first['name'], 'Laboratório II')
        self.assertEqual(first['address'], 'laboratorio.example.org')
        self.assertIsNone(first['last_seen'])
        self.assertEqual(str(uuid.UUID(first['id'])), first['id'])
        self.assertIsNotNone(datetime.fromisoformat(first['created_at']).tzinfo)
        stored = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(set(stored), {'schema', 'computers'})
        self.assertEqual(stored['schema'], 1)
        self.assertEqual(set(first), {'id', 'name', 'address', 'port', 'created_at', 'updated_at', 'last_seen'})
        self.assertNotIn('password', self.path.read_text(encoding='utf-8').lower())

    def test_returns_are_independent_copies(self):
        original = self.add()
        original['name'] = 'Mudei só a cópia'
        listing = self.book.entries()
        listing[0]['address'] = 'changed.invalid'
        listing.clear()
        found = self.book.find_endpoint('192.168.1.20', 64321)
        found['port'] = 1
        self.assertEqual(self.book.entries()[0]['name'], 'Laboratório II')
        self.assertEqual(self.book.entries()[0]['address'], '192.168.1.20')
        self.assertEqual(self.book.entries()[0]['port'], 64321)

    def test_rename_keeps_identity_endpoint_created_at_and_last_connection(self):
        first = self.add()
        seen = self.book.mark_seen(first['id'], '2026-10-10T06:30:00-03:00')
        renamed = self.book.update(first['id'], name='  Sala de informática  ')
        self.assertEqual(renamed['name'], 'Sala de informática')
        for key in ('id', 'address', 'port', 'created_at', 'last_seen'):
            self.assertEqual(renamed[key], seen[key])
        self.assertEqual(ComputerBook(self.path).entries(), [renamed])

    def test_change_endpoint_preserves_name_and_reloads(self):
        first = self.add()
        updated = self.book.update(first['id'], address='NEW.example.org.', port=5905)
        self.assertEqual(updated['name'], first['name'])
        self.assertEqual((updated['address'], updated['port']), ('new.example.org', 5905))
        self.assertIsNone(self.book.find_endpoint(first['address'], first['port']))
        self.assertEqual(ComputerBook(self.path).find_endpoint('NEW.EXAMPLE.ORG.', 5905), updated)

    def test_remove_is_persistent_and_unknown_id_is_actionable(self):
        first = self.add()
        self.assertIsNone(self.book.remove(first['id']))
        self.assertEqual(ComputerBook(self.path).entries(), [])
        with self.assertRaisesRegex(ValueError, 'não está mais'):
            self.book.remove(first['id'])
        with self.assertRaisesRegex(ValueError, 'identificador'):
            self.book.update('not-a-uuid', name='Teste')

    def test_dns_and_ipv6_equivalents_cannot_duplicate(self):
        named = self.add(address='PC-LAB.Example.Org.')
        self.assertEqual(self.book.find_endpoint('pc-lab.example.org', 64321), named)
        with self.assertRaisesRegex(ValueError, 'já estão cadastrados'):
            self.add(address='PC-LAB.EXAMPLE.ORG')
        ipv6 = self.add(name='IPv6', address='[2001:0DB8:0000:0000:0000:0000:0000:0001]')
        self.assertEqual(ipv6['address'], '2001:db8::1')
        self.assertEqual(self.book.find_endpoint('2001:DB8::1', 64321), ipv6)
        with self.assertRaises(ValueError):
            self.add(name='Duplicado', address='[2001:db8::1]')
        self.assertEqual(self.book.update(ipv6['id'], name='IPv6 renomeado')['id'], ipv6['id'])

    def test_same_address_different_port_is_allowed_and_update_collision_is_atomic(self):
        first = self.add()
        second = self.add(name='Outro serviço', port=5905)
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'já estão cadastrados'):
            self.book.update(second['id'], name='Não deve salvar', port=first['port'])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.book.entries(), [first, second])

    def test_valid_boundary_names_ports_dns_and_ipv6_scope(self):
        name = 'A' * 80
        first = self.add(name=name, address='a' * 63 + '.example.org', port=1)
        self.assertEqual(first['name'], name)
        second = self.add(name='IPv6 local', address='[FE80:0:0:0:0:0:0:1%en0]', port=65535)
        self.assertEqual(second['address'], 'fe80::1%en0')
        international = self.add(name='Internacional', address='bücher.example')
        self.assertEqual(international['address'], 'xn--bcher-kva.example')

    def test_invalid_name_address_port_inputs_do_not_write(self):
        for name in ('', '   ', 'a' * 81, 'Lab\nSala', '\x00Lab', 'Lab\u202e', 3):
            with self.subTest(name=repr(name)), self.assertRaises(ValueError):
                self.add(name=name)
        for address in ('', 'pc lab', ' pc', 'pc\n', 'https://pc', 'pc/path', 'user@pc', 'pc:5905',
                        '[pc]', '[::1', '::1]', '000.1.2.3', 'example..org', 'a' * 254,
                        '-pc', 'pc-', 'a' * 64 + '.org', '[192.168.1.1]', 'fe80::1%bad scope', 123):
            with self.subTest(address=repr(address)), self.assertRaises(ValueError):
                self.add(address=address)
        for port in (True, False, 0, 65536, -1, '64321', 64321.0, None):
            with self.subTest(port=repr(port)), self.assertRaises(ValueError):
                self.add(port=port)
        self.assertFalse(self.path.exists())

    def test_repeated_mark_seen_persists_timestamp_without_renaming_or_duplicates(self):
        first = self.add()
        for timestamp in ('2026-10-10T06:30:00-03:00', '2026-10-10T09:45:00Z', '2026-10-10T10:00:00+00:00'):
            result = self.book.mark_seen(first['id'], timestamp)
            self.assertEqual(result['name'], first['name'])
            self.assertEqual(result['id'], first['id'])
        self.assertEqual(len(self.book.entries()), 1)
        self.assertEqual(ComputerBook(self.path).entries()[0]['last_seen'], '2026-10-10T10:00:00+00:00')
        now = self.book.mark_seen(first['id'])
        self.assertIsNotNone(datetime.fromisoformat(now['last_seen']).tzinfo)
        for timestamp in ('not-a-date', '2026-10-10', '2026-10-10T10:00:00', 123,
                          datetime(2026, 10, 10)):
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                self.book.mark_seen(first['id'], timestamp)

    def test_maximum_256_existing_records_is_enforced_without_replacing_file(self):
        fixture = self.fixture(256)
        self.write_fixture(fixture)
        loaded = ComputerBook(self.path)
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '256 computadores'):
            loaded.add('Novo', 'new.example.org', 64321)
        self.assertEqual(self.path.read_bytes(), original)
        loaded.update(fixture['computers'][0]['id'], name='Renomeado no limite')
        self.assertEqual(len(loaded.entries()), 256)

    def test_corrupt_json_unknown_schema_and_invalid_records_are_preserved(self):
        bad_files = [b'{invalid', b'\xff', b'', b'{"schema":2,"computers":[]}',
            b'{"schema":true,"computers":[]}', b'{"schema":1,"computers":null}']
        invalid = self.fixture()
        invalid['computers'][0]['password'] = 'never-save-passwords'
        bad_files.append(json.dumps(invalid).encode())
        invalid = self.fixture()
        invalid['computers'][0]['created_at'] = None
        bad_files.append(json.dumps(invalid).encode())
        invalid = self.fixture()
        invalid['computers'][0]['port'] = True
        bad_files.append(json.dumps(invalid).encode())
        invalid = self.fixture(2)
        invalid['computers'][1]['address'] = invalid['computers'][0]['address']
        bad_files.append(json.dumps(invalid).encode())
        bad_files.append(json.dumps(self.fixture(257)).encode())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for contents in bad_files:
            with self.subTest(contents=contents[:80]):
                self.path.write_bytes(contents)
                with self.assertRaisesRegex(ValueError, 'preservado'):
                    ComputerBook(self.path)
                self.assertEqual(self.path.read_bytes(), contents)

    def test_external_corruption_is_detected_before_any_overwrite(self):
        self.add()
        corrupted = b'{broken externally'
        self.path.write_bytes(corrupted)
        with self.assertRaises(ValueError): self.book.entries()
        with self.assertRaises(ValueError): self.add(name='Outro', address='192.168.1.22')
        with self.assertRaises(ValueError): self.book.find_endpoint('192.168.1.20', 64321)
        self.assertEqual(self.path.read_bytes(), corrupted)

    def test_replace_failure_rolls_back_and_can_retry_add(self):
        first = self.add()
        original = self.path.read_bytes()
        with patch('computer_book.os.replace', side_effect=PermissionError('arquivo protegido')):
            with self.assertRaisesRegex(OSError, 'dados anteriores foram mantidos'):
                self.add(name='Segundo', address='192.168.1.22')
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.book.entries(), [first])
        self.assertEqual(set(self.path.parent.iterdir()), {self.path, self.book.lock_path})
        second = self.add(name='Segundo', address='192.168.1.22')
        self.assertEqual(self.book.entries(), [first, second])

    def test_flush_failure_cannot_change_update_remove_or_last_seen(self):
        first = self.add()
        original = self.path.read_bytes()
        actions = [lambda: self.book.update(first['id'], name='Mudança'),
                   lambda: self.book.remove(first['id']),
                   lambda: self.book.mark_seen(first['id'])]
        for action in actions:
            with patch('computer_book.os.fsync', side_effect=OSError('sem espaço')):
                with self.assertRaisesRegex(OSError, 'permissões e o espaço'):
                    action()
            self.assertEqual(self.path.read_bytes(), original)
            self.assertEqual(self.book.entries(), [first])
            self.assertEqual(set(self.path.parent.iterdir()), {self.path, self.book.lock_path})

    def test_multiple_instances_and_threads_do_not_lose_saved_records(self):
        second = ComputerBook(self.path)
        def save(index):
            book = self.book if index % 2 else second
            entry = book.add(f'Computador {index}', f'pc-{index}.example.org', 64321)
            book.mark_seen(entry['id'])
            return entry['id']
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(save, range(40)))
        entries = ComputerBook(self.path).entries()
        self.assertEqual(len(entries), 40)
        self.assertEqual({entry['id'] for entry in entries}, set(ids))
        self.assertTrue(all(entry['last_seen'] is not None for entry in entries))

    def test_default_paths_match_controller_state_directory(self):
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.directory / 'appdata')}):
            book = ComputerBook()
            self.assertEqual(book.path, self.directory / 'appdata/MiniAnyDesk/computers.json')
        with patch.dict(os.environ, {}, clear=True), patch('computer_book.Path.home', return_value=self.directory):
            book = ComputerBook()
            self.assertEqual(book.path, self.directory / '.config/MiniAnyDesk/computers.json')

    def test_two_processes_can_add_rename_and_mark_seen_without_lost_records(self):
        worker = '''
import sys,time
from pathlib import Path
from computer_book import ComputerBook
path,start,ready,index = sys.argv[1:]
book = ComputerBook(path)
Path(ready).touch()
deadline = time.monotonic()+5
while not Path(start).exists():
    if time.monotonic() > deadline: raise RuntimeError("test barrier timeout")
    time.sleep(.005)
for item in range(20):
    entry = book.add("Original " + index, "pc-%s-%s.example.org" % (index,item),64321)
    book.update(entry["id"],name="Renomeado %s-%s" % (index,item))
    book.mark_seen(entry["id"])
'''
        start = self.directory / 'start-workers'
        processes = []
        source = str(Path(__file__).resolve().parents[1])
        environment = dict(os.environ, PYTHONPATH=source + os.pathsep + os.environ.get('PYTHONPATH', ''))
        try:
            for index in range(2):
                ready = self.directory / f'ready-{index}'
                processes.append(subprocess.Popen([sys.executable, '-c', worker, str(self.path), str(start),
                    str(ready), str(index)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment))
            deadline = time.monotonic() + 5
            while not all((self.directory / f'ready-{index}').exists() for index in range(2)):
                self.assertLess(time.monotonic(), deadline, 'Workers did not reach the concurrent start barrier')
                time.sleep(.005)
            start.touch()
            for process in processes:
                output, errors = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, output + errors)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=3)
        records = ComputerBook(self.path).entries()
        self.assertEqual(len(records), 40)
        self.assertEqual({entry['name'] for entry in records},
                         {f'Renomeado {index}-{item}' for index in range(2) for item in range(20)})
        self.assertTrue(all(entry['last_seen'] is not None for entry in records))

    @unittest.skipIf(os.name == 'nt', 'The POSIX file-lock holder is specific to this Linux test environment')
    def test_busy_external_lock_times_out_without_changing_registry(self):
        import fcntl
        first = self.add()
        original = self.path.read_bytes()
        with self.book.lock_path.open('r+b') as held:
            fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                started = time.monotonic()
                with patch('computer_book.LOCK_TIMEOUT', .1):
                    with self.assertRaisesRegex(OSError, 'outro programa'):
                        self.add(name='Bloqueado', address='other.example.org')
                self.assertLess(time.monotonic() - started, 1)
            finally:
                fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.book.entries(), [first])


if __name__ == '__main__': unittest.main(verbosity=2)
