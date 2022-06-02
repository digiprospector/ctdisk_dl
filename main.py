#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from enum import Enum
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
import queue

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/63.0.3239.133 Safari/537.39'
DYN_UA_FORMAT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/63.0.{:04d}.{:03d} Safari/537.39'

WEBAPI_HOST = 'https://webapi.ctfile.com'
GET_DIR_URL = WEBAPI_HOST + '/getdir.php'
GET_FILE_URL1 = WEBAPI_HOST + '/getfile.php'
GET_FILE_URL2 = WEBAPI_HOST + '/get_file_url.php'
TEMP_DIR = 'temp'
DOWNLOAD_DIR = 'download'

DL_ERROR_FILELINKTIMEOUT = '下载链接已超时，请重新从文件夹获取。'
SPLIT_CNT = 4
DOWNLOAD_CNT = 5

g_sem = threading.Semaphore(DOWNLOAD_CNT)

DL_Thread_status = Enum('DL_Thread_status', ('init', 'finished', 'E404'))


def random_ua(id):
    return DYN_UA_FORMAT.format(random.randrange(9999), id + 1)


def requests_debug(r, prefix=''):
    log.debug('{}requests:{}'.format(prefix, r.request.url))
    log.debug('{}response:{}'.format(prefix, r.text))


class CtFile():
    def __init__(self, url, args, filename='', fid=0, parent_dir=DOWNLOAD_DIR, session=None):
        self.url = url
        self.args = args
        self.fid = fid
        self.parent_dir = parent_dir
        self.filename = filename
        self.s = session if session else requests.session()

        self.urlparsed = urlparse(self.url)

    def dl(self):
        log.info('download {}'.format(self.url))

        # step 1
        headers = {
            'origin': self.urlparsed.netloc,
        }

        # parameters
        params = {
            'f': self.url.split('/')[-1],
            'passcode': '',
            'r': str(random.random()),
            'ref': '',
        }
        r = self.s.get(GET_FILE_URL1, params=params, headers=headers)
        j_full = json.loads(r.text)
        j = j_full.get('file')#add a sub dict
        log.debug('step 1')
        requests_debug(r)

        # link error handler
        if j_full.get('code') == 404:
            log.error('dl_file error: {}'.format(j_full.get('message')))
            if j_full.get('message') == DL_ERROR_FILELINKTIMEOUT:
                log.error('need get dir list again')
            return False, j_full.get('message')

        if not self.filename:
            #print(j.get('file').get('file_name'))
            self.filename = j['file_name']

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
        filename = os.path.join(self.parent_dir, self.filename)
        filesize = int(j['file_size'])
        temp_filename = filename + '.ctdown'
        log.debug('create empty file {} size {}'.format(temp_filename, filesize))
        with open(temp_filename, 'wb') as fd:
            fd.truncate(filesize)

        # donwload with thread
        threads = []
        for i in range(self.args.split):
            start = i * filesize // self.args.split
            end = (i + 1) * filesize // self.args.split - 1 if i != self.args.split - 1 else filesize

            t = SplitThread(
                i, j['downurl'].replace(r'\/', r'/'),
                params, headers, filename, start, end)

            log.debug('dl-{:03d} download range start={} end={}'.format(i + 1, start, end))

            threads.append(t)
            t.start()
            # time.sleep(1)

        progressbar = tqdm.tqdm(total=filesize, desc=filename, ascii=' #', unit="B", unit_scale=True, unit_divisor=1024)
        downloaded_bytes = 0
        last_downloaded_bytes = 0
        download_success = True
        while downloaded_bytes < filesize:
            downloaded_bytes = 0
            for t in threads:
                if t._status == DL_Thread_status.E404:
                    log.error('dl-{:03d} download {} Fail'.format(t._index, filename))
                    download_success = False
                    break
                downloaded_bytes += t.downloaded_bytes()

            if not download_success:
                log.error('exit')
                break

            progressbar.update(downloaded_bytes - last_downloaded_bytes)
            last_downloaded_bytes = downloaded_bytes
            log.debug("{} {}".format(downloaded_bytes, filesize))
            time.sleep(1)

        log.debug('quit')
        for i in range(self.args.split):
            threads[i].join()

        os.rename(temp_filename, filename)
        return True, None


