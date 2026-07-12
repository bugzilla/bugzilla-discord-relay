#! env python

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# This is a debug script to test the webhooks with previously-captured
# data. It needs two arguments:
# 1) The webhook URL of the RELAY server (not Discord)
# 2) Path to a file to send as the payload.

import sys
import os
import requests

def usage():
    print("Usage: %s WEBHOOK_URL PAYLOADFILE" % sys.argv[0])
    print()
    print("WEBHOOK_URL is the URL of the RELAY server (not Discord)")
    print("PAYLOADFILE is the path to a file to send as payload")
    sys.exit(0)

if len(sys.argv) < 3:
    print("Not enough arguments")
    print()
    usage()

url = sys.argv[1]
payloadfile = sys.argv[2]
print("url = %s" % url)
print("payload = %s" % payloadfile)

if os.path.isfile(payloadfile) and os.access(payloadfile, os.R_OK):
    contents = ""
    with open(payloadfile, 'r', encoding='utf-8') as f:
        try:
            contents = f.read()
        except:
            print("Couldn't read file")
            sys.exit(0)

    response = requests.post(
            url=url,
            headers={'Content-Type': 'application/json',
                     'User-Agent': 'WebHookTester/1.0',
                     'Content-Length': str(len(contents)),
                     'Connection': 'close'},
            data=contents)
    status = "%s %s" % (response.status_code, response.reason)
    print(status)
    print(response.content)
    sys.exit(0)
else:
    print("Couldn't open %s" % payloadfile)
    sys.exit(0)
