#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from utils.logging import log
import requests
import random
from urllib.parse import urlparse, urlunparse
import json
import os
from pyquery import PyQuery as pq

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/63.0.3239.132 Safari/537.36'
WEBAPI_HOST = 'https://webapi.ctfile.com'
GET_DIR_URL = WEBAPI_HOST + '/getdir.php'
GET_FILE_URL1 = WEBAPI_HOST + '/getfile.php'
GET_FILE_URL2 = WEBAPI_HOST + '/get_file_url.php'
TEMP_DIR = 'temp'

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
        if(os.path.exists(self.status_file)):
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

    def init_status(self):
        self.status = {
            'list_finish' : False,
        }
        with open(self.status_file, 'w') as f:
            f.write(json.dumps(self.status))

    def get_dir_list(self):
        log.info('Get list for {}'.format(self.url))
        if self.status['list_finish']:
            log.info('get list from status file')
            self.dir_list = self.status['dir_list']
            self.folder_url = self.status['folder_url']
            self.folder_name = self.status['folder_name']
        else:
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
            self.folder_name = j['folder_name']
            self.folder_url = j['url'] #real url
            log.info('folder name: {}'.format(self.folder_name))
            log.info('folder url: {}'.format(self.folder_url))

            r = self.s.get(WEBAPI_HOST + self.folder_url)
            self.dir_list = json.loads(r.text)

            self.status['list_finish'] = True
            self.status['folder_url'] = self.folder_url
            self.status['folder_name'] = self.folder_name
            self.status['dir_list'] = self.dir_list
            self.save_status()

    def dl_all(self):
        if not os.path.exists(self.folder_name):
            os.mkdir(self.folder_name)

        for i in self.dir_list['aaData']:
            self.dl_file(i[1])

    def dl_file(self, aaData):
        d = pq(aaData)
        url = d('a').attr('href')
        log.debug('download {}'.format(url))

        # step 1
        headers = {
            'origin': self.urlparsed.netloc,
        }
        # parameters
        params = {
            'f' : d('a').attr('href').split('/')[-1],
            'passcode' : '',
            'r' : str(random.random()),
            'ref' : '',
        }
        r = self.s.get(GET_FILE_URL1, params=params, headers=headers)
        j = json.loads(r.text)
        log.debug('step 1 r={}'.format(json.dumps(j)))
        fn = j['file_name']
        log.info('download {}'.format(fn))

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
        with open(os.path.join(self.folder_name, fn), 'wb') as fd:
            for chunk in r.iter_content(chunk_size=1024*1024):
                fd.write(chunk)



def main():
    ct_dir = CtDir("https://n802.com/dir/11449240-27299530-05ba1e")
    ct_dir.get_dir_list()
    ct_dir.dl_all()

if __name__ == "__main__":
    main()
