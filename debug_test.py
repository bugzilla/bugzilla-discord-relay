#! env python

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# This is a debug script to test the webhooks with previously-captured
# data. It needs two arguments:
# 1) The webhook URL of the RELAY server (not Discord) as entered in Bugzilla's WebHook configuration.
# 2) Path to a file to send as the payload.

import sys
import os
import json
import argparse
from urllib.parse import urlsplit
import requests

SPOOL_METADATA_PREFIX = 'X-Bz2Discord-'

def parse_spool_file(path):
    raw = b''
    with open(path, 'rb') as f:
        raw = f.read()

    metadata = {}
    if raw.startswith(SPOOL_METADATA_PREFIX.encode('utf-8')):
        header, separator, payload = raw.partition(b'\n\n')
        if not separator:
            header, separator, payload = raw.partition(b'\r\n\r\n')
        if separator:
            for line in header.decode('utf-8', errors='replace').splitlines():
                if not line.startswith(SPOOL_METADATA_PREFIX) or ':' not in line:
                    continue
                key, value = line.split(':', 1)
                key = key[len(SPOOL_METADATA_PREFIX):].strip().lower().replace('-', '_')
                metadata[key] = value.strip()
            return payload, metadata

    return raw, metadata

def load_config(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def usage(parser):
    parser.print_help()
    print()
    print('Examples:')
    print('  %(prog)s https://relay.example/hook-id spool/bugzilla-...')
    print('  %(prog)s --relay-url https://relay.example --payload-file spool/bugzilla-... --config /path/to/config.json')
    sys.exit(1)

def main():
    parser = argparse.ArgumentParser(
        description='Replay a captured webhook payload to the relay.',
        add_help=True,
    )
    parser.add_argument('relay_url', nargs='?', help='Relay URL or relay base URL.')
    parser.add_argument('payloadfile', nargs='?', help='Path to captured payload file.')
    parser.add_argument('--relay-url', dest='relay_url_opt', help='Relay URL or relay base URL.')
    parser.add_argument('--payload-file', dest='payloadfile_opt', help='Path to captured payload file.')
    parser.add_argument('--config', help='Path to relay config JSON. If provided, auth header is loaded from config.')
    parser.add_argument('--webhook-id', help='Webhook ID. If omitted, metadata header value is used when present.')
    parser.add_argument('--use-next-secret', action='store_true', help='Use api_key_value_next from config when present.')

    args = parser.parse_args()

    relay_url = args.relay_url_opt or args.relay_url
    payloadfile = args.payloadfile_opt or args.payloadfile

    if not payloadfile:
        usage(parser)

    if not os.path.isfile(payloadfile) or not os.access(payloadfile, os.R_OK):
        print("Couldn't open %s" % payloadfile)
        return 1

    payload_bytes, spool_metadata = parse_spool_file(payloadfile)

    if not relay_url:
        print('relay URL is required')
        usage(parser)

    relay_path = spool_metadata.get('relay_path')
    if relay_path:
        parsed = urlsplit(relay_url)
        if parsed.path in ('', '/'):
            relay_url = relay_url.rstrip('/') + relay_path

    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'WebHookTester/2.0',
        'Content-Length': str(len(payload_bytes)),
        'Connection': 'close',
    }

    if args.config:
        config = load_config(args.config)
        webhook_id = args.webhook_id or spool_metadata.get('webhook_id')
        if not webhook_id:
            print('webhook ID is required when --config is used (or include metadata header with webhook ID).')
            return 1
        webhook_config = config.get('webhooks', {}).get(webhook_id)
        if not webhook_config:
            print("webhook ID '%s' not found in config" % webhook_id)
            return 1
        auth_header = webhook_config.get('api_key_header')
        if args.use_next_secret and webhook_config.get('api_key_value_next'):
            auth_value = webhook_config.get('api_key_value_next')
        else:
            auth_value = webhook_config.get('api_key_value')
        if not auth_header or not auth_value:
            print("webhook config for '%s' is missing auth header/value" % webhook_id)
            return 1
        headers[auth_header] = auth_value

    print("url = %s" % relay_url)
    print("payload = %s" % payloadfile)
    if spool_metadata:
        print("spool metadata: payload_type=%s routing_key=%s webhook_id=%s" % (
            spool_metadata.get('payload_type', '(unknown)'),
            spool_metadata.get('routing_key', '(unknown)'),
            spool_metadata.get('webhook_id', '(none)'),
        ))

    response = requests.post(
            url=relay_url,
            headers=headers,
            data=payload_bytes,
            timeout=30)
    status = "%s %s" % (response.status_code, response.reason)
    print(status)
    print(response.content)
    return 0

if __name__ == '__main__':
    sys.exit(main())
