#! env python

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import sys
import os
import json
import uuid
import hashlib
import pathlib
import datetime
import requests
from discord_webhook import DiscordWebhook, DiscordEmbed

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

def save_payload_to_spool(environ, body, routing_key):
    # write what we received from Bugzilla to our spool directory for later debugging
    timestamp = datetime.datetime.utcnow()
    timestamp_string = timestamp.strftime("%Y%m%dT%H%M%SZ")
    uniq_string = uuid.uuid4()
    mydir = pathlib.Path(__file__).parent.resolve()
    filename = "%s/spool/%s-%s" % (mydir, timestamp_string, uniq_string)
    fd = open(filename, "wb");
    fd.write(body)
    fd.close()
    error_log(environ, "payload for '%s' written to %s" % (routing_key, filename))
    return

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
            except json.JSONDecodeError(msg, doc, pos):
                return error500_response(environ, start_response,
                    "config failed to load: %s" % msg,
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

    input = environ['wsgi.input']
    body = input.read(int(environ.get('CONTENT_LENGTH', '0')))
    bzdata = {}
    try:
        bzdata = json.loads(body)
    except json.JSONDecodeError:
        return error400_response(environ, start_response,
                "Payload data is not valid JSON",
                "Payload data is not valid JSON")

    if 'event' not in bzdata or not isinstance(bzdata['event'], dict):
        error_log(environ, "Payload missing required 'event' object")
        save_payload_to_spool(environ, body, 'invalid.missing_event')
        return error400_response(environ, start_response,
                "Payload missing required 'event' object",
                "Payload is missing required event data")

    required_event_keys = ['user', 'target', 'action', 'routing_key']
    for required_key in required_event_keys:
        if required_key not in bzdata['event']:
            error_log(environ, "Payload event missing required key '%s'" % required_key)
            save_payload_to_spool(environ, body, 'invalid.missing_event_key')
            return error400_response(environ, start_response,
                    "Payload event missing required key '%s'" % required_key,
                    "Payload is missing required event data")

    if 'login' not in bzdata['event']['user']:
        error_log(environ, "Payload event user missing required key 'login'")
        save_payload_to_spool(environ, body, 'invalid.missing_event_user_login')
        return error400_response(environ, start_response,
                "Payload event user missing required key 'login'",
                "Payload is missing required event user data")

    baseurl = config['webhooks'][webhook_id]['source_baseurl']

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
    webhook_url = config['webhooks'][webhook_id]['destination_webhook']
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
            save_payload_to_spool(environ, body, event['routing_key'])
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
            save_payload_to_spool(environ, body, event['routing_key'])
    elif event['target'] == 'attachment':
        attachment = bug['attachment']
        if event['action'] == 'create':
            embed.set_color('ff00ff')
            embed.add_embed_field(name='Attachment added', value=attachment['file_name'], inline=False)
            embed.add_embed_field(name='Description', value=attachment['description'], inline=False)
            embed.add_embed_field(name='Content-Type', value=attachment['content_type'], inline=True)
            if attachment['content_type'][:6] == 'image/':
                embed.set_image('%s/attachment.cgi?id=%s' % (baseurl, attachment['id']))
    else:
        embed.set_description("Unhandled event type")
        embed.add_embed_field(name="Event Type", value=event['routing_key'], inline=False)
        error_log(environ, "Unhandled event type: %s" % event['routing_key'])
        # write what we received from Bugzilla to our spool directory for later debugging
        save_payload_to_spool(environ, body, event['routing_key'])
    response = None
    webhook = None
    total_embeds = len(embeds_to_send)
    for idx, pending_embed in enumerate(embeds_to_send, start=1):
        webhook = DiscordWebhook(url=webhook_url, rate_limit_retry=True)
        webhook.add_embed(pending_embed)
        if total_embeds > 1:
            error_log(environ, "Forwarding webhook for %s to Discord! (%d/%d)" % (event['routing_key'], idx, total_embeds))
        else:
            error_log(environ, "Forwarding webhook for %s to Discord!" % event['routing_key'])
        response = webhook.execute()
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
        save_payload_to_spool(environ, body, event['routing_key'])
        # and what we tried to send to Discord
        save_payload_to_spool(environ, bytes(json.dumps(webhook.json),"utf-8"), 'Discord Webhook Payload')

    start_response(status, list(response.headers.items()))
    return [response.content]

    # Send a success response back to the caller
    #output = bytes('success\n', encoding="utf-8")
    #response_headers = [('Content-type', 'text/plain, charset=utf-8'),
    #                    ('Content-Length', str(len(output)))]
    #start_response("200 OK", response_headers)
    #return [output]
