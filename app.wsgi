#! env python

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import sys
import os
import json
import uuid
import hmac
import hashlib
import pathlib
import datetime
from http.client import HTTPException
import requests
from discord_webhook import DiscordWebhook, DiscordEmbed

DEFAULT_MAX_REQUEST_BYTES = 262144
DEFAULT_SPOOL_MAX_FILE_BYTES = 262144
DEFAULT_SPOOL_MAX_FILES = 1000
DEFAULT_SPOOL_MAX_TOTAL_BYTES = 104857600
DEFAULT_SPOOL_MAX_AGE_DAYS = 14
DEFAULT_SPOOL_ENABLED = False
DEFAULT_DISCORD_TIMEOUT_SECONDS = 10.0

def error_log(environ, theError):
    print(theError, file=environ['wsgi.errors'])

def error500_response(environ, start_response, logerror, publicerror):
    status = '500 Internal Server Error'
    output = '<html><head><title>Internal Server Error</title></head>'
    output += '<h1>Internal Server Error</h1>'
    output += "<p>%s</p>" % publicerror
    output = bytes(output, encoding='utf-8')
    response_headers = [('Content-type', 'text/html, charset=utf-8'),
                        ('Content-Length', str(len(output)))]
    error_log(environ, logerror)
    start_response(status, response_headers)
    return [output]

def error401_response(environ, start_response, logerror, publicerror):
    status = '401 Unauthorized'
    output = '<html><head><title>Unauthorized</title></head>'
    output += '<h1>Unauthorized</h1>'
    output += "<p>%s</p>" % publicerror
    output = bytes(output, encoding='utf-8')
    response_headers = [('Content-type', 'text/html, charset=utf-8'),
                        ('Content-Length', str(len(output)))]
    error_log(environ, logerror)
    start_response(status, response_headers)
    return [output]

def error400_response(environ, start_response, logerror, publicerror):
    status = '400 Bad Request'
    output = '<html><head><title>Bad Request</title></head>'
    output += '<h1>Bad Request</h1>'
    output += "<p>%s</p>" % publicerror
    output = bytes(output, encoding='utf-8')
    response_headers = [('Content-type', 'text/html, charset=utf-8'),
                        ('Content-Length', str(len(output)))]
    error_log(environ, logerror)
    start_response(status, response_headers)
    return [output]

def error413_response(environ, start_response, logerror, publicerror):
    status = '413 Payload Too Large'
    output = '<html><head><title>Payload Too Large</title></head>'
    output += '<h1>Payload Too Large</h1>'
    output += "<p>%s</p>" % publicerror
    output = bytes(output, encoding='utf-8')
    response_headers = [('Content-type', 'text/html, charset=utf-8'),
                        ('Content-Length', str(len(output)))]
    error_log(environ, logerror)
    start_response(status, response_headers)
    return [output]

def error502_response(environ, start_response, logerror, publicerror):
    status = '502 Bad Gateway'
    output = '<html><head><title>Bad Gateway</title></head>'
    output += '<h1>Bad Gateway</h1>'
    output += "<p>%s</p>" % publicerror
    output = bytes(output, encoding='utf-8')
    response_headers = [('Content-type', 'text/html, charset=utf-8'),
                        ('Content-Length', str(len(output)))]
    error_log(environ, logerror)
    start_response(status, response_headers)
    return [output]