class DirThread(threading.Thread):
    def __init__(self, ct_dir):
        super().__init__()
        self.ct_dir = ct_dir
        self.queue = ct_dir.queue
        self.file_threads = []
        self.link_timeout = False
        self.quit = False

    def run(self):
        while not self.quit:
            for t in self.file_threads:
                if not t.is_alive():
                    if t.get_ret():
                        success, error = t.get_ret()
                        if success:
                            # save downloaded status
                            self.ct_dir.status['loc'][t.ct_file.fid] = True
                            self.ct_dir.save_status()
                        else:
                            if error == DL_ERROR_FILELINKTIMEOUT:
                                self.link_timeout = True

                        # remove the thread from array
                        self.file_threads.remove(t)
            time.sleep(1)

    def add(self, thread):
        self.file_threads.append(thread)


class FileThread(threading.Thread):
    def __init__(self, ct_file):
        super().__init__()
        self.ct_file = ct_file
        self.ret = None

    def run(self):
        self.ret = self.ct_file.dl()
        g_sem.release()

    def get_ret(self):
        return self.ret


class SplitThread(threading.Thread):
    def __init__(self, i, url, params, headers, filename, start, end):
        super().__init__()
        self._params = params
        self._index = i
        self._url = url
        self._headers = headers
        self._filename = filename
        self._start = start
        self._end = end
        self._UA = random_ua(self._index)
        self._s = requests.session()
        self._downloaded_bytes = 0
        self._status = DL_Thread_status.init

        self._headers['user-agent'] = self._UA

    def run(self):
        while True:
            while True:
                self._params['rd'] = str(random.random())
                r = self._s.get(GET_FILE_URL2, params=self._params, headers=self._headers)
                j = json.loads(r.text)
                log.debug('dl-{:03d} step 2'.format(self._index + 1))
                requests_debug(r, 'dl-{:03d} '.format(self._index + 1))
                if j['code'] == 503 and j['message'] == 'require for verifycode':
                    log.debug('dl-{:03d} retry'.format(self._index + 1))
                    self._UA = random_ua(self._index)
                    self._headers['user-agent'] = self._UA
                else:
                    break

            with open(self._filename + '.ctdown', 'r+b') as f:
                f.seek(self._start)
                self._headers['Range'] = 'bytes={}-{}'.format(self._start, self._end)
                r = self._s.get(j['downurl'].replace(r'\/', r'/'), headers=self._headers, stream=True)
                log.debug('dl-{:03d} download file request: {}'.format(self._index + 1, r.status_code))
                if r.status_code == 503:
                    log.warning('dl-{:03d} download fail, retry'.format(self._index + 1))
                    self._headers['user-agent'] = random_ua(self._index)
                    time.sleep(1)
                elif r.status_code == 404:
                    log.error('dl-{:03d} download fail, 404'.format(self._index + 1))
                    self._status = DL_Thread_status.E404
                    break
                else:
                    for chunk in r.iter_content(chunk_size=128):
                        f.write(chunk)
                        self._downloaded_bytes += len(chunk)
                    log.debug('dl-{:03d} exit'.format(self._index + 1))
                    break

    def downloaded_bytes(self):
        return self._downloaded_bytes


