#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
from utils.logging import log
import requests
import random
from urllib.parse import urlparse, urlunparse
import json
import os
from pyquery import PyQuery as pq
import time
import threading
import tqdm

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/63.0.3239.133 Safari/537.39'
DL_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/63.0.{:04d}.{:03d} Safari/537.39'
MOBILE_UA = 'Mozilla/5.0 (Linux; Android 5.1.1; SM-G9350 Build/LMY48Z) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/45.0.2454.94 Mobile Safari/537.36'
WEBAPI_HOST = 'https://webapi.ctfile.com'
GET_DIR_URL = WEBAPI_HOST + '/getdir.php'
GET_FILE_URL1 = WEBAPI_HOST + '/getfile.php'
GET_FILE_URL2 = WEBAPI_HOST + '/get_file_url.php'
TEMP_DIR = 'temp'
DOWNLOAD_DIR = 'download'

DL_ERROR_FILELINKTIMEOUT = '下载链接已超时，请重新从文件夹获取。'
DL_Threads_cnt = 16

def requests_debug(r, prefix=''):
    log.debug('{}requests:{}'.format(prefix, r.request.url))
    log.debug('{}response:{}'.format(prefix, r.text))

class CtFile():
    def __init__(self,
                 url,
                 session=None,
                 folder=""):
        self.s = session if session else requests.session()
        self.url = url
        self.urlparsed = urlparse(self.url)
        self.folder = folder

    def dl(self):
        log.info('download {}'.format(self.url))

        # step 1
        headers = {
            'origin': self.urlparsed.netloc,
        }

        # parameters
        params = {
            'f' : self.url.split('/')[-1],
            'passcode' : '',
            'r' : str(random.random()),
            'ref' : '',
        }
        r = self.s.get(GET_FILE_URL1, params=params, headers=headers)
        j = json.loads(r.text)
        log.debug('step 1')
        requests_debug(r)

        # link error handler
        if j.get('code') == 404:
            log.error('dl_file error: {}'.format(j.get('message')))
            if j.get('message') == DL_ERROR_FILELINKTIMEOUT:
                log.error('need get dir list again')
            return False, j.get('message')

        fn = j['file_name']

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
        while True:
            r = self.s.get(GET_FILE_URL2, params=params, headers=headers)
            j = json.loads(r.text)
            log.debug('step 2')
            requests_debug(r)
            if j.get('code') == 503:
                params['rd'] = str(random.random())
            else:
                break

        # create an empty file
        filename = os.path.join(DOWNLOAD_DIR, self.folder, fn)
        filesize = int(j['file_size'])
        log.debug('create empty file {} size {}'.format(filename, filesize))
        with open(filename, 'wb') as fd:
            fd.truncate(filesize)

        #donwload with thread
        threads = []
        for i in range(DL_Threads_cnt):
            start = i * filesize // DL_Threads_cnt
            end = (i + 1) * filesize // DL_Threads_cnt - 1 if i != DL_Threads_cnt - 1 else filesize

            t = DL_Thread(i,
                          j['downurl'].replace(r'\/', r'/'),
                          params,
                          headers,
                          filename,
                          start,
                          end)
            log.debug('dl-{:03d} download range start={} end={}'.format(i+1, start, end))

            threads.append(t)
            t.start()
            #time.sleep(1)

        progressbar = tqdm.tqdm(total=filesize, desc=filename, ascii=' #', unit="B", unit_scale=True, unit_divisor=1024)
        downloaded_bytes = 0
        last_downloaded_bytes = 0
        while downloaded_bytes < filesize:
            downloaded_bytes = 0
            for t in threads:
                downloaded_bytes += t.downloaded_bytes()
            progressbar.update(downloaded_bytes-last_downloaded_bytes)
            last_downloaded_bytes = downloaded_bytes
            log.debug("{} {}".format(downloaded_bytes, filesize))
            time.sleep(1)

        log.debug('quit')
        for i in range(DL_Threads_cnt):
            threads[i].join()

        return True, None

class DL_Thread(threading.Thread):
    def __init__(self, i, url, params, headers, filename, start, end):
        super().__init__()
        self._params = params
        self._index = i
        self._url = url
        self._headers = headers
        self._filename = filename
        self._start = start
        self._end = end
        self._UA = DL_UA.format(random.randrange(9999), self._index + 1)
        self._s = requests.session()
        self._downloaded_bytes = 0

        self._headers['user-agent'] = self._UA

    def run(self):
        # step 2
        while True:
            while True:
                self._params['rd'] = str(random.random())
                r = self._s.get(GET_FILE_URL2, params=self._params, headers=self._headers)
                j = json.loads(r.text)
                log.debug('dl-{:03d} step 2'.format(self._index + 1))
                requests_debug(r, 'dl-{:03d} '.format(self._index + 1))
                if j['code'] == 503 and j['message'] == 'require for verifycode':
                    log.debug('dl-{:03d} retry'.format(self._index + 1))
                    self._UA = DL_UA.format(random.randrange(9999), self._index + 1)
                    self._headers['user-agent'] = self._UA
                else:
                    break

            with open(self._filename, 'r+b') as f:
                f.seek(self._start)
                self._headers['Range'] = 'bytes={}-{}'.format(self._start, self._end)
                r = self._s.get(j['downurl'].replace(r'\/', r'/'), headers=self._headers, stream=True)
                log.debug('dl-{:03d} download file request: {}'.format(self._index+1, r.status_code))
                if r.status_code == 503:
                    log.warning('dl-{:03d} download fail, retry'.format(self._index + 1))
                    self._headers['user-agent'] = DL_UA.format(random.randrange(9999), self._index + 1)
                    time.sleep(1)
                else:
                    for chunk in r.iter_content(chunk_size=128):
                        f.write(chunk)
                        self._downloaded_bytes += len(chunk)
                    log.debug('dl-{:03d} exit'.format(self._index + 1))
                    break



    def downloaded_bytes(self):
        return self._downloaded_bytes

class CtDir():
    def __init__(self, args):
        self.args = args
        self.url = args.dir
        self.urlparsed = urlparse(self.url)
        self.url_host = self.urlparsed.netloc
        # remove the last '/' if exist
        path_split = self.urlparsed.path.split('/') 
        self.url_id = path_split[-1] if path_split[-1] else path_split[-2]
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
            requests_debug(r)
            if j.get('code') == 404 or j.get('code') == 503:
                log.error('dl_dir_list error: {}, {}'.format(self.url, j.get('message')))

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

        for i in web['aaData'][24:25]:
            p = pq(i[0])
            fid = p('input').attr('value')
            p = pq(i[1])
            fn = p('a').text()
            url = p('a').attr('href')
            if loc.get(fid) and loc[fid]:
                log.info('file {} {} downloaded, skip it'.format(fid, fn))
            else:
                l = list(self.urlparsed)
                l[3] = l[4] = l[5] = ""
                l[2] = url
                ct_file = CtFile(urlunparse(l), self.s, loc['name'])
                success, error = ct_file.dl()
                if success:
                    loc[fid] = True
                    self.save_status()
                else:
                    return success, error

        return True, None

def main():
    parser = argparse.ArgumentParser(description='Download from CTDisk.')
    parser.add_argument('-d', '--dir', help='download a directory')
    parser.add_argument('-f', '--file', help='download a file' )
    args = parser.parse_args()

    if args.dir:
        stop = False
        get_dir_from_web = False
        error = None
        ct_dir = CtDir(args)
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
    elif args.file:
        ct_file = CtFile(url=args.file)
        ct_file.dl()
        log.info('download finished')

if __name__ == "__main__":
    main()
