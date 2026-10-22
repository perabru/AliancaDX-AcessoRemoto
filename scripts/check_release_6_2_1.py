"""Validate the already uploaded 6.2.1 draft before its one-time publication."""
import argparse
import json
import re
from pathlib import Path


def verify(release, pin, published=False, published_target=None):
    if published_target is not None and (not published or not re.fullmatch(r'[0-9a-f]{40}', published_target)):
        raise ValueError('An explicit publication commit requires published state and a full commit SHA')
    target = published_target if published_target is not None else pin['target']
    expected = {row['name']: (row['bytes'], row['digest'], 'uploaded') for row in pin['assets']}
    actual = {row['name']: (row['size'], row['digest'], row['state']) for row in release['assets']}
    if (release['id'] != pin['release_id'] or release['tag_name'] != pin['tag']
            or release['target_commitish'] != target
            or release['draft'] is not (not published) or release['prerelease']
            or len(release['assets']) != len(expected) or actual != expected):
        raise RuntimeError('Release target, state, or uploaded asset digests differ from the verified draft')
    if published and not release['html_url'].endswith('/releases/tag/' + pin['tag']):
        raise RuntimeError('Unexpected published release URL')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('release_json', type=Path)
    parser.add_argument('--published', action='store_true')
    parser.add_argument('--published-target', help='Current main commit verified to contain identical application sources')
    args = parser.parse_args()
    pin = json.loads(Path(__file__).with_name('release_6_2_1_recovery.json').read_text())
    verify(json.loads(args.release_json.read_text()), pin, args.published, args.published_target)
    print('Verified exact release target, state and all eight uploaded SHA-256 digests.')
