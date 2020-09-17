#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from utils.logging import log
import requests
import random
from urllib.parse import urlparse, urlunparse
import json
import os
from pyquery import PyQuery as pq
import time

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/63.0.3239.133 Safari/537.36'
WEBAPI_HOST = 'https://webapi.ctfile.com'
GET_DIR_URL = WEBAPI_HOST + '/getdir.php'
GET_FILE_URL1 = WEBAPI_HOST + '/getfile.php'
GET_FILE_URL2 = WEBAPI_HOST + '/get_file_url.php'
TEMP_DIR = 'temp'
DOWNLOAD_DIR = 'download'

DL_ERROR_FILELINKTIMEOUT = '下载链接已超时，请重新从文件夹获取。'

class CtDir():
    def __init__(self, url):
        self.url = url
        self.urlparsed = urlparse(url)
        self.url_host = self.urlparsed.netloc
        self.url_id = self.urlparsed.path.split('/')[-1]
        self.status_file = os.path.join(TEMP_DIR, self.url_id)
        self.s = requests.session()
        self.s.headers = {'user-agent': UA}
        # init self.status
        if self.status_exist():
            log.debug('read dir status')
            self.load_status()
        else:
            log.debug('init dir status')
            self.init_status()

    def save_status(self):
        with open(self.status_file, 'w') as f:
            f.write(json.dumps(self.status))

    def load_status(self):
        with open(self.status_file) as f:
            self.status = json.loads(f.read())

    def status_exist(self):
        return os.path.isfile(self.status_file)

    def init_status(self):
        self.status = {
            'loc': {},
            'web': {},
        }
        with open(self.status_file, 'w') as f:
            f.write(json.dumps(self.status))

    def get_dir_list(self, get_dir_from_web):
        log.info('Get list for {}'.format(self.url))
        if get_dir_from_web or not self.status.get('web'):
            loc = self.status['loc']
            log.info('get list from website')
            headers = {
                'origin': self.urlparsed.netloc
            }
            #parameters
            params = {
                'd' : self.urlparsed.path.split('/')[-1],
                'folder_id' : '',
                'passcode' : '',
                'r' : str(random.random()),
                'ref' : '',
            }
            r = self.s.get(GET_DIR_URL, params=params, headers=headers)
            j = json.loads(r.text.encode().decode('utf-8-sig'))
            loc['name'] = j['folder_name']
            loc['url'] = j['url'] #real url
            log.info('folder name: {}'.format(loc['name']))
            log.info('folder url: {}'.format(loc['url']))

            r = self.s.get(WEBAPI_HOST + loc['url'])
            self.status['web'] = json.loads(r.text)

            self.save_status()

    def dl_dir(self):
        loc = self.status['loc']
        web = self.status['web']
        if not os.path.exists(os.path.join(DOWNLOAD_DIR, loc['name'])):
            os.mkdir(os.path.join(DOWNLOAD_DIR, loc['name']))

        for i in web['aaData']:
            p = pq(i[0])
            fid = p('input').attr('value')
            p = pq(i[1])
            fn = p('a').text()
            url = p('a').attr('href')
            if loc.get(fid) and loc[fid]:
                log.info('file {} {} downloaded, skip it'.format(fid, fn))
            else:
                success, error = self.dl_file(url, fn)
                if success:
                    loc[fid] = True
                    self.save_status()
                else:
                    return success, error

        return True, None

    def dl_file(self, url, fn):
        loc = self.status['loc']
        log.info('download {} {}'.format(fn, url))

        # step 1
        headers = {
            'origin': self.urlparsed.netloc,
        }
        # parameters
        params = {
            'f' : url.split('/')[-1],
            'passcode' : '',
            'r' : str(random.random()),
            'ref' : '',
        }
        r = self.s.get(GET_FILE_URL1, params=params, headers=headers)
        j = json.loads(r.text)
        log.debug('step 1 r={}'.format(json.dumps(j)))

        # link error handler
        if j.get('code') == 404:
            log.error('dl_file error: {}, {}'.format(url, j.get('message')))
            if j.get('message') == DL_ERROR_FILELINKTIMEOUT:
                log.error('need get dir list again')
            return False, j.get('message')

        # step 2
        params = {
            'uid': j['userid'],
            'fid': j['file_id'],
            'folder_id': 0,
            'file_chk': j['file_chk'],
            'mb': 0,
            'app': 0,
            'acheck': 1,
            'verifycode': '',
            'rd': str(random.random())
        }
        r = self.s.get(GET_FILE_URL2, params=params, headers=headers)
        j = json.loads(r.text)
        log.debug('step 2 r={}'.format(json.dumps(j)))

        # step3
        r = self.s.get(j['downurl'].replace(r'\/', r'/'), headers=headers, stream=True)
        with open(os.path.join(DOWNLOAD_DIR, loc['name'], fn), 'wb') as fd:
            for chunk in r.iter_content(chunk_size=1024*1024):
                fd.write(chunk)

        return True, None


def main():
    stop = False
    get_dir_from_web = False
    error = None
    ct_dir = CtDir("https://n802.com/dir/11449240-27299530-05ba1e")
    while not stop:
        if error == DL_ERROR_FILELINKTIMEOUT:
            get_dir_from_web = True
        else:
            get_dir_from_web = False
        ct_dir.get_dir_list(get_dir_from_web)
        success, error = ct_dir.dl_dir()
        if success:
            stop = True
            log.info('download finished')

if __name__ == "__main__":
    main()
