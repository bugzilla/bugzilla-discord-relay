# Bugzilla Discord Relay

This is an app that can be set as a target for outgoing Bugzilla webhooks, and can in turn relay those webhook requests to a Discord incoming webhook.

Bugzilla (harmony and newer) can send outgoing Webhooks, but they aren't in a format that Discord can understand. This is a server application you can set up to accept webhooks from Bugzilla, reformat them how Discord expects them, then forward them to Discord, in order to announce changes to bugs on a Discord channel.

## Installation

This app is designed to run under mod_wsgi on Apache HTTPd, and requires Python 3.8 or newer.

This app has python dependencies, and requires a virtualenv to be set up for it.

* `cd` into the directory containing the app
* `virtualenv venv`
* `source venv/bin/activate`
* `pip install -r requirements.txt`

Note that the version of python installed in the virtualenv needs to match the one that mod_wsgi was compiled for.

Place the following in the VirtualHost block for the VirtualHost that will host your app:

``` httpconf
    SetEnv bz2discord_config /path/to/bz2discord_config.json
    WSGIDaemonProcess bz2discord python-home=/path/to/bugzilla-discord-relay/venv
    WSGIScriptAlias /bugzilla /path/to/bugzilla-discord-relay/app.wsgi process-group=bz2discord application-group=%{GLOBAL}
    <Directory /path/to/bugzilla-discord-relay>
        Order allow,deny
        Allow from all
    </Directory>
```

You can put the config file wherever you want, as long as you update the SetEnv line above to match where you put it and your apache user can read it.

The WSGIScriptAlias line above points the /bugzilla path after the domain to point at the app. If it's the only thing you have on the domain, you can just leave it as / . Or change it to whatever path you want.

## Config file format

### Sample config

``` json
{
  "max_request_bytes": 262144,
  "discord_timeout_seconds": 10,
  "spool_enabled": false,
  "spool_max_file_bytes": 262144,
  "spool_max_files": 1000,
  "spool_max_total_bytes": 104857600,
  "spool_max_age_days": 14,
  "webhooks": {
    "{webhook id}": {
      "destination_webhook": "https://discord.com/api/webhooks/{random webhook code}",
      "source_baseurl": "https://yourbugzilla.tld",
      "api_key_header": "X-Bugzilla-Webhook-Key",
      "api_key_value": "current shared secret",
      "api_key_value_next": "optional next shared secret"
    }
  }
}
```

### Configuring Webhooks

`webhook_id` should be a UUID or similar. It's basically pretty arbitrary. Whatever you use for this would be placed after the url to your app deployment. For example, if your WSGIScriptAlias points at `/webhooks` then your webhook URL that you put in the config on GitHub for thr webhook will be: `https:/my.server.tld/webhooks/{webhook_id}`

`destination_webhook` needs to be the full URL assigned to the webhook by Discord when you set it up in the Discord config.

`source_baseurl` should contain the URL used as the "baseurl" for the Bugzilla the webhooks are coming from. For whatever reason, the payload of the webhook doesn't contain this anywhere, so this is needed to build the link to the bug that's included in the post to Discord. You should therefore assign a new webhook for any new Bugzilla you have pointed at it, so you can assign the baseurl to the correct Bugzilla.

`api_key_header` and `api_key_value` are required by the relay and are sent by Bugzilla as a static shared-secret header on each webhook request. The header should generally start with "X-" and the value can be whatever you want, as long as the header and value both match what you enter in the config in Bugzilla when you create the webhook. `api_key_value_next` is optional and can be used during secret rotation so the relay will accept either the current secret or the next secret while you cut over the sender.

### General Configuration

`max_request_bytes` is an optional limit for the size of a single inbound webhook request.

`discord_timeout_seconds` is an optional timeout (in seconds) for each outbound request to Discord.

The `spool_*` settings are optional limits for on-disk debug payload retention.
* `spool_enabled` controls whether the relay writes debug payloads to the spool directory. Payloads that produce an error or contain an unhandled event type get written to the spool directory if this is set, to allow you to debug them. If enabled, the rest of these settings help prevent it from filling up your disk if you forget to turn it off.
* `spool_max_file_bytes` caps the size of any one stored payload
* `spool_max_files` caps the number of stored payloads
* `spool_max_total_bytes` caps the total size of the spool directory
* `spool_max_age_days` removes older files before new ones are written.

All of the General Configuration settings default to the values shown in the sample config above, so you only need to include them in your config if you want to change a default. They can be set globally at the top level of the config file or within an individual webhook entry. A value set on a webhook overrides the global value for that webhook only, which is useful if production and staging share a config file but should keep different request or spool behavior, or if you only need to debug payloads coming from a specific sender or to a specific webhook.

When spooling is enabled, files are written with a payload-type prefix in the filename (`bugzilla-...` or `discord-...`) and include a metadata header block at the top of the file. The metadata includes non-secret context such as payload type, routing key, webhook ID, and relay path.

## Replaying spooled payloads

`debug_test.py` can replay both legacy spool files and the newer metadata-header spool files. Metadata headers are stripped automatically before replay.

Basic usage:

``` shell
./debug_test.py RELAY_URL PAYLOAD_FILE
```

Config-aware usage (recommended):

``` shell
./debug_test.py --relay-url https://relay.example --payload-file spool/bugzilla-... --config /path/to/bz2discord_config.json
```

With `--config`, the script loads `api_key_header` and `api_key_value` from the matching webhook entry in the relay config, so you do not need to pass auth headers manually. If the spool metadata includes a webhook ID, the script uses it automatically unless you provide `--webhook-id`.

If you are rotating secrets, `--use-next-secret` tells the script to use `api_key_value_next` when available.
