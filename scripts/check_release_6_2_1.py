"""Validate the already uploaded 6.2.1 draft before its one-time publication."""
import argparse
import json
from pathlib import Path


def verify(release, pin, published=False):
    expected = {row['name']: (row['bytes'], row['digest'], 'uploaded') for row in pin['assets']}
    actual = {row['name']: (row['size'], row['digest'], row['state']) for row in release['assets']}
    if (release['id'] != pin['release_id'] or release['tag_name'] != pin['tag']
            or release['target_commitish'] != pin['target']
            or release['draft'] is not (not published) or release['prerelease']
            or len(release['assets']) != len(expected) or actual != expected):
        raise RuntimeError('Release target, state, or uploaded asset digests differ from the verified draft')
    if published and not release['html_url'].endswith('/releases/tag/' + pin['tag']):
        raise RuntimeError('Unexpected published release URL')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('release_json', type=Path)
    parser.add_argument('--published', action='store_true')
    args = parser.parse_args()
    pin = json.loads(Path(__file__).with_name('release_6_2_1_recovery.json').read_text())
    verify(json.loads(args.release_json.read_text()), pin, args.published)
    print('Verified exact release target, state and all eight uploaded SHA-256 digests.')
