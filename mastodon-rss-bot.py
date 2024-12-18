import os
import sys
import re

import sqlite3
from datetime import datetime, timedelta
import base64
import hashlib

import feedparser
from mastodon import Mastodon
import requests
import urllib.request
from bs4 import BeautifulSoup

import text_replacements
import dynamic_tags

from fake_useragent import UserAgent

sql = sqlite3.connect('/data/cache.db')
db = sql.cursor()
db.execute('''CREATE TABLE IF NOT EXISTS entries (feed_entry_id text, toot_id text, rss_feed_url text, mastodon_instance text)''')

include_author = False
include_link = True
include_link_thumbnail = os.environ.get('INCLUDE_LINK_THUMBNAIL') != 'FALSE'
include_images = os.environ.get('INCLUDE_IMAGES') != 'FALSE'
include_description = os.environ.get('INCLUDE_DESCRIPTION') == 'TRUE'
toot_language = os.environ.get('LANGUAGE', 'de')
use_privacy_frontends = True
use_shortlink = True
maximum_toots_count = int(os.environ.get('MAXIMUM_TOOTS_COUNT', '1'))

rss_feed_url = os.environ['RSS_FEED_URL']
mastodon_instance = os.environ['MASTODON_INSTANCE']
tags_to_add = os.environ['TAGS_TO_ADD']
days_to_check = int(os.environ['DAYS_TO_CHECK'])
client_id = os.environ['CLIENT_ID']
client_secret = os.environ['CLIENT_SECRET']
access_token = os.environ['ACCESS_TOKEN']
ignore_images = os.environ.get('IGNORE_IMAGES', '')

rss_feed_domain = re.sub('^[a-z]*://', '', rss_feed_url)
rss_feed_domain = re.sub('/.*$', '', rss_feed_domain)

try:
    mastodon_api = Mastodon(
        client_id = client_id,
        client_secret = client_secret,
        access_token = access_token,
        api_base_url = 'https://' + mastodon_instance
    )
except:
    print("ERROR: Failed to log into " + mastodon_instance)
    sys.exit(1)

feed = feedparser.parse(rss_feed_url)
print('Retrieved ' + str(len(feed.entries)) + ' feed entries!')