class CtDir():
    def __init__(self, args, parent_dir=DOWNLOAD_DIR, subdir=''):
        self.args = args
        self.parent_dir = parent_dir

        # create a sub folder url
        if subdir:
            self.url = '{}?{}'.format(args.dir, subdir)
        else:
            self.url = args.dir
        self.urlparsed = urlparse(self.url)
        self.url_host = self.urlparsed.netloc

        # remove the last '/' if exist
        path_split = self.urlparsed.path.split('/')
        self.url_id = path_split[-1] if path_split[-1] else path_split[-2]
        if subdir:
            self.status_file = os.path.join(TEMP_DIR, self.url_id + '.sub-{}'.format(subdir))
        else:
            self.status_file = os.path.join(TEMP_DIR, self.url_id)

        # session
        self.s = requests.session()
        self.s.headers = {'user-agent': UA}

        # init self.status
        if self.status_exist():
            log.debug('read dir status')
            self.load_status()
        else:
            log.debug('init dir status')
            self.init_status()

        # queue between FileThread to DirThread
        self.queue = queue.Queue()

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
            # parameters
            params = {
                'd': self.urlparsed.path.split('/')[-1],
                'folder_id': self.urlparsed.query,
                'passcode': '',
                'r': str(random.random()),
                'ref': '',
            }
            r = self.s.get(GET_DIR_URL, params=params, headers=headers)
            j = json.loads(r.text.encode().decode('utf-8-sig'))
            requests_debug(r)
            if j.get('code') == 404 or j.get('code') == 503:
                log.error('dl_dir_list error: {}, {}'.format(self.url, j.get('message')))

            loc['name'] = j['folder_name']
            loc['url'] = j['url']  # real url
            log.info('folder name: {}'.format(loc['name']))
            log.info('folder url: {}'.format(loc['url']))

            r = self.s.get(WEBAPI_HOST + loc['url'])
            self.status['web'] = json.loads(r.text)

            self.save_status()

    def dl_dir(self):
        loc = self.status['loc']
        web = self.status['web']

        # mkdir
        dir_fullname = os.path.join(self.parent_dir, loc['name'])
        log.info(dir_fullname)
        if not os.path.exists(dir_fullname):
            os.mkdir(dir_fullname)

        # start a thread to watch the download process
        dir_thread = DirThread(self)
        dir_thread.start()

        for i in web['aaData']:
            p = pq(i[0])
            value = p('input').attr('value')
            dl_type = p('input').attr('name')
            p = pq(i[1])
            name = p('a').text()
            if 'file' in dl_type:
                url = p('a').attr('href')
                if loc.get(value) and loc[value]:
                    log.info('file {} {} downloaded, skip it'.format(value, name))
                else:
                    lst = list(self.urlparsed)
                    lst[3] = lst[4] = lst[5] = ""
                    lst[2] = url
                    ct_file = CtFile(urlunparse(lst), self.args, name, value, dir_fullname, self.s)
                    file_thread = FileThread(ct_file)

                    # get the semaphare before create thread
                    r = False
                    while not r:
                        r = g_sem.acquire(timeout=1)
                        if dir_thread.link_timeout:
                            dir_thread.quit = True
                            return False, DL_ERROR_FILELINKTIMEOUT

                    file_thread.start()
                    dir_thread.add(file_thread)

            elif 'folder' in dl_type:
                log.debug('{} {} is a folder'.format(value, name))
                ct_subdir = CtDir(self.args, dir_fullname, value)
                ct_subdir.get_dir_list(True)
                success, error = ct_subdir.dl_dir()
                if not success:
                    dir_thread.quit = True
                    return success, error

        dir_thread.quit = True
        return True, None


def main():
    global g_sem
    parser = argparse.ArgumentParser(description='Download from CTDisk.')
    parser.add_argument('-d', '--dir', help='download a directory')
    parser.add_argument('-f', '--file', help='download a file')
    parser.add_argument('-s', '--split', type=int, default=SPLIT_CNT, help='split a files to parts')
    parser.add_argument('-c', '--dl_cnt', type=int, default=DOWNLOAD_CNT, help='download files concorrent')
    args = parser.parse_args()

    g_sem = threading.Semaphore(args.dl_cnt)

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
        ct_file = CtFile(args.file, args)
        ct_file.dl()
        log.info('download finished')


if __name__ == "__main__":
    main()