def config_int(config, key, default_value):
    value = config.get(key, default_value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default_value

def config_bool(config, key, default_value):
    value = config.get(key, default_value)
    if isinstance(value, bool):
        return value
    return default_value

def config_float(config, key, default_value):
    value = config.get(key, default_value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default_value

def effective_setting(config, webhook_config, key, default_value, converter=None):
    if key in webhook_config:
        value = webhook_config.get(key)
        source = {key: value}
    else:
        value = config.get(key, default_value)
        source = {key: value}

    if converter:
        return converter(source, key, default_value)
    return value

def spool_settings(config, webhook_config):
    return {
        'enabled': effective_setting(config, webhook_config, 'spool_enabled', DEFAULT_SPOOL_ENABLED, config_bool),
        'max_file_bytes': effective_setting(config, webhook_config, 'spool_max_file_bytes', DEFAULT_SPOOL_MAX_FILE_BYTES, config_int),
        'max_files': effective_setting(config, webhook_config, 'spool_max_files', DEFAULT_SPOOL_MAX_FILES, config_int),
        'max_total_bytes': effective_setting(config, webhook_config, 'spool_max_total_bytes', DEFAULT_SPOOL_MAX_TOTAL_BYTES, config_int),
        'max_age_days': effective_setting(config, webhook_config, 'spool_max_age_days', DEFAULT_SPOOL_MAX_AGE_DAYS, config_int),
    }

def prune_spool_directory(spooldir, settings):
    now = datetime.datetime.utcnow().timestamp()
    max_age_seconds = max(settings['max_age_days'], 0) * 86400
    files = []
    total_bytes = 0

    if not spooldir.exists():
        try:
            spooldir.mkdir(mode=0o700, exist_ok=True)
        except OSError as err:
            raise OSError("Unable to create spool directory '%s': %s" % (spooldir, err)) from err

    for entry in spooldir.iterdir():
        if not entry.is_file():
            continue
        stat_result = entry.stat()
        if max_age_seconds and (now - stat_result.st_mtime) > max_age_seconds:
            try:
                entry.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        files.append((entry, stat_result.st_mtime, stat_result.st_size))
        total_bytes += stat_result.st_size

    files.sort(key=lambda item: item[1])
    max_files = settings['max_files']
    max_total_bytes = settings['max_total_bytes']

    while files and ((max_files >= 0 and len(files) > max_files) or (max_total_bytes >= 0 and total_bytes > max_total_bytes)):
        path, _, size = files.pop(0)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # If we cannot prune this file, stop to avoid spinning forever.
            break
        total_bytes -= size

    return files, total_bytes

def save_payload_to_spool(environ, body, routing_key, config=None, webhook_config=None):
    # write what we received from Bugzilla to our spool directory for later debugging
    settings = spool_settings(config or {}, webhook_config or {})
    if not settings['enabled']:
        return

    body_size = len(body)
    if settings['max_file_bytes'] >= 0 and body_size > settings['max_file_bytes']:
        error_log(environ, "payload for '%s' not written to spool because it exceeds spool_max_file_bytes" % routing_key)
        return

    timestamp = datetime.datetime.utcnow()
    timestamp_string = timestamp.strftime("%Y%m%dT%H%M%SZ")
    uniq_string = uuid.uuid4()
    mydir = pathlib.Path(__file__).parent.resolve()
    spooldir = mydir / 'spool'
    try:
        files, total_bytes = prune_spool_directory(spooldir, settings)
    except OSError as err:
        error_log(environ, "payload for '%s' not written to spool because spool directory is not writable: %s" % (routing_key, err))
        return

    if settings['max_files'] == 0 or settings['max_total_bytes'] == 0:
        error_log(environ, "payload for '%s' not written to spool because spool limits disable writes" % routing_key)
        return

    if settings['max_files'] > 0 and len(files) >= settings['max_files']:
        error_log(environ, "payload for '%s' not written to spool because spool_max_files has been reached" % routing_key)
        return

    if settings['max_total_bytes'] > 0 and (total_bytes + body_size) > settings['max_total_bytes']:
        error_log(environ, "payload for '%s' not written to spool because spool_max_total_bytes would be exceeded" % routing_key)
        return

    filename = spooldir / ('%s-%s' % (timestamp_string, uniq_string))
    try:
        with open(filename, 'wb') as fd:
            fd.write(body)
        os.chmod(filename, 0o600)
    except OSError as err:
        error_log(environ, "payload for '%s' not written to spool because spool file write failed: %s" % (routing_key, err))
        return
    error_log(environ, "payload for '%s' written to %s" % (routing_key, filename))
    return

def request_content_length(environ):
    content_length = environ.get('CONTENT_LENGTH', '0')
    if content_length in (None, ''):
        return 0, None
    try:
        parsed = int(content_length)
    except ValueError:
        return None, 'Request Content-Length is invalid.'
    if parsed < 0:
        return None, 'Request Content-Length is invalid.'
    return parsed, None

def request_header_name_to_environ_key(header_name):
    return 'HTTP_' + header_name.upper().replace('-', '_')

def auth_value_matches(request_value, configured_value):
    if request_value is None or configured_value is None:
        return False
    # compare_digest gives us a constant-time equality check for a shared secret.
    return hmac.compare_digest(request_value, configured_value)

def request_is_authorized(environ, webhook_config):
    auth_header = webhook_config.get('api_key_header')
    auth_value = webhook_config.get('api_key_value')
    auth_value_next = webhook_config.get('api_key_value_next')

    if not auth_header or not auth_value:
        return False, "Webhook auth configuration is missing."

    request_value = environ.get(request_header_name_to_environ_key(auth_header))
    if auth_value_matches(request_value, auth_value):
        return True, None

    if auth_value_next and auth_value_matches(request_value, auth_value_next):
        return True, None

    return False, "Webhook auth header missing or invalid."

def validate_change_list(changes, change_list_name):
    if not isinstance(changes, list):
        return "%s must be a list." % change_list_name
    for change in changes:
        if not isinstance(change, dict):
            return "%s entries must be objects." % change_list_name
        for key in ('field', 'removed', 'added'):
            if key not in change:
                return "%s entries are missing required key '%s'." % (change_list_name, key)
    return None

def validate_payload_schema(bzdata):
    if not isinstance(bzdata, dict):
        return "Payload root object is invalid."

    event = bzdata.get('event')
    if not isinstance(event, dict):
        return "Payload missing required 'event' object."

    required_event_keys = ['user', 'target', 'action', 'routing_key']
    for required_key in required_event_keys:
        if required_key not in event:
            return "Payload event missing required key '%s'." % required_key

    user = event.get('user')
    if not isinstance(user, dict):
        return "Payload event user object is invalid."
    if 'login' not in user:
        return "Payload event user missing required key 'login'."

    target = event.get('target')
    action = event.get('action')
    if target not in ('bug', 'comment', 'attachment'):
        # Unknown targets are handled by the existing "Unhandled event type" path.
        return None

    bug = bzdata.get('bug')
    if not isinstance(bug, dict):
        return "Payload missing required 'bug' object."

    for required_key in ('id', 'is_private'):
        if required_key not in bug:
            return "Payload bug missing required key '%s'." % required_key

    if not bug.get('is_private'):
        for required_key in ('summary', 'status', 'assigned_to', 'product', 'component', 'last_change_time'):
            if required_key not in bug:
                return "Payload bug missing required key '%s'." % required_key

    if target == 'bug' and action == 'modify' and not bug.get('is_private'):
        change_error = validate_change_list(event.get('changes'), 'Payload event changes')
        if change_error:
            return change_error

    if target == 'comment' and action == 'create':
        comment = bug.get('comment')
        if not isinstance(comment, dict):
            return "Payload bug missing required 'comment' object."
        if 'is_private' not in comment:
            return "Payload comment missing required key 'is_private'."
        if not comment.get('is_private'):
            for required_key in ('body', 'number'):
                if required_key not in comment:
                    return "Payload comment missing required key '%s'." % required_key

    if target == 'attachment' and action in ('create', 'modify'):
        attachment = bug.get('attachment')
        if not isinstance(attachment, dict):
            return "Payload bug missing required 'attachment' object."
        if 'file_name' not in attachment:
            return "Payload attachment missing required key 'file_name'."
        if action == 'create':
            for required_key in ('description', 'content_type', 'id'):
                if required_key not in attachment:
                    return "Payload attachment missing required key '%s'." % required_key
        if action == 'modify':
            if 'description' not in attachment:
                return "Payload attachment missing required key 'description'."
            if 'changes' in event:
                change_error = validate_change_list(event.get('changes'), 'Payload event changes')
                if change_error:
                    return change_error

    return None

def application(environ, start_response):
    if 'bz2discord_config' not in environ:
        return error500_response(environ, start_response,
                "bz2discord_config not specified in Environment.",
                'Configuration error. See error log for details.')

    configfile = environ['bz2discord_config']
    config = {}
    if os.path.isfile(configfile) and os.access(configfile, os.R_OK):
        with open(configfile, 'r') as f:
            try:
                config = json.load(f)
            except json.JSONDecodeError as err:
                return error500_response(environ, start_response,
                    "config failed to load: %s" % err,
                    "Configuration error. See error log for details.")
    else:
        return error500_response(environ, start_response,
                "Configuration file '%s' not found or not readable." % configfile,
                "Configuration error. See error log for details.")

    if "webhooks" not in config:
        return error500_response(environ, start_response,
                "'webhooks' dict is missing from config file.",
                "Configuration error. See error log for details.")

    # PATH_INFO will start with a leading / so drop the first char
    webhook_id = environ["PATH_INFO"][1:]
    if webhook_id not in config["webhooks"]:
        return error401_response(environ, start_response,
                "Invalid webhook: %s" % webhook_id,
                "The webhook you specified does not exist.")

    webhook_config = config["webhooks"][webhook_id]
    authorized, auth_error = request_is_authorized(environ, webhook_config)
    if not authorized:
        error_log(environ, "Unauthorized webhook request for '%s': %s" % (webhook_id, auth_error))
        return error401_response(environ, start_response,
                auth_error,
                "The webhook you specified does not exist.")

    max_request_bytes = effective_setting(config, webhook_config, 'max_request_bytes', DEFAULT_MAX_REQUEST_BYTES, config_int)
    discord_timeout_seconds = effective_setting(
        config,
        webhook_config,
        'discord_timeout_seconds',
        DEFAULT_DISCORD_TIMEOUT_SECONDS,
        config_float,
    )
    content_length, content_length_error = request_content_length(environ)
    if content_length_error:
        return error400_response(environ, start_response,
                content_length_error,
                content_length_error)
    if max_request_bytes >= 0 and content_length > max_request_bytes:
        return error413_response(environ, start_response,
                "Payload exceeds max_request_bytes.",
                "Payload is too large.")

    input = environ['wsgi.input']
    body = input.read(content_length)
    bzdata = {}
    try:
        bzdata = json.loads(body)
    except json.JSONDecodeError:
        return error400_response(environ, start_response,
                "Payload data is not valid JSON",
                "Payload data is not valid JSON")

    validation_error = validate_payload_schema(bzdata)
    if validation_error:
        error_log(environ, validation_error)
        save_payload_to_spool(environ, body, 'invalid.schema', config, webhook_config)
        return error400_response(environ, start_response,
                validation_error,
                "Payload is missing required event data")

    baseurl = webhook_config['source_baseurl']

    def build_base_embed(event, bug, baseurl):
        embed = DiscordEmbed(title='Webhook Event Received',
                color='cccccc')
        embed.set_author(name=event['user']['real_name'] or event['user']['login'],
                icon_url="https://secure.gravatar.com/avatar/%s?d=mm&size=64" % hashlib.md5(event['user']['login'].encode('utf-8')).hexdigest())
        if bug:
            if bug['is_private']:
                bug['summary'] = 'Private Bug'
            title = "%s - %s" % (bug['id'], bug['summary'])
            if len(title) > 256:
                title = title[:256]
            embed.set_title(title)
            if bug['is_private']:
                embed.set_description("Private Bug - click through (with adequate permissions) to view details.")
            else:
                embed.set_description("%s (%s) in %s - %s. Last updated %s" % (bug['status'], bug['assigned_to'], bug['product'], bug['component'], bug['last_change_time']))
            embed.set_url("%s/show_bug.cgi?id=%s" % (baseurl, bug['id']))
        return embed

    # process the hook and deal with it properly
    webhook_url = webhook_config['destination_webhook']
    event = bzdata['event']
    embed = build_base_embed(event, {}, baseurl)
    embeds_to_send = [embed]
    bug = {}
    if "bug" in bzdata:
        bug = bzdata['bug']
        embed = build_base_embed(event, bug, baseurl)
        embeds_to_send = [embed]
    if event['target'] == 'bug':
        if event['action'] == 'modify':
            if bug['is_private']:
                embed.set_color('ffff00')
                embed.add_embed_field(name='Bug modified', value='Click through for details', inline=False)
            else:
                # Discord allows at most 25 embed fields. Each Bugzilla change
                # uses 3 fields, so limit each message chunk to 8 changes.
                changes = event['changes']
                chunk_size = 8
                embeds_to_send = []
                for i in range(0, len(changes), chunk_size):
                    change_chunk = changes[i:i + chunk_size]
                    chunk_embed = build_base_embed(event, bug, baseurl)
                    chunk_embed.set_color('ffff00')
                    for change in change_chunk:
                        chunk_embed.add_embed_field(name='━━━━━━━━━━', value='**Field Modified:** %s' % change['field'], inline=False)
                        chunk_embed.add_embed_field(name='Removed', value=change['removed'] or "_ _", inline=True)
                        chunk_embed.add_embed_field(name='Added', value=change['added'] or "_ _", inline=True)
                        if change['field'] == 'status' and change['added'] == 'RESOLVED':
                            chunk_embed.set_color('ff0000')
                    embeds_to_send.append(chunk_embed)
                if len(embeds_to_send) == 0:
                    embed.set_color('ffff00')
                    embed.add_embed_field(name='Bug modified', value='Click through for details', inline=False)
                    embeds_to_send = [embed]
        elif event['action'] == 'create':
            embed.set_color('00ff00')
            embed.add_embed_field(name='New bug filed with fields:', value=' ', inline=False)
            for field in bug:
                if field not in ['id','assigned_to','status','summary','last_change_time','creator','creation_time','classification','product','component'] and isinstance(bug[field], str) and bug[field] not in ['', '--', '---']:
                    embed.add_embed_field(name=field, value=bug[field], inline=True)
        else:
            embed.set_description("Unhandled bug action: %s" % event["action"])
            error_log(environ, "Unhandled bug action: $s" % event["action"])
            # write what we received from Bugzilla to our spool directory for later debugging
            save_payload_to_spool(environ, body, event['routing_key'], config, webhook_config)
    elif event['target'] == 'comment':
        if event['action'] == 'create':
            commentbody = ''
            if bug['comment']['is_private']:
                commentbody = "Private comment - click through (with adequate permissions) to view"
            else:
                commentbody = bug['comment']['body']
            if len(commentbody) > 1000:
                commentbody = commentbody[:1000]
                commentbody += "\n**[truncated]**"
            elif commentbody == '':
                # marking as duplicate and adding an attachment with no
                # description will transmit an empty comment, so just ignore
                # these when we get them.
                # Send a success response back to the caller and bail
                error_log(environ, 'Ignoring webhook for comment.create with empty comment body.')
                output = bytes('success\n', encoding="utf-8")
                response_headers = [('Content-type', 'text/plain, charset=utf-8'),
                                    ('Content-Length', str(len(output)))]
                start_response("200 OK", response_headers)
                return [output]
            if bug['comment']['is_private']:
                embed.add_embed_field(name="A private comment was added:", value=commentbody, inline=False)
            elif bug['comment']['number'] == 0:
                embed.set_color('00ff00')
                embed.add_embed_field(name='New bug filed with description:', value=commentbody, inline=False)
            else:
                embed.add_embed_field(name='Comment #%s added:' % bug['comment']['number'], value=commentbody, inline=False)
        else:
            embed.set_description("Unhandled comment action: %s" % event["action"])
            error_log(environ, "Unhandled comment action: $s" % event["action"])
            # write what we received from Bugzilla to our spool directory for later debugging
            save_payload_to_spool(environ, body, event['routing_key'], config, webhook_config)
    elif event['target'] == 'attachment':
        attachment = bug['attachment']
        if event['action'] == 'create':
            embed.set_color('ff00ff')
            embed.add_embed_field(name='Attachment added', value=attachment['file_name'], inline=False)
            embed.add_embed_field(name='Description', value=attachment['description'], inline=False)
            embed.add_embed_field(name='Content-Type', value=attachment['content_type'], inline=True)
            if attachment['content_type'][:6] == 'image/':
                embed.set_image('%s/attachment.cgi?id=%s' % (baseurl, attachment['id']))
        elif event['action'] == 'modify':
            embed.set_color('ffff00')
            embed.add_embed_field(name='Attachment modified', value=attachment['file_name'], inline=False)
            if attachment['description']:
                embed.add_embed_field(name='Description', value=attachment['description'], inline=False)
            changes = event.get('changes', [])
            for change in changes:
                field_name = change['field']
                if field_name.startswith('flag.'):
                    field_name = 'Flag: %s' % field_name.split('.', 1)[1]
                old_value = change['removed'] or '(unset)'
                new_value = change['added'] or '(unset)'
                embed.add_embed_field(name=field_name, value='%s -> %s' % (old_value, new_value), inline=False)
            if len(changes) == 0:
                embed.add_embed_field(name='Attachment modified', value='Click through for details', inline=False)
    else:
        embed.set_description("Unhandled event type")
        embed.add_embed_field(name="Event Type", value=event['routing_key'], inline=False)
        error_log(environ, "Unhandled event type: %s" % event['routing_key'])
        # write what we received from Bugzilla to our spool directory for later debugging
        save_payload_to_spool(environ, body, event['routing_key'], config, webhook_config)
    response = None
    webhook = None
    total_embeds = len(embeds_to_send)
    for idx, pending_embed in enumerate(embeds_to_send, start=1):
        webhook = DiscordWebhook(
            url=webhook_url,
            rate_limit_retry=True,
            timeout=discord_timeout_seconds,
        )
        webhook.add_embed(pending_embed)
        if total_embeds > 1:
            error_log(environ, "Forwarding webhook for %s to Discord! (%d/%d)" % (event['routing_key'], idx, total_embeds))
        else:
            error_log(environ, "Forwarding webhook for %s to Discord!" % event['routing_key'])
        try:
            response = webhook.execute()
        except (requests.RequestException, HTTPException, ValueError) as err:
            error_log(environ, "Discord webhook delivery failed: %s" % err)
            save_payload_to_spool(environ, body, event['routing_key'], config, webhook_config)
            return error502_response(environ, start_response,
                    "Discord webhook delivery failed: %s" % err,
                    "Unable to forward webhook to Discord.")
        if response.status_code != 200:
            break

    # Forward Discord's response back to the caller
    status = "%s %s" % (response.status_code, response.reason)
    if "transfer-encoding" in response.headers:
        del response.headers['Transfer-Encoding']
    if "content-encoding" in response.headers:
        del response.headers['Content-Encoding']
    if response.status_code != 200:
        error_log(environ, status)
        error_log(environ, response.content)
        # write what we received from Bugzilla to our spool directory for later debugging
        save_payload_to_spool(environ, body, event['routing_key'], config, webhook_config)
        # and what we tried to send to Discord
        save_payload_to_spool(environ, bytes(json.dumps(webhook.json),"utf-8"), 'Discord Webhook Payload', config, webhook_config)

    start_response(status, list(response.headers.items()))
    return [response.content]

    # Send a success response back to the caller
    #output = bytes('success\n', encoding="utf-8")
    #response_headers = [('Content-type', 'text/plain, charset=utf-8'),
    #                    ('Content-Length', str(len(output)))]
    #start_response("200 OK", response_headers)
    #return [output]
