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

Example config:
``` json
{
  "webhooks": {
    "{webhook id}": {
      "destination_webhook": "https://discord.com/api/webhooks/{random webhook code}",
      "source_baseurl": "https://yourbugzilla.tld"
    }
  }
}
```

`webhook_id` should be a UUID or similar. It's basically pretty arbitrary. Whatever you use for this would be placed after the url to your app deployment. For example, if your WSGIScriptAlias points at `/webhooks` then your webhook URL that you put in the config on GitHub for thr webhook will be: `https:/my.server.tld/webhooks/{webhook_id}`

`destination_webhook` needs to be the full URL assigned to the webhook by Discord when you set it up in the Discord config.

`source_baseurl` should contain the URL used as the "baseurl" for the Bugzilla the webhooks are coming from. For whatever reason, the payload of the webhook doesn't contain this anywhere, so this is needed to build the link to the bug that's included in the post to Discord. You should therefore assign a new webhook for any new Bugzilla you have pointed at it, so you can assign the baseurl to the correct Bugzilla.