toots_count = 0
for feed_entry in reversed(feed.entries):
    if id in feed_entry:
        feed_entry_id = feed_entry.id
    elif len(feed_entry.link) > 0:
        feed_entry_id = feed_entry.link
    elif len(feed_entry.title) > 0:
        feed_entry_id = feed_entry.title
    elif 'published_parsed' in feed_entry:
        feed_entry_id = str(feed_entry.published_parsed)
    else:
        feed_entry_id = str(feed_entry.updated_parsed)
    feed_entry_id = hashlib.md5(feed_entry_id.encode()).hexdigest()

    print('Entry found: ' + feed_entry_id)

    db.execute(
        'SELECT * FROM entries WHERE feed_entry_id = ? AND mastodon_instance = ?',
        (feed_entry_id, mastodon_instance))
    last = db.fetchone()

    if 'published_parsed' in feed_entry:
        feed_entry_date_raw = feed_entry.published_parsed
    else:
        feed_entry_date_raw = feed_entry.updated_parsed

    feed_entry_date = datetime(
            feed_entry_date_raw.tm_year, feed_entry_date_raw.tm_mon, feed_entry_date_raw.tm_mday,
            feed_entry_date_raw.tm_hour, feed_entry_date_raw.tm_min, feed_entry_date_raw.tm_sec)
    feed_entry_age = datetime.now() - feed_entry_date

    print(' > Date = ' + str(feed_entry_date))
    print(' > Age = ' + str(feed_entry_age))

    if last is None and feed_entry_age < timedelta(days = days_to_check):
        print(' > Processing...')
        linked_page = None

        if feed_entry.link is not None:
            try:
                print(' > Retrieving the linked page: ' + feed_entry.link)
                user_agent = UserAgent().firefox
                headers = {'User-Agent': user_agent}
                linked_page_request = urllib.request.Request(feed_entry.link, headers=headers)
                linked_page_response = urllib.request.urlopen(linked_page_request).read()
                linked_page_response = linked_page_response.decode('UTF-8')
                linked_page = BeautifulSoup(linked_page_response, 'lxml')
            except Exception as ex:
                print('   > FAILURE!', ex)

        if 'twitter.com' in rss_feed_url or '/twitter/' in rss_feed_url:
            feed_entry_title = feed_entry.description
            feed_entry_title = re.sub('<[^>]*>', '', feed_entry_title)
        elif feed_entry.title is not None and len(feed_entry.title) > 0:
            feed_entry_title = feed_entry.title
        elif linked_page is not None:
            feed_entry_title = linked_page.find('title')
            feed_entry_title = None

            if feed_entry_title is None:
                feed_entry_title = linked_page.find('meta', property='og:title')

            if feed_entry_title is not None:
                feed_entry_title = str(feed_entry_title)
            else:
                feed_entry_title = ''

            feed_entry_title = re.sub('<[/]*title>', '', feed_entry_title)
            feed_entry_title = re.sub('<meta content=[\"\']', '', feed_entry_title)
            feed_entry_title = re.sub('[\"\'] property.*$', '', feed_entry_title)
            feed_entry_title = re.sub(' [\\|-] .*$', '', feed_entry_title)

        if '(P)' in feed_entry_title:
            print(' > This article is an ad! Skipping...')
            continue

        if re.match(r"^[ ]*- [A-Z][a-z]*$", feed_entry_title):
            print(' > The title is missing!')
            raise ValueError('The title is missing')

        toot_body = text_replacements.apply(feed_entry_title, toot_language)

        media_urls = []
        media_urls_posted = []

        if 'summary' in feed_entry:
            for p in re.finditer(r"https://pbs.twimg.com/[^ \xa0\"]*", feed_entry.summary):
                twitter_media_url = p.group(0)
                #print(' > Found Twitter media: ' + twitter_media_url)

                nitter_media_url = twitter_media_url.replace('https://pbs.twimg.com/media/', 'https://nitter.unixfox.eu/pic/media%2F')
                nitter_media_url = nitter_media_url.replace('?format=', '.')
                nitter_media_url = re.sub('&amp;name=[^&]*', '', nitter_media_url)

                #print('   > Resulting Nitter media URL = ' + nitter_media_url)
                media_urls.append(nitter_media_url)

            for p in re.finditer(r"https://i.redd.it/[a-zA-Z0-9]*.(gif|jpg|mp4|png|webp)", feed_entry.summary):
                media_url = p.group(0)
                #print(' > Found Reddit media: ' + media_url)
                media_urls.append(media_url)

        if include_link_thumbnail and media_urls is not None and linked_page is not None:
            thumbnail_url = str(linked_page.find('meta', property='og:image'))
            thumbnail_url = thumbnail_url.replace('<meta content=\"', '')
            thumbnail_url = re.sub('\".*', '', thumbnail_url)

            #print(' > Found link thumbnail media: ' + thumbnail_url)
            media_urls.append(thumbnail_url)

        toot_media = []
        for media_url in media_urls:
            if media_url is None or media_url == 'None' or media_urls_posted.count(media_url) > 0:
                continue

            if not include_images:
                continue

            if media_url in ignore_images.split(' '):
                continue
                
            try:
                print (' > Uploading media to Mastodon: ' + media_url)
                user_agent = UserAgent().firefox
                headers = {'User-Agent': user_agent}
                media = requests.get(media_url, headers=headers)
                media_posted = mastodon_api.media_post(
                    media.content,
                    mime_type = media.headers.get('content-type'))
                toot_media.append(media_posted['id'])
                media_urls_posted.append(media_url)
            except Exception as e:
                print('   > FAILURE!')
                print(e)

        for link in feed_entry.links:
            if 'image' in link.type:
                if not include_images:
                    continue

                if media_urls_posted.count(link.href) > 0:
                    continue

                if link.href in ignore_images.split(' '):
                    continue
                    
                try:
                    media = requests.get(link.href)
                    media_posted = mastodon_api.media_post(
                        media.content,
                        mime_type = link.type)
                    toot_media.append(media_posted['id'])
                    media_urls_posted.append(link.href)
                except:
                    print(' > Could not upload to Mastodon!')

        if include_description and len(feed_entry.description) > 0:
            toot_body += '\n\n' + feed_entry.description

        if include_link:
            feed_entry_link = feed_entry.link

            if use_shortlink and linked_page is not None:
                feed_entry_shortlink_node = linked_page.find('link', rel='shortlink')
                if feed_entry_shortlink_node is not None:
                    feed_entry_link = str(feed_entry_shortlink_node).replace('<link href=\"', '')
                    feed_entry_link = re.sub('\".*', '', feed_entry_link)

            if use_privacy_frontends:
                feed_entry_link = feed_entry_link.replace('old.reddit.com', 'reddit.com')
                feed_entry_link = feed_entry_link.replace('reddit.com', 'libreddit.kavin.rocks')
                feed_entry_link = feed_entry_link.replace('twitter.com', 'nitter.net')
                feed_entry_link = feed_entry_link.replace('youtube.com', 'yewtu.be')

            feed_entry_link = feed_entry_link.replace('www.', '')
            feed_entry_link = re.sub('\\?utm.*$', '', feed_entry_link)
            feed_entry_link = re.sub('/$', '', feed_entry_link)

            toot_body += '\n\n🔗 ' + feed_entry_link

        if include_author and 'authors' in feed_entry:
            toot_body += '\nby ' + feed_entry.authors[0].name

        all_tags_to_add = ''
        dynamic_tags_to_add = dynamic_tags.get(toot_body, toot_language)

        if tags_to_add: all_tags_to_add += ' ' + tags_to_add
        if dynamic_tags_to_add: all_tags_to_add += ' ' + dynamic_tags_to_add

        if all_tags_to_add != '':
            filtered_tags_to_add = ''

            for tag in all_tags_to_add.split(' '):
                if '#' not in tag:
                    filtered_tags_to_add += ' ' + tag
                    continue

                if (tag.lower() not in toot_body.lower() and
                    tag.lower() not in filtered_tags_to_add.lower()):
                    filtered_tags_to_add += ' ' + tag

            toot_body += '\n\n' + filtered_tags_to_add.lstrip().rstrip()

        if len(toot_body) > 500:
            toot_body = toot_body.replace(' și ', ' & ');

        toot_body = re.sub('[#][#]*', '#', toot_body);
        print(toot_body)
        toot_body = toot_body[:500];

        if toot_media is not None:
            toot = mastodon_api.status_post(
                toot_body,
                in_reply_to_id = None,
                media_ids = toot_media,
                sensitive = False,
                visibility = 'public',
                spoiler_text = None,
                language = toot_language)

            if "id" in toot:
                db.execute("INSERT INTO entries VALUES ( ? , ? , ? , ? )",
                        (feed_entry_id, toot["id"], rss_feed_url, mastodon_instance))
                sql.commit()

        toots_count += 1
        if toots_count == maximum_toots_count:
            print('Exiting... Reached the maximum number of toots per run!')
            break
